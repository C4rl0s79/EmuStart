"""api — EmuStart na PC: metody wywoływane z UI (pywebview js_api).

Biblioteka gier, grafiki i opisy pochodzą z EmuStart Server (kopia biblioteki —
emustart/libsync.py); tu: granie, profile, emulatory, pady, logowanie do serwera.
Skan folderów, pobieranie grafik i opisów robi serwer (emustart/srvapi.py).

Wszystkie zwracają zwykłe dicty/listy (JSON). Długie operacje (odświeżenie biblioteki,
start gry, pobieranie przypiętych) działają w wątkach; UI odpytuje ich stan.
"""

from __future__ import annotations

import collections
import json
import logging
import math
import os
import re
import socket
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from emustart import (__version__, art, bios, cache, catalog, ingame, installer, logos, sysinfo, metadata, pads, profiles, translations, uipad, config, emulators, launcher, library,
                      nasfs, netsrc, paths, saveslots, syncclient, systems, winutil)

log = logging.getLogger("emustart.api")
KEEP_LIST = 60                     # stanów na liście „Wczytaj zapis”
PIN_RETRY = 300                 # s — wznowienie pobierania przypiętej gry najwyżej tak często

class Api:
    def __init__(self):
        self._cfg = config.load()
        self._window = None
        self._fetcher = art.Fetcher(logos=lambda: bool(self._cfg.get("games_logo")))
        self._session: launcher.Session | None = None
        self._background: list = []          # sesje, które jeszcze kopiują w tle
        self._pin_jobs: dict = {}            # game_id → Session-like (kopiowanie przypiętych)
        self._amiga_asked = False
        try:                              # WinFsp: granie w trakcie pobierania (emustart/vfs.py)
            from emustart import vfs
            self._winfsp = vfs.status_text()
        except Exception as ex:
            self._winfsp = f"błąd: {ex}"
        log.info("WinFsp: %s", self._winfsp)
        self._scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}
        ingame.restore_pending()          # ustawienia padów po ewentualnej awarii
        profiles.adopt_existing_install()  # aktualizacja: komputer już ma swój profil
        self._uipad = uipad.UiPad(self._uipad_active) if self.pad_backend() == "python" else None
        self._profile = self._initial_profile()
        # lokalne save'y i ustawienia profili → NAS/serwer (zaległe po grze bez połączenia,
        # pierwsze przejście na serwer); gra uruchomiona w tym czasie poczeka (post_busy)
        launcher.start_post(lambda: profiles.push_all(self._cfg), "zapisy profili")
        # zapisy gier niewysłane na serwer (gra bez połączenia) — paczki, syncclient; potem
        # nowsze wersje z innych urządzeń (lokalnie bez zmian; konflikty — przy uruchomieniu gry)
        def saves_at_start():
            try:
                syncclient.retry(self._cfg)
                syncclient.pull_all(self._cfg, self._profile)
            except Exception:
                log.exception("zapisy gier przy starcie")
        launcher.start_post(saves_at_start, "zapisy gier")
        threading.Thread(target=self._profiles_bootstrap, daemon=True, name="profiles").start()
        from emustart import mediastore
        mediastore.migrate_later()                # lokalna kopia grafik: jeden plik na system
        from emustart import libsync
        self._lib_version = 0                  # rośnie, gdy biblioteka z serwera się zmieniła (UI przerysowuje)
        if libsync.thin(self._cfg):            # biblioteka z serwera przy starcie i co 5 minut (zmienione systemy)
            threading.Thread(target=self._thin_loop, daemon=True, name="biblioteka-serwera").start()
        threading.Thread(target=self._platforms_meta, daemon=True, name="platforms").start()
        # Menu w grze: stan czyta UI (ingame_poll), Python niczego nie wywołuje
        # w oknie. evaluate_js przy grze na pełnym ekranie potrafiło czekać 20 s
        # i blokowało w tym czasie wszystkie wywołania z UI.
        self._menu = {"open": False, "items": [], "message": "", "title": "", "seq": 0}
        self._menu_inputs: collections.deque = collections.deque()
        self._menu_lock = threading.Lock()
        self._slot_lock = threading.Lock()        # zmiany slotów / stanów jedna po drugiej

    def _uipad_active(self) -> bool:
        if not self._window:
            return False                  # tryb --browser: pady obsługuje przeglądarka
        s = self._session
        if s and s.phase == "running":
            return False                  # w grze pad czyta menu w grze (hotkey)
        hwnd = self._hwnd()
        fg = winutil.foreground()
        active = bool(hwnd) and fg == hwnd
        if not active and getattr(self, "_fg_was_ours", False) and fg:
            # fokus uciekł z okna EmuStart bez uruchomionej gry — zapisz dokąd
            log.warning("fokus przejęło okno: %s", winutil.window_info(fg))
            # Strażnik: Windows 11 potrafi przy nawigacji padem przenieść fokus
            # na pasek zadań. Jeśli stało się to tuż po naciśnięciu pada,
            # odzyskujemy fokus (Alt+Tab do innych programów nie ruszamy).
            pad_recent = time.monotonic() - getattr(self._uipad, "last_event", 0) < 2.0
            if pad_recent and winutil.window_class(fg) in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
                winutil.bring_to_front(hwnd)
                log.info("strażnik fokusu: odzyskano fokus z paska zadań")
                active = winutil.foreground() == hwnd
        self._fg_was_ours = active
        return active

    def ui_log(self, msg: str) -> None:
        """Wpis do logu z interfejsu (diagnostyka)."""
        log.info("UI: %s", str(msg)[:300])

    def ui_pad_poll(self) -> list:
        """Zdarzenia padów XInput dla UI: [{a, up}]."""
        return self._uipad.poll() if self._uipad else []

    def pad_backend(self) -> str:
        """Kto obsługuje pady w interfejsie: python (XInput) | browser (Gamepad API
        w WebView2) | none. Flaga startowa --pady=… wygrywa z ustawieniem."""
        import sys
        if getattr(self, "browser_mode", False):
            return "browser"              # interfejs w przeglądarce: pady przez Gamepad API
        for arg in sys.argv:
            if arg.startswith("--pady="):
                v = arg.split("=", 1)[1].lower()
                return {"python": "python", "przegladarka": "browser", "browser": "browser",
                        "brak": "none", "none": "none"}.get(v, "python")
        v = self._cfg.get("pad_backend", "python")
        return v if v in ("python", "browser", "none") else "python"

    def _platforms_meta(self) -> None:
        """Opisy platform dla karuzeli."""
        try:
            sysinfo.ensure([s["es"] for s in library.systems_summary()])
        except Exception:
            log.exception("platformy")

    def _initial_profile(self) -> int:
        ids = [p["id"] for p in profiles.all_profiles()]
        if not profiles.setup_needed() and not profiles.ask_at_start():
            return profiles.machine_owner()   # bez pytania: profil tego komputera
        try:
            last = int(library.meta_get("last_profile", "0"))
        except ValueError:
            last = 0
        return last if last in ids else ids[0]

    def attach(self, window) -> None:
        self._window = window
        try:
            window.events.shown += self._hook_webview_focus
        except Exception:
            log.exception("fokus okna")

    def _hook_webview_focus(self, *_a) -> None:
        """pywebview daje fokus kontrolce WebView2 tylko przy pierwszym pokazaniu okna.
        Gdy okno wraca na wierzch (po grze, menu w grze, Alt+Tab — SetForegroundWindow
        na formularzu), fokus zostawał na formularzu: pad działał (Python), klawiatura
        i kółko myszy nie. Każda aktywacja okna oddaje fokus stronie."""
        if getattr(self, "_focus_hooked", False):      # „shown” bywa wołane ponownie
            return
        form = getattr(self._window, "native", None)
        # pole wewnętrzne pywebview (WinForms, 6.x): BrowserForm.browser.webview / .webview
        webview = getattr(getattr(form, "browser", None), "webview", None) or getattr(form, "webview", None)
        if form is None or webview is None or not hasattr(webview, "Focus"):
            log.warning("fokus WebView2: brak kontrolki w oknie pywebview (inna wersja/renderer) — "
                        "klawiatura po powrocie z gry może wymagać kliknięcia w okno")
            return

        def on_activated(*_x):
            try:
                webview.Focus()
            except Exception:
                log.exception("fokus WebView2")

        try:
            form.Activated += on_activated
            self._focus_hooked = True
            log.info("fokus WebView2 przy aktywacji okna: włączony")
        except Exception:
            log.exception("fokus okna")

    # ── profile: rzeczy per gracz (EmuStart, RetroAchievements) ──
    USER_KEYS = ("look", "games_logo", "hide_arcade_clones",
                 "hide_beta", "hide_demo", "hide_pirate", "hide_unl", "hide_program")

    def _profiles_bootstrap(self) -> None:
        """W tle przy starcie: profile z NAS (czysta instalacja), konto RA
        zalogowane w emulatorach → profil tego komputera, ustawienia EmuStart profilu."""
        try:
            launcher.cleanup_run_dirs()
            if library.meta_get("demos_reset", "") != "1":
                art.reset_system("amigawhddemos")
                library.meta_set("demos_reset", "1")
            if library.meta_get("whd_games_retry", "") != "1":
                art.retry_missing("amigawhdgames")
                library.meta_set("whd_games_retry", "1")
            if library.meta_get("secrets_scrubbed", "") != "1" and profiles.nas_online(self._cfg):
                profiles.scrub_settings_copies(self._cfg)
                library.meta_set("secrets_scrubbed", "1")
            profiles.import_from_nas(self._cfg)
            acc = profiles.account_of_device(self._cfg)
            if acc and (profiles.setup_needed() or (profiles.get(self._profile) or {}).get("nas_name", "").lower() != acc.lower()):
                self._adopt_account(acc)      # zatwierdzenie bez połączenia — dokończenie przy starcie
            self._ra_adopt()
            self._user_load(self._profile)
        except Exception:
            log.exception("profile przy starcie")

    def _ra_adopt(self) -> None:
        """Konto RA zalogowane w emulatorach należy do właściciela komputera —
        dopiero gdy wiadomo, kto nim jest (po pierwszym uruchomieniu)."""
        if profiles.setup_needed():
            return
        owner = profiles.machine_owner()
        if profiles.ra_get(self._cfg, owner) is None:
            for fam, acc in self._ra_found().items():
                profiles.ra_set(self._cfg, owner, {**acc, "hardcore": False})
                log.info("RetroAchievements: konto %s z %s → profil komputera", acc["user"], fam)
                break

    def profile_setup(self, pid: int, ask: bool) -> dict:
        """Pierwsze uruchomienie na tym komputerze: kto tu gra."""
        if not profiles.get(int(pid)):
            return {"ok": False, "reason": "Nie ma takiego profilu."}
        block = self._pin_block(pid)
        if block:
            return block
        profiles.finish_setup(int(pid), bool(ask))
        self.profile_select(int(pid))
        threading.Thread(target=self._ra_adopt, daemon=True).start()
        return {"ok": True}

    def _look_shared(self) -> bool:
        return self._cfg.get("look_scope", "profile") != "machine"

    def _user_load(self, pid: int) -> None:
        """Ustawienia EmuStart profilu (wygląd itp.); profil bez nich dziedziczy bieżące."""
        # emulatory i rdzenie wybrane na innych komputerach tego konta (w tle: serwer, pobieranie)
        from emustart import emuchoice
        self._choices = threading.Thread(target=emuchoice.apply_and_fetch, args=(self._cfg, pid), daemon=True,
                                         name="wybory-emulatorow")
        self._choices.start()
        data = profiles.json_get(self._cfg, pid, "emustart.json")
        if not data:
            return
        changed = False
        for k in self.USER_KEYS:
            if k == "look" and not self._look_shared():
                continue                      # wygląd tego komputera
            if k not in data and k.startswith("hide_"):
                # profil zapisany przed wersją z ukrywaniem: domyślnie widać wszystko
                data[k] = k == "hide_arcade_clones"
            if k in data and self._cfg.get(k) != data[k]:
                self._cfg[k] = clean_look(data[k]) if k == "look" else bool(data[k])
                changed = True
        if changed:
            config.save(self._cfg)

    def _user_save(self) -> None:
        try:
            data = profiles.json_get(self._cfg, self._profile, "emustart.json") or {}
            for k in self.USER_KEYS:
                if k in self._cfg and (k != "look" or self._look_shared()):
                    data[k] = self._cfg[k]
            profiles.json_set(self._cfg, self._profile, "emustart.json", data)
        except Exception:
            log.exception("zapis ustawień profilu")

    def _ra_found(self) -> dict:
        """Konta RA zalogowane w emulatorach z konfiguracji: {rodzina: {user, token}}."""
        out = {}
        for sc in (self._cfg.get("systems") or {}).values():
            exe = sc.get("exe") or ""
            if not exe or not Path(exe).is_file():
                continue
            ad = ingame.adapter_for(exe)
            if ad.family in out:
                continue
            try:
                acc = ad.read_cheevos()
            except Exception:
                acc = {}
            if acc:
                out[ad.family] = acc
        return out

    def ra_status(self, pid: int) -> dict:
        rec = profiles.ra_get(self._cfg, int(pid)) or {}
        found = [{"family": f, "user": a["user"]} for f, a in self._ra_found().items()]
        return {"user": rec.get("user", ""), "hardcore": bool(rec.get("hardcore")), "found": found}

    def ra_login(self, pid: int, user: str, password: str) -> dict:
        """Logowanie do RetroAchievements: hasło idzie tylko do retroachievements.org
        (POST), zapisujemy nazwę i token — jak robią to same emulatory."""
        import urllib.parse
        import urllib.request
        user = (user or "").strip()
        if not user or not password:
            return {"ok": False, "reason": "Podaj nazwę użytkownika i hasło."}
        body = urllib.parse.urlencode({"r": "login2", "u": user, "p": password}).encode()
        req = urllib.request.Request("https://retroachievements.org/dorequest.php", data=body,
                                     headers={"User-Agent": f"EmuStart/{__version__} (+https://github.com/C4rl0s79/EmuStart)",
                                              "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as ex:
            try:
                data = json.loads(ex.read().decode("utf-8", "replace"))
            except ValueError:
                return {"ok": False, "reason": f"Serwer RetroAchievements: błąd {ex.code}."}
        except (OSError, ValueError) as ex:
            return {"ok": False, "reason": f"Brak połączenia z RetroAchievements: {ex}"}
        if not data.get("Success") or not data.get("Token"):
            return {"ok": False, "reason": data.get("Error") or "Nieprawidłowa nazwa lub hasło."}
        rec = profiles.ra_get(self._cfg, int(pid)) or {}
        profiles.ra_set(self._cfg, int(pid), {"user": data.get("User") or user, "token": data["Token"],
                                              "hardcore": rec.get("hardcore", False)})
        log.info("RetroAchievements: zalogowano %s (profil %s)", data.get("User") or user, pid)
        return {"ok": True, "user": data.get("User") or user}

    def ra_import(self, pid: int, family: str) -> dict:
        acc = self._ra_found().get(family)
        if not acc:
            return {"ok": False, "reason": "W tym emulatorze nie ma zalogowanego konta."}
        rec = profiles.ra_get(self._cfg, int(pid)) or {}
        profiles.ra_set(self._cfg, int(pid), {**acc, "hardcore": rec.get("hardcore", False)})
        return {"ok": True, "user": acc["user"]}

    def ra_logout(self, pid: int) -> dict:
        profiles.ra_set(self._cfg, int(pid), {"user": "", "token": ""})
        return {"ok": True}

    def ra_hardcore(self, pid: int, on: bool) -> dict:
        rec = profiles.ra_get(self._cfg, int(pid)) or {}
        if not rec.get("user"):
            return {"ok": False, "reason": "Najpierw zaloguj profil do RetroAchievements."}
        profiles.ra_set(self._cfg, int(pid), {**rec, "hardcore": bool(on)})
        return {"ok": True}

    def _server_state(self) -> dict | None:
        """Serwer EmuStart (pasek u góry): nazwa i czy odpowiada; None = nie ustawiony."""
        if not (self._cfg.get("server_url") and self._cfg.get("server_key")):
            return None
        i = netsrc.info_nowait(self._cfg)           # bez czekania na sieć (start, pasek co 15 s)
        if i is None:
            return {"ok": False, "checking": True, "name": "", "account": self._cfg.get("server_account", ""),
                    "key_rejected": False}
        rejected = bool(i.get("key_rejected")) or bool(netsrc.rejected())
        out = {"ok": bool(i.get("ok")), "name": i.get("name", ""), "account": self._cfg.get("server_account", ""),
               "key_rejected": rejected, "reason": netsrc.rejected() or i.get("reason", "")}
        if rejected:
            # urządzenie unieważnione na serwerze — ile gier ma zapisy, które czekają na wysłanie
            try:
                out["pending_saves"] = len(syncclient.pending(self._cfg))
            except Exception:
                out["pending_saves"] = 0
        return out

    # ── stan ogólny ──
    def get_state(self) -> dict:
        cfg = self._cfg
        if not getattr(self, "_account_aligned", False):     # raz na uruchomienie (pasek u góry)
            self._account_aligned = True
            try:
                profiles.align_account_name(cfg)
            except Exception:
                log.exception("nazwa profilu konta")
        return {
            "version": __version__,
            "configured": config.is_configured(cfg),
            "server": self._server_state(),
            "lib_version": getattr(self, "_lib_version", 0),
            "admin": self._is_admin() if self._cfg.get("server_key") else False,
            "account": profiles.account_of_device(cfg),
            "systems": self.list_systems(),
            "scan": dict(self._scan),
            "cache": cache.usage(cfg),
            "copying": self._copying(),
            "py_pad": bool(self._window) and uipad.xinput.available() and self.pad_backend() == "python",
            "pad_backend": self.pad_backend() if self._window else "browser",
            "games_logo": bool(self._cfg.get("games_logo")),
            "look": self._cfg.get("look", {}),
            "look_scope": self._cfg.get("look_scope", "profile"),
            "setup_needed": profiles.setup_needed(),
            "ask_profile": profiles.ask_at_start(),
            "conflicts": profiles.pop_conflicts(),
            "profile": profiles.get(self._profile),
            "profiles": len(profiles.all_profiles()),
        }

    def system_info(self, es: str) -> dict:
        """Opis platformy do karuzeli (producent, lata, nośnik, opis). Brak danych
        = pobranie w tle; UI zapyta ponownie."""
        d = sysinfo.get(es)
        if d is None:
            sysinfo.ensure_now(es)
            return {"pending": True}
        return {k: v for k, v in d.items() if not k.startswith("_") and k != "lb_notes"}

    HIDE_KEYS = catalog.HIDE_KEYS

    def list_systems(self) -> list:
        """Systemy z biblioteki serwera; logo systemu takie jak na serwerze."""
        from emustart import libsync
        summary = library.systems_summary()
        counts = catalog.visible_counts(self._cfg, summary)
        out = []
        for s in summary:
            if counts is not None:
                s["games"] = counts.get(s["es"], 0)
            emu = (self._cfg.get("systems") or {}).get(s["es"]) or {}
            if not emu.get("enabled", True) or not s["games"]:
                continue
            lg = libsync.logo_for(s["es"]) or logos.url_for(s["es"], self._cfg)
            out.append({"es": s["es"], "display": emu.get("name") or s["display"], "games": s["games"],
                        "cached": s["cached"], "logo": lg["url"], "logo_glow": lg["glow"],
                        "emulator": emu.get("label", ""), "kind": systems.info(s["es"])["kind"]})
        return out

    def list_games(self, es: str) -> list:
        return catalog.list_games(self._cfg, es, self._profile)

    def request_art(self, game_ids: list, logo: bool = False) -> None:
        if logo and game_ids:
            self._fetcher.request_logo(int(game_ids[0]))
        self._fetcher.request([int(g) for g in game_ids])

    def art_for(self, game_ids: list) -> dict:
        """Aktualne adresy grafik dla podanych gier (po dociągnięciu w tle)."""
        out = {}
        con = library.db()
        for gid in game_ids:
            r = con.execute("SELECT es,name,art_box,art_snap,art_logo FROM games WHERE id=?",
                            (int(gid),)).fetchone()
            if r:
                out[str(gid)] = {
                    "box": art.media_url(r["es"], r["name"], "box") if r["art_box"] == art.HAS else "",
                    "snap": art.media_url(r["es"], r["name"], "snap") if r["art_snap"] == art.HAS else "",
                    "logo": art.media_url(r["es"], r["name"], "logo") if r["art_logo"] == art.HAS else "",
                    "checked": bool(r["art_box"] and r["art_snap"])}
        return out

    # ── aktualizacja EmuStart (menu Start; emustart/updater.py) ──
    def app_update_check(self) -> dict:
        from emustart import updater
        try:
            info = updater.latest()
        except Exception as ex:
            log.warning("sprawdzenie aktualizacji: %s", ex)
            return {"ok": False, "reason": f"Nie udało się sprawdzić wydań na GitHubie: {ex}"}
        self._app_update = {"info": info, "job": None}
        frozen = bool(getattr(sys, "frozen", False))
        return {"ok": True, "version": info["version"], "current": info["current"], "newer": info["newer"],
                "size": info["size"], "available": bool(info["url"]) and frozen and os.name == "nt",
                "reason": "" if frozen else "Aktualizacja z menu działa w EmuStart.exe."}

    def app_update_start(self) -> dict:
        from emustart import updater
        st = getattr(self, "_app_update", None) or {}
        info = st.get("info")
        if not info or not info.get("newer") or not info.get("url"):
            return {"ok": False, "reason": "Najpierw sprawdź aktualizacje."}
        if st.get("job") and st["job"].phase in ("downloading", "extracting"):
            return {"ok": True, **st["job"].status()}
        job = updater.Job(info)
        st["job"] = job
        job.start()
        return {"ok": True, **job.status()}

    def app_update_status(self) -> dict:
        job = (getattr(self, "_app_update", None) or {}).get("job")
        return {"ok": True, **job.status()} if job else {"ok": True, "phase": "idle"}

    def app_update_apply(self) -> dict:
        """Podmiana plików po zamknięciu EmuStart i ponowne uruchomienie z tymi samymi parametrami."""
        job = (getattr(self, "_app_update", None) or {}).get("job")
        if not job:
            return {"ok": False, "reason": "Brak pobranej aktualizacji."}
        if self._session and self._session.phase in ("preparing", "downloading", "extracting", "running"):
            return {"ok": False, "reason": "Najpierw zamknij grę."}
        r = job.apply(sys.argv[1:], server=False)
        if not r.get("ok"):
            return r
        log.info("koniec EmuStart — aktualizacja do %s", job.info.get("version"))

        def bye():
            time.sleep(0.8)
            try:
                self._shutdown()
            finally:
                logging.shutdown()
                os._exit(0)
        threading.Thread(target=bye, daemon=True, name="update-exit").start()
        return {"ok": True, "version": job.info.get("version")}

    # ── tłumaczenia: wersje gry (emustart/translations.py) ──
    def _variants(self, g: dict) -> list:
        return catalog.variants(g)

    @staticmethod
    def _variant_key(g: dict) -> str:
        return g["es"] + "/" + g["rel"].replace("\\", "/")

    def _variant_choice(self, g: dict, variants: list | None = None) -> dict | None:
        """Wersja wybrana w profilu gracza (None = oryginał)."""
        variants = self._variants(g) if variants is None else variants
        if not variants:
            return None
        want = (profiles.json_get(self._cfg, self._profile, translations.FILE) or {}).get(self._variant_key(g))
        return next((v for v in variants if v["rel"] == want), None)

    def set_variant(self, game_id: int, variant_id: int = 0) -> dict:
        """Wersja gry dla profilu gracza: id tłumaczenia albo 0 = oryginał."""
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}
        data = dict(profiles.json_get(self._cfg, self._profile, translations.FILE) or {})
        v = next((x for x in self._variants(g) if x["id"] == int(variant_id or 0)), None)
        if variant_id and not v:
            return {"ok": False, "reason": "Nie ma takiej wersji gry."}
        if v:
            data[self._variant_key(g)] = v["rel"]
        else:
            data.pop(self._variant_key(g), None)
        profiles.json_set(self._cfg, self._profile, translations.FILE, data)
        return {"ok": True, "variant": v["id"] if v else 0, "label": v["label"] if v else "Oryginał"}

    def game_detail(self, game_id: int, use_variant: bool = True) -> dict:
        g = library.game(int(game_id))
        if not g:
            return {}
        info = systems.info(g["es"])
        variants = self._variants(g)
        chosen = self._variant_choice(g, variants) if use_variant else None
        # stan „lokalnie” i czas gry — wybranej wersji (tę się pobiera i uruchamia)
        pg = (library.game(chosen["id"]) or g) if chosen else g
        row = library.db().execute(
            "SELECT * FROM cache WHERE game_id=?", (pg["id"],)).fetchone()
        play = library.db().execute(
            "SELECT * FROM play WHERE game_id=? AND profile_id=?",
            (pg["id"], self._profile)).fetchone()
        cached = self._sync_cached(pg, row)
        meta = catalog.game_meta(self._cfg, g)
        return {"id": g["id"], "title": meta.get("title") or g["title"], "tags": g["tags"],
                "name": g["name"], "meta": meta,
                "meta_online": bool(metadata.get(g["id"]).get("_online")),
                "meta_edits": metadata.get(g["id"]).get("_edits", {}),
                "system": info["display"], "size": pg["size"], "files": len(pg["files"]),
                "file": pg["rel"], "cached": cached,
                "local": pg["size"] if cached else max(0, pg["size"] - cache.missing_bytes(self._cfg, pg)),
                "pinned": bool(row and row["pinned"]),
                "plays": play["plays"] if play else 0,
                "seconds": play["seconds"] if play else 0,
                "last": play["last"] if play else 0,
                "copying": pg["id"] in self._copying(),
                "es": g["es"], "rel": g["rel"].replace("\\", "/"),
                "variants": variants, "variant": chosen["id"] if chosen else 0,
                "variant_label": chosen["label"] if chosen else ""}

    def _sync_cached(self, g: dict, row) -> bool:
        """Stan „lokalnie” z plików w cache, nie tylko z bazy: plik dokończony później
        (był w użyciu) albo usunięty ręcznie. Przypięta, niepełna gra bez kopiowania
        w toku — pobieranie wznawia się samo."""
        if not row:
            return False
        ok = cache.is_complete(self._cfg, g)
        if ok != bool(row["complete"]):
            with library.db() as c:
                c.execute("UPDATE cache SET complete=?, size=? WHERE game_id=?",
                          (int(ok), g["size"] if ok else 0, g["id"]))
        if row["pinned"] and not ok and g["id"] not in self._copying() \
                and not (self._session and self._session.game_id == g["id"]):
            self._start_pin_copy(g, auto=True)
        return ok

    # ── uruchamianie ──
    def launch(self, game_id: int, state: str = "", takeover: bool = False) -> dict:
        if self._session and self._session.phase in ("preparing", "downloading",
                                                     "extracting", "running"):
            return {"ok": False, "reason": "Inna gra jest właśnie uruchamiana."}
        t0 = time.monotonic()
        ch = getattr(self, "_choices", None)
        if ch and ch.is_alive():
            ch.join(15)                       # wybory emulatorów z konta (zaraz po zalogowaniu)
        block = self._pin_block(self._profile)
        if block:
            return {**block, "profile_pin": True}
        t_pin = time.monotonic() - t0
        g = library.game(int(game_id))
        if g:
            emu = self._effective_emu(g)
            if not emu["exe"] or not Path(emu["exe"]).is_file():
                steps = installer.plan_for(g["es"], self._cfg.get("emu_root", ""))
                info = systems.info(g["es"])
                if not steps:
                    return {"ok": False, "reason": f"Brak emulatora dla {info['display']} "
                            "i nie umiem go pobrać. Wybierz go w ustawieniach."}
                return {"ok": False, "need_install": {
                    "es": g["es"], "system": info["display"],
                    "steps": installer.describe(steps)}}
        warning = ""
        if g and ingame.adapter_for(emu["exe"]).family == "retroarch":
            # BIOS-y rdzenia z folderu BIOS-ów (Kickstarty, capsimg, BIOS-y PS1…)
            amiga = systems.info(g["es"])["plat"] == "AMIGA"
            try:
                bios.sync(self._cfg, Path(emu["exe"]).parent, bios.core_name(emu.get("args", "")),
                          ("capsimg.dll",) if amiga else ())
            except Exception:
                log.exception("BIOS-y")
        if g and systems.info(g["es"])["plat"] == "AMIGA" and ingame.adapter_for(emu["exe"]).family == "retroarch":
            root = self._cfg.get("emu_root", "")
            extra = installer.amiga_extras(root)
            if extra and g["rel"].lower().endswith((".ipf", ".zip")) and not self._amiga_asked:
                self._amiga_asked = True      # raz na uruchomienie programu
                return {"ok": False, "need_install": {
                    "es": g["es"], "system": "Amiga — obrazy .ipf",
                    "steps": installer.describe(extra)}}
            if installer.amiga_kickstart_missing(root):
                warning = ("Brak ROM-ów Kickstart (np. kick34005.A500, kick40068.A1200) w folderze system "
                           "RetroArcha" + ("" if self._cfg.get("bios_dir") else " — wskaż Folder z BIOS-ami w Ustawieniach")
                           + ". Rdzeń użyje zamiennika AROS, część gier może nie działać.")
        bg = next((s for s in self._background if s.game_id == int(game_id)), None)
        if bg:   # kopia tej gry trwa w tle — przejmujemy ją (wznowienie z .part)
            bg.cancel.set()
            bg.copy_thread.join(5)
        job = self._pin_jobs.pop(int(game_id), None)
        if job:  # to samo dla kopiowania przypiętej gry
            job["cancel"].set()
            job["thread"].join(5)
        self._session = launcher.Session(self._cfg, int(game_id), self._profile,
                                         on_running=self._on_running,
                                         on_finished=self._on_finished,
                                         ui=self if self._window else None,
                                         start_state=state or "", takeover=bool(takeover))
        self._session.start()
        pre = time.monotonic() - t0
        if pre > 0.5:                        # przed oknem postępu — co tu trwa długo
            log.info("przed startem sesji: %.1f s (konto profilu %.1f s)", pre, t_pin)
        return {"ok": True, "warning": warning}

    def configure_emulator(self, game_id: int) -> dict:
        """Emulator tej gry bez gry — zmiana ustawień w profilu gracza (obraz, dźwięk,
        sterowanie). Po zamknięciu emulatora ustawienia wracają do profilu i na NAS."""
        if self._session and self._session.phase in ("preparing", "downloading",
                                                     "extracting", "running"):
            return {"ok": False, "reason": "Gra jest właśnie uruchomiona."}
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}
        emu = self._effective_emu(g)
        if not emu["exe"] or not Path(emu["exe"]).is_file():
            return {"ok": False, "reason": "Ta gra nie ma jeszcze emulatora — uruchom ją raz albo wybierz emulator."}
        self._session = launcher.Session(self._cfg, int(game_id), self._profile,
                                         on_running=self._on_running, on_finished=self._on_finished,
                                         ui=None, configure=True)
        self._session.start()
        return {"ok": True, "label": emu["label"] or Path(emu["exe"]).stem}

    def launch_status(self) -> dict:
        return self._session.status() if self._session else {"phase": "idle"}

    def launch_cancel(self) -> None:
        if self._session:
            self._session.abort()

    def launch_choice(self, choice: str) -> bool:
        """Konflikt zapisów przy uruchamianiu: local | remote | skip."""
        return bool(self._session and self._session.choose(str(choice)))

    def launch_dismiss(self) -> None:
        s = self._session
        if s and s.phase in ("finished", "error", "cancelled"):
            self._session = None

    def _on_running(self, s) -> None:
        """Okno EmuStart zostaje pod spodem; pilnujemy tylko, żeby emulator
        dostał fokus, gdy pokaże swoje okno."""
        def focus():
            # RetroArch (i inne) najpierw pokazuje okno, a przy ładowaniu rdzenia
            # tworzy je od nowa na pełnym ekranie — wtedy fokus wracał do EmuStart,
            # a pełnoekranowe okno bez fokusu Windows minimalizuje. Pilnujemy więc
            # okna emulatora, aż przez 3 s utrzyma się na wierzchu (najwyżej 25 s).
            end = time.monotonic() + 25
            stable_since, last_push = None, 0.0
            while time.monotonic() < end and s.proc and s.proc.poll() is None:
                if self._menu.get("open"):
                    return                    # menu w grze — fokus należy do EmuStart
                wins = winutil.windows_of(s.proc.pid)
                if wins:
                    fg = winutil.foreground()
                    ok = fg in wins and not winutil.is_iconic(fg)
                    now = time.monotonic()
                    if ok:
                        stable_since = stable_since or now
                        if now - stable_since >= 3:
                            return
                    else:
                        stable_since = None
                        if now - last_push >= 0.8:
                            winutil.bring_to_front(wins[0])
                            last_push = now
                time.sleep(0.25)
        threading.Thread(target=focus, daemon=True, name="emu-focus").start()

    def _on_finished(self, s) -> None:
        if s.copy_thread and s.copy_thread.is_alive():
            self._background.append(s)
        if s.phase in ("finished", "error"):
            hwnd = self._hwnd()
            winutil.set_topmost(hwnd, False)
            winutil.bring_to_front(hwnd)       # SW_RESTORE + fokus

    # ── menu w grze (wywoływane z wątku hotkey; nazwy bez _ — to interfejs UI) ──
    def _hwnd(self) -> int:
        return winutil.find_window(self._window.title) if self._window else 0

    def menu_show(self, items: list, message: str) -> None:
        s = self._session
        with self._menu_lock:
            self._menu_inputs.clear()
            self._menu.update(open=True, items=items, message=message,
                              title=s.game.get("title", "") if s else "",
                              seq=self._menu["seq"] + 1)
        hwnd = self._hwnd()
        winutil.set_topmost(hwnd, True)
        winutil.bring_to_front(hwnd)

    def menu_hide(self) -> None:
        with self._menu_lock:
            self._menu["open"] = False
            self._menu_inputs.clear()
        # okna nie minimalizujemy (WebView2 po przywróceniu bywał czarny) —
        # emulator dostaje fokus i przykrywa je (GameMenu._focus_emulator)
        winutil.set_topmost(self._hwnd(), False)

    def menu_input(self, action: str) -> None:
        with self._menu_lock:
            if self._menu["open"]:
                self._menu_inputs.append(action)

    def ingame_poll(self) -> dict:
        """Stan menu w grze + naciśnięcia z XInput od ostatniego odczytu."""
        with self._menu_lock:
            out = dict(self._menu, inputs=list(self._menu_inputs))
            self._menu_inputs.clear()
        return out

    def ingame_action(self, name: str) -> None:
        s = self._session
        log.info("menu w grze: %s", name)
        if s and s.menu:
            s.menu.action(str(name))

    def _copying(self) -> list:
        self._background = [s for s in self._background
                            if s.copy_thread and s.copy_thread.is_alive()]
        ids = [s.game_id for s in self._background]
        ids += [gid for gid, j in self._pin_jobs.items() if j["thread"].is_alive()]
        return ids

    def copy_status(self) -> list:
        out = []
        for s in self._background:
            if s.copy_thread and s.copy_thread.is_alive() and s.prog:
                out.append({"game_id": s.game_id, "title": s.game.get("title", ""),
                            **s.prog.snapshot()})
        for gid, j in list(self._pin_jobs.items()):
            if j["thread"].is_alive():
                out.append({"game_id": gid, "title": j["title"], **j["prog"].snapshot()})
            else:
                self._pin_jobs.pop(gid, None)
        return out

    # ── przypinanie ──
    def toggle_pin(self, game_id: int) -> dict:
        g = library.game(int(game_id))
        if not g:
            return {"ok": False}
        row = library.db().execute("SELECT pinned FROM cache WHERE game_id=?",
                                   (g["id"],)).fetchone()
        pinned = not (row and row["pinned"])
        cache.set_pinned(g["id"], pinned)
        if pinned and not cache.is_complete(self._cfg, g):
            self._start_pin_copy(g)
        elif not pinned:
            job = self._pin_jobs.pop(g["id"], None)
            if job:
                job["cancel"].set()
            cache.touch(g["id"])          # odpięta wraca do zwykłej kolejki LRU
            cache.enforce_limit(self._cfg)
        return {"ok": True, "pinned": pinned}

    def _start_pin_copy(self, g: dict, auto: bool = False) -> None:
        """`auto` — wznowienie bez prośby (podgląd gry): najwyżej raz na PIN_RETRY s."""
        now = time.monotonic()
        tries = self.__dict__.setdefault("_pin_tries", {})
        if auto and now - tries.get(g["id"], -1e9) < PIN_RETRY:
            return
        tries[g["id"]] = now
        rom_dir = library.game_dir(g)
        prog = cache.Progress(cache.missing_bytes(self._cfg, g), len(g["files"]))
        cancel = threading.Event()

        def run():
            try:
                srcs, deps = netsrc.find(self._cfg, g)
                if len(srcs) != len(g["files"]):
                    return    # serwer niedostępny — dociągniemy przy następnym uruchomieniu gry
                extra = [(src, cache.root(self._cfg) / g["es"] / rel, size, 0) for rel, src, size in deps]
                cache.copy_game(self._cfg, g, rom_dir, prog, cancel, extra, srcs=srcs)
            except cache.Cancelled:
                pass
            except Exception:
                log.exception("pin copy")
        t = threading.Thread(target=run, daemon=True, name="pin-copy")
        self._pin_jobs[g["id"]] = {"thread": t, "prog": prog, "cancel": cancel,
                                   "title": g["title"]}
        t.start()

    def remove_local(self, game_id: int) -> dict:
        """Usuwa lokalną kopię gry (cache, także niedokończoną i przypiętą — przypięcie
        znika). Gra zostaje na NAS-ie/serwerze; przy następnym uruchomieniu pobierze się."""
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}
        s = self._session
        if s and s.game_id == g["id"] and s.phase in ("preparing", "downloading", "extracting", "running"):
            return {"ok": False, "reason": "Ta gra jest właśnie uruchomiona — zamknij ją najpierw."}
        job = self._pin_jobs.pop(g["id"], None)
        if job:
            job["cancel"].set()
            job["thread"].join(10)
        for b in self._background:
            if b.game_id == g["id"] and b.copy_thread and b.copy_thread.is_alive():
                b.cancel.set()
                b.copy_thread.join(10)
        row = library.db().execute("SELECT size FROM cache WHERE game_id=?", (g["id"],)).fetchone()
        cache.remove(self._cfg, g)
        with library.db() as c:
            c.execute("UPDATE cache SET pinned=0, last_used=0 WHERE game_id=?", (g["id"],))
        left = [rel for rel, _s, _m in g["files"]
                if (cache.root(self._cfg) / g["es"] / rel).exists()]
        if left:
            return {"ok": False, "reason": f"Nie udało się usunąć: {', '.join(left[:3])} "
                                           "(plik w użyciu?)."}
        log.info("usunięto lokalną kopię: %s", g["title"])
        return {"ok": True, "freed": (row["size"] if row else 0) or g["size"]}

    # ── opcje gry (przytrzymane A) ──
    def _effective_emu(self, g: dict) -> dict:
        own = library.game_emu(g)
        if own:
            return {"label": own["label"], "exe": own["exe"], "args": own["args"], "own": True}
        sc = (self._cfg.get("systems") or {}).get(g["es"]) or {}
        return {"label": sc.get("label", ""), "exe": sc.get("exe", ""), "args": sc.get("args", ""),
                "own": False}

    def game_options(self, game_id: int) -> dict:
        g = library.game(int(game_id))
        if not g:
            return {}
        emu = self._effective_emu(g)
        opts = emulators.options_for(systems.info(g["es"]), self._cfg.get("emu_root", ""))
        states, slots = [], []
        desc: dict = {}
        if emu["exe"]:
            ad = ingame.adapter_for(emu["exe"])
            try:
                profiles.bind(ad, self._profile)       # stany w folderze profilu, nie emulatora
            except Exception:
                log.exception("foldery profilu")
            meta = metadata.ensure_local(self._cfg, g)
            learned = [r["prefix"] for r in library.db().execute(
                "SELECT prefix FROM states WHERE game_id=? AND family=?", (g["id"], ad.family))]
            bundled = self._bundle_states(g, emu, ad)
            states = bundled if bundled is not None else ingame.list_states(ad, g, meta, learned)
            slots = self._slots_brief(g, emu, ad)
            if ad.family == "retroarch":
                # stany innych rdzeni tej gry (inne urządzenia) — wczytanie przełącza rdzeń gry
                from emustart import savebundles
                core = savebundles.core_id(emu.get("args", ""))
                try:
                    for st in syncclient.other_core_states(self._cfg, self._profile, g, core):
                        m = re.search(r"\.state(\d*)(\.auto)?$", st["path"])
                        states.append({"path": st["path"], "time": st["time"], "other_core": st["core"],
                                       "arc": Path(st["path"]).name, "name": Path(st["path"]).name,
                                       "label": ("Kontynuuj" if m and m.group(2) else f"Stan #{int(m.group(1) or 0) if m else 0}"),
                                       "detail": f"rdzeń {st['core']} — wczytanie przełączy grę na ten rdzeń"})
                except Exception:
                    log.exception("stany innych rdzeni")
            launcher.resume_from_nas(self._cfg, self._profile, g)
            res = library.get_resume(self._profile, g["id"])
            if res and res["path"] and Path(res["path"]).is_file() and res["family"] == ad.family:
                states.insert(0, {"path": res["path"], "name": "Quicksave EmuStart",
                                  "time": res["created"], "resume": True})
        states = states[:KEEP_LIST]
        for st in states:                     # obrazek stanu (jeśli emulator go zapisał)
            th = ingame.state_thumb(Path(st["path"]))
            if th:
                st["thumb"] = "/media/_stany/" + th
            self._state_label(st, desc)
        self._continue_newest(states)
        states.sort(key=lambda x: (not x.get("resume"), bool(x.get("other_core")), not x.get("continue"), -x["time"]))
        sc = (self._cfg.get("systems") or {}).get(g["es"]) or {}
        return {"id": g["id"], "title": g["title"], "emulator": emu, "options": opts,
                "states": states, "es": g["es"], "system": systems.info(g["es"])["display"],
                "retroarch": bool(emu["exe"]) and ingame.adapter_for(emu["exe"]).family == "retroarch",
                "shader_off": bool(sc.get("shader_off")),
                "sync": self._sync_kind(g, emu), "slots": slots}

    def sync_progress(self) -> dict:
        """Postęp trwającej wysyłki / pobierania zapisów (pasek postępu): {active, op, done, total, eta}."""
        return syncclient.progress()

    def _sync_kind(self, g: dict, emu: dict) -> dict:
        """Stan synchronizacji; emulator bez paczek zapisów: „folder” (cały folder zapisów
        z serwerem przed grą i po niej — RPCS3) albo „none” (zapisy tylko na tym urządzeniu)."""
        if emu.get("exe"):
            ad = ingame.adapter_for(emu["exe"])
            if ad.family not in ("retroarch", "duckstation", "pcsx2", "dolphin", "ppsspp"):
                return {"state": "folder" if ad.family and ad.save_dirs() else "none", "last": 0}
        return self._sync_status(g)

    def _slots_brief(self, g: dict, emu: dict, ad) -> list:
        """Sloty zapisów gry do opcji gry (po synchronizacji w game_options; `ad` — emulator
        z folderami profilu); [] — emulator bez paczek zapisów."""
        try:
            bundles = self._game_bundles(g, emu, ad)
            if not bundles:
                return []
            plat, gid = syncclient.game_ids(g)
            return saveslots.summary(self._profile, plat, gid, bundles)
        except Exception:
            log.exception("sloty zapisów")
            return []

    def _game_bundles(self, g: dict, emu: dict, ad) -> list | None:
        """Paczki zapisów gry na tym komputerze (savebundles) — None: błąd."""
        from emustart import savebundles
        core = savebundles.core_id(emu.get("args", ""))
        try:
            return savebundles.for_game(ad, core, launcher.content_guess(self._cfg, g, emu["exe"]),
                                        launcher.ps2_info(self._cfg, g))
        except Exception:
            log.exception("paczki zapisów gry")
            return None

    def _bundle_states(self, g: dict, emu: dict, ad) -> list | None:
        """Stany gry z paczki — po synchronizacji z serwerem (serwer jest jedyną prawdą: lista
        to dokładnie paczka, bez lokalnych plików spoza niej). None — emulator bez paczek stanów."""
        bundles = self._game_bundles(g, emu, ad)
        st = next((b for b in bundles or [] if b.kind == "states"), None)
        if st is None:
            return None
        if launcher.post_busy():
            launcher.wait_post(30)               # zapisy po ostatniej grze jeszcze idą na serwer
        try:
            # gra działa (Lumen pozwala przeglądać bibliotekę w trakcie gry): bez synchronizacji —
            # pliki są w użyciu, a zmiana aktywnego slotu z innego urządzenia podmieniłaby je pod
            # emulatorem; lista z plików na dysku, synchronizacja po grze
            if not self._game_running():
                for b in bundles:
                    b.adopt_names()
                syncclient.before(self._cfg, self._profile, bundles, g)   # konflikty zapisów — przy uruchomieniu
        except Exception:
            log.exception("zapisy gry z serwera")
        st.collect()
        return [{"path": str(p), "arc": arc, "name": Path(p).name, "time": Path(p).stat().st_mtime}
                for p, arc in st.files if not arc.endswith(".png")]

    # ── sloty zapisów i edycja stanów (emustart/saveslots.py) ──
    def _game_running(self) -> bool:
        """Gra uruchamia się albo działa — pliki zapisów w folderach emulatora są w użyciu."""
        return bool(self._session and self._session.phase not in ("finished", "error", "cancelled"))

    def _slot_ctx(self, game_id: int):
        """(gra, paczki gry) dla slotów — paczki w folderach profilu gracza."""
        if self._game_running():
            # zamiana slotu / zmiana stanu pod działającym emulatorem: jego autozapis przy wyjściu
            # trafiłby do innego slotu
            raise saveslots.EditError("Najpierw zamknij grę — zapisy są teraz w użyciu.")
        g = library.game(int(game_id))
        if not g:
            raise saveslots.EditError("Nie ma takiej gry.")
        emu = self._effective_emu(g)
        if not emu["exe"]:
            raise saveslots.EditError("Ta gra nie ma emulatora.")
        ad = ingame.adapter_for(emu["exe"])
        profiles.bind(ad, self._profile)
        bundles = self._game_bundles(g, emu, ad)
        if not bundles:
            raise saveslots.EditError("Ten emulator nie ma jeszcze synchronizacji zapisów tej gry.")
        if launcher.post_busy():
            launcher.wait_post(30)               # zapisy po ostatniej grze jeszcze idą na serwer
            if launcher.post_busy():
                # pliki tej gry właśnie idą na serwer — zamiana slotu / zmiana stanu by się z nimi minęła
                raise saveslots.EditError("Zapisy po ostatniej grze jeszcze idą na serwer — spróbuj za chwilę.")
        return g, bundles

    def _slot_call(self, fn) -> dict:
        try:
            with self._slot_lock:
                return fn()
        except saveslots.EditError as ex:
            return {"ok": False, "reason": str(ex)}
        except Exception as ex:
            log.exception("sloty zapisów")
            return {"ok": False, "reason": f"Nie udało się ({ex})."}

    def save_slots(self, game_id: int) -> dict:
        """Sloty zapisów gry: [{slot, active, states, saves, time}]."""
        def run():
            g, bundles = self._slot_ctx(game_id)
            plat, gid = syncclient.game_ids(g)
            with syncclient._lock:               # dokończenie przerwanej zamiany — nie w trakcie synchronizacji
                saveslots.finish_swap(self._profile, plat, gid, bundles)
            return {"ok": True, "slots": saveslots.summary(self._profile, plat, gid, bundles)}
        return self._slot_call(run)

    def save_slot_set(self, game_id: int, slot: int) -> dict:
        """Aktywny slot gry (zamiana plików od razu, wybór na serwer)."""
        def run():
            g, bundles = self._slot_ctx(game_id)
            r = saveslots.set_active(self._cfg, self._profile, g, bundles, int(slot))
            log.info("sloty: %s → slot %d", g["title"], int(slot))
            return r
        return self._slot_call(run)

    def state_edit_info(self, game_id: int) -> dict:
        """Do edycji stanu: wolne numery i pozostałe sloty."""
        def run():
            g, bundles = self._slot_ctx(game_id)
            ed = saveslots.Editor(self._cfg, self._profile, g, bundles)
            return {"ok": True, "numbers": ed.numbers(), "slots": ed.slots(), "slot": ed.slot,
                    "retroarch": ed.st.emulator_id == "retroarch"}
        return self._slot_call(run)

    def _other_core_ctx(self, game_id: int, core: str):
        """Stany innego rdzenia RetroArcha (lista „Wczytaj zapis” → `other_core`)."""
        if self._game_running():
            raise saveslots.EditError("Najpierw zamknij grę — zapisy są teraz w użyciu.")
        g = library.game(int(game_id))
        if not g:
            raise saveslots.EditError("Nie ma takiej gry.")
        return saveslots.OtherCore(self._cfg, self._profile, g, str(core))

    def state_edit(self, game_id: int, op: str, arc: str, arg=None, core: str = "") -> dict:
        """Zmiana stanu aktywnego slotu: renumber (arg: numer), copy_new, copy_slot / move_slot
        (arg: slot), continue, delete; restore (arc: nazwa w koszu). `core` — stan innego rdzenia
        RetroArcha (`other_core` z listy stanów): tylko delete i restore."""
        def run():
            if core:
                if op not in ("delete", "restore"):
                    raise saveslots.EditError("Stan innego rdzenia można tylko usunąć albo przywrócić.")
                ed = self._other_core_ctx(game_id, core)
                return ed.delete(arc) if op == "delete" else ed.restore(arc)
            g, bundles = self._slot_ctx(game_id)
            ed = saveslots.Editor(self._cfg, self._profile, g, bundles)
            fn = {"renumber": lambda: ed.renumber(arc, int(arg)), "copy_new": lambda: ed.copy_new(arc),
                  "copy_slot": lambda: ed.to_slot(arc, int(arg)), "move_slot": lambda: ed.to_slot(arc, int(arg), True),
                  "continue": lambda: ed.set_continue(arc), "delete": lambda: ed.delete(arc),
                  "restore": lambda: ed.restore(arc)}.get(str(op))
            if not fn:
                raise saveslots.EditError("Nieznana zmiana stanu.")
            return fn()
        return self._slot_call(run)

    def state_trash(self, game_id: int, core: str = "") -> dict:
        """Kosz aktywnego slotu: [{name, arc, time, label, thumb}]; `core` — kosz innego rdzenia
        RetroArcha (przywrócenie: state_edit(…, "restore", name, core=core))."""
        def run():
            if core:
                ed = self._other_core_ctx(game_id, core)
                items = ed.trash()
                ed.settle({})
            else:
                g, bundles = self._slot_ctx(game_id)
                items = saveslots.Editor(self._cfg, self._profile, g, bundles).trash()
            for it in items:
                n = saveslots.state_no(it["arc"])
                it["label"] = "Kontynuuj" if it["arc"] == saveslots.AUTO else (f"Stan #{n}" if n is not None else it["arc"])
                th = ingame.state_thumb(Path(it.pop("path")))
                if th:
                    it["thumb"] = "/media/_stany/" + th
            return {"ok": True, "items": items}
        return self._slot_call(run)

    def play_other_core(self, game_id: int, core: str) -> dict:
        """„Wczytaj zapis” — stan innego rdzenia: ta gra przechodzi na RetroArch z tym rdzeniem
        (wybór także na koncie), brakujący rdzeń — z serwera. Potem launch("arc:<stan>")."""
        from emustart import emuchoice, installer
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}
        key = f"retroarch#{str(core).lower()}"
        opt = emuchoice.find(self._cfg, g["es"], key)
        if not opt:
            item = emuchoice.install_item(key)
            if item in (installer.index().get("items") or {}):
                try:
                    installer.install(self._cfg, item)
                    emulators.forget_scan()
                    opt = emuchoice.find(self._cfg, g["es"], key)
                except Exception as ex:
                    log.exception("pobieranie rdzenia %s", core)
                    return {"ok": False, "reason": f"Nie udało się pobrać rdzenia {core}: {ex}"}
        if not opt:
            return {"ok": False, "reason": f"Brak rdzenia {core} na tym komputerze i na serwerze."}
        self.set_game_emulator(int(game_id), opt)
        return {"ok": True, "label": opt["label"]}

    def _sync_status(self, g: dict) -> dict:
        """Stan synchronizacji zapisów gry z serwerem (opcje gry)."""
        try:
            return syncclient.status(self._cfg, self._profile, g)
        except Exception:
            log.exception("stan synchronizacji zapisów")
            return {"state": "never"}

    def ra_states(self, game_id: int) -> dict:
        """„Wczytaj zapis” — stany ze wszystkich urządzeń (na telefonie: Bridge.raStates).
        Na PC to ta sama lista co w opcjach gry."""
        opts = self.game_options(game_id)
        return {"ok": bool(opts), "states": opts.get("states", []) if opts else []}

    @staticmethod
    def _continue_newest(states: list) -> None:
        """„Kontynuuj” = naprawdę najnowszy stan gry. Autozapis RetroArcha (.state.auto) bywa
        starszy niż ręczne stany (np. zapis przy wyjściu wyłączony) — wtedy „Kontynuuj” dostaje
        najnowszy ręczny stan (wczytanie: kopia na miejsce autozapisu), a autozapis to „Autozapis”."""
        own = [s for s in states if s.get("label") and not s.get("other_core") and not s.get("resume")]
        if not own:
            return
        newest = max(own, key=lambda s: (s["time"], bool(s.get("continue"))))
        if newest.get("continue"):
            return
        for s in own:
            if s.get("continue"):
                s.pop("continue")
                s["label"] = "Autozapis"
        newest["continue"] = True
        newest["label"] = f"Kontynuuj — najnowszy stan ({newest['label']})"

    @staticmethod
    def _state_label(st: dict, desc: dict) -> None:
        """Opis stanu na liście: „Kontynuuj”, „Stan #13”, urządzenie i rodzaj (z serwera)."""
        name = Path(st["path"]).name if st.get("path") else st.get("name", "")
        d = desc.get(name) or {}
        m = re.match(r"^.+?\.state(\d*)(\.auto)?$", name, re.I)
        n = re.match(r"^[A-Z0-9]{6}\.s(\d{2})$|^[A-Z]{4}\d{5}_(\d{1,2})\.ppst$", name, re.I)   # Dolphin, PPSSPP
        if n and not st.get("resume"):
            st["label"] = f"Stan #{int(n.group(1) or n.group(2))}"
            return
        if st.get("resume") or not m:
            return
        if m.group(2):
            st["continue"] = True
            st["label"] = "Kontynuuj — najnowszy stan"
        else:
            st["label"] = f"Stan #{int(m.group(1) or 0)}"
        who = d.get("device") or ""
        if who:
            who = ("telefon " if d.get("platform") == "telefon" else "") + who
        kind = {"auto": "autozapis przy wyjściu", "manual": ""}.get(d.get("kind", ""), "")
        st["detail"] = " · ".join(x for x in (who, kind) if x)

    def set_shader_off(self, es: str, off: bool) -> dict:
        """RetroArch w tym systemie bez shadera (czarny ekran przy niezgodnym shaderze)."""
        if es not in systems.SYSTEMS and not library.db().execute("SELECT 1 FROM systems WHERE es=?", (es,)).fetchone():
            return {"ok": False}
        sc = self._cfg.setdefault("systems", {}).setdefault(es, {})
        if off:
            sc["shader_off"] = True
        else:
            sc.pop("shader_off", None)
        config.save(self._cfg)
        return {"ok": True, "off": bool(off)}

    # ── historia ustawień emulatora (opcje gry → Ustawienia emulatora) ──
    def _emu_family(self, game_id: int) -> str:
        g = library.game(int(game_id))
        emu = self._effective_emu(g) if g else {}
        return ingame.adapter_for(emu["exe"]).family if emu.get("exe") else ""

    def emu_settings_history(self, game_id: int) -> dict:
        fam = self._emu_family(game_id)
        if not fam:
            return {"ok": False, "reason": "Ta gra nie ma jeszcze emulatora."}
        return {"ok": True, "family": fam, "list": profiles.settings_history(self._profile, fam)}

    def emu_settings_restore(self, game_id: int, hid: str) -> dict:
        fam = self._emu_family(game_id)
        if not fam:
            return {"ok": False, "reason": "Ta gra nie ma jeszcze emulatora."}
        return profiles.settings_restore(self._cfg, self._profile, fam, str(hid))

    # ── menedżer zapisów (opcje gry → Zapisy i stany) ──
    def _savemgr_ctx(self, g: dict):
        prof = profiles.get(self._profile) or {}
        if not prof.get("nas_name") or not profiles.nas_online(self._cfg):
            return None, None, "Menedżer zapisów działa przez serwer EmuStart — serwer jest niedostępny."
        return nasfs.backend(self._cfg), prof["nas_name"], ""

    def save_manager(self, game_id: int) -> dict:
        """Stany gry z komputera i telefonu (ze zgodnością) i wspólne zapisy w grze."""
        from emustart import savemgr
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}
        fs, nas, why = self._savemgr_ctx(g)
        if not fs:
            return {"ok": False, "reason": why}
        meta = metadata.get(g["id"])
        serial = ingame._serial(meta)
        stems = [Path(g["rel"]).stem, g["name"]]
        if g["rel"].lower().endswith(".zip"):
            # RetroArch nazywa stan od ROM-u w środku ZIP-a — z kopii lokalnej (gra pobrana)
            local = cache.local_path(self._cfg, g)
            if local.is_file():
                stems += savemgr.zip_stems(local)
        learned = [r["prefix"] for r in library.db().execute(
            "SELECT prefix FROM states WHERE game_id=?", (g["id"],))]
        stems += [p[:-len(".state")] for p in learned if p.endswith(".state")]
        try:
            out = fs.savemgr_catalog({"profile": nas, "es": g["es"], "name": g["name"],
                                      "stems": "|".join(stems), "serial": serial,
                                      "learned": "|".join(learned)})
        except OSError as ex:
            return {"ok": False, "reason": f"Nie udało się odczytać zapisów: {ex}"}
        out["ok"] = True
        out["here"] = "windows"
        return out

    def save_copy(self, game_id: int, rel: str, platform: str, emu: str) -> dict:
        """Kopia stanu na drugą platformę; na ten komputer — od razu do folderu emulatora."""
        g = library.game(int(game_id))
        if not g:
            return {"ok": False, "reason": "Nie ma takiej gry."}
        fs, nas, why = self._savemgr_ctx(g)
        if not fs:
            return {"ok": False, "reason": why}
        try:
            out = fs.savemgr_copy({"profile": nas, "src": rel, "platform": platform, "emu": emu,
                                   "es": g["es"], "name": g["name"], "device": socket.gethostname()})
        except OSError as ex:
            return {"ok": False, "reason": f"Nie udało się skopiować: {ex}"}
        if out.get("ok") and platform == "windows":
            # od razu u siebie: pobranie folderu stanów emulatora z NAS-a
            emu_cfg = self._effective_emu(g)
            ad = ingame.adapter_for(emu_cfg["exe"]) if emu_cfg["exe"] else None
            if ad and ad.family == emu:
                try:
                    profiles.sync_down(self._cfg, self._profile, emu, [d.name for d in ad.save_dirs()])
                    out["local"] = True
                except Exception:
                    log.exception("menedżer zapisów: pobranie stanów")
        return out

    def set_game_emulator(self, game_id: int, opt: dict | None) -> dict:
        from emustart import emuchoice
        with library.db() as c:
            if not opt:
                c.execute("DELETE FROM game_emu WHERE game_id=?", (int(game_id),))
            else:
                c.execute("INSERT OR REPLACE INTO game_emu VALUES(?,?,?,?)",
                          (int(game_id), opt["label"], opt["exe"], opt.get("args", "")))
        g = library.game(int(game_id))
        if g:                                 # wybór dla gry → konto (inne komputery konta)
            threading.Thread(target=lambda: _quiet(emuchoice.record_game, self._cfg, self._profile, g, opt),
                             daemon=True, name="wybor-emulatora").start()
        return {"ok": True}

    def meta_fetch(self, game_id: int, full: bool = False) -> dict:
        """Opis gry z serwera (serwer pobiera go z sieci, gdy go nie ma) → lokalna kopia.
        UI odpytuje game_detail."""
        g = library.game(int(game_id))
        if not g:
            return {"ok": False}

        def run():
            import json as _json
            from emustart import libsync
            try:
                files, _deps = netsrc.find(self._cfg, g)
                sid = next(iter(files.values())).gid if files else 0
                if not sid:
                    return
                d = libsync._get(self._cfg, f"/v1/games/{sid}", 15)
                if d.get("meta_online") and d.get("meta"):
                    with library.db() as c:
                        c.execute("INSERT OR REPLACE INTO game_meta(game_id, data, edits, online, updated) "
                                  "VALUES(?,?,?,?,?)", (g["id"], _json.dumps(d["meta"], ensure_ascii=False),
                                                         "{}", 1, time.time()))
            except Exception as ex:
                log.info("opis %s z serwera: %s", g["name"], ex)
        threading.Thread(target=run, daemon=True, name="meta").start()
        return {"ok": True}

    # ── administrator: grafiki, opisy i wygląd systemów przez serwer (serwer = źródło prawdy) ──
    def _is_admin(self) -> bool:
        i = netsrc.info_nowait(self._cfg) or {}
        return bool(i.get("admin"))

    def art_keys_push(self) -> dict:
        """Administrator: klucze źródeł grafik (SteamGridDB, IGDB, TheGamesDB) z PyLinks na tym
        komputerze → serwer. PyLinks szyfruje je kluczem tego komputera, więc odszyfrować je
        da się tylko tutaj; na serwer idą połączeniem szyfrowanym, serwer szyfruje je swoim."""
        from emustart import art_sources, secure
        if not self._is_admin():
            return {"ok": False, "reason": "Tylko na urządzeniu administratora."}
        found = art_sources.import_pylinks_keys()
        if not found:
            return {"ok": False, "reason": "Nie znaleziono config.json PyLinksWeb ("
                    + ", ".join(art_sources.PYLINKS_CONFIGS) + ")."}
        plain = {k: secure.unprotect(found.get(k, "")) for k in art_sources.KEY_NAMES}
        plain = {k: v for k, v in plain.items() if v}
        if not plain:
            return {"ok": False, "reason": "W PyLinks nie ma kluczy (albo nie da się ich odszyfrować na tym koncie)."}
        r = self._admin_call("PUT", "art_keys", plain)
        if r.get("ok"):
            log.info("klucze źródeł grafik wysłane na serwer: %s", ", ".join(sorted(plain)))
            r["sent"] = sorted(plain)
        return r

    def _admin_call(self, method: str, path: str, body: dict | None = None) -> dict:
        """Zapytanie do /v1/admin/… serwera. Po zmianie — biblioteka z serwera w tle (wersja
        systemu na serwerze wzrosła), żeby ten komputer od razu pokazał nową grafikę / opis."""
        import http.client
        import json as _json
        ep = netsrc.endpoint(self._cfg)
        if not ep:
            return {"ok": False, "reason": "Zaloguj się do serwera EmuStart."}
        data = _json.dumps(body).encode("utf-8") if body is not None else None
        for attempt in (0, 1):
            c = netsrc.conn(ep[0], ep[1], 60)
            try:
                c.request(method, "/v1/admin/" + path, body=data,
                          headers={**netsrc.auth_headers(ep[2]), "Content-Type": "application/json"})
                r = c.getresponse()
                raw = r.read()
            except (OSError, http.client.HTTPException) as ex:
                return {"ok": False, "reason": f"Serwer niedostępny: {ex}"}
            finally:
                c.close()
            if r.status == 401 and attempt == 0 and ep[2].startswith(netsrc.DEV):
                netsrc.drop_token()
                continue
            break
        try:
            out = _json.loads(raw or b"{}")
        except ValueError:
            out = {}
        if r.status == 403:
            return {"ok": False, "reason": "Zmieniać grafiki i opisy może tylko administrator."}
        if r.status != 200:
            return {"ok": False, "reason": out.get("error") or f"Serwer: błąd {r.status}"}
        if method != "GET":
            threading.Thread(target=self._thin_sync, daemon=True, name="biblioteka-po-zmianie").start()
        return out if isinstance(out, (dict, list)) else {"ok": False}

    def _sid(self, game_id: int) -> int:
        """Identyfikator gry na serwerze (lokalna kopia biblioteki ma własne)."""
        g = library.game(int(game_id))
        if not g:
            return 0
        files, _deps = netsrc.find(self._cfg, g)
        return next(iter(files.values())).gid if files else 0

    @staticmethod
    def _via_server(url: str) -> str:
        """Podgląd z serwera (logo z paczki, grafika serwera) przez lokalny serwer interfejsu."""
        return "/serwer" + url if url.startswith("/") and not url.startswith("/assets/") else url

    def art_candidates(self, game_id: int, kind: str, query: str = "") -> list:
        sid = self._sid(game_id)
        if not sid:
            return []
        r = self._admin_call("GET", f"games/{sid}/art?" + urllib.parse.urlencode({"kind": kind, "q": query or ""}))
        return [dict(c, url=c.get("url", ""), thumb=self._via_server(c.get("thumb") or c.get("url", "")))
                for c in (r.get("list") or [])] if r.get("ok") else []

    def _drop_local_art(self, game_id: int, kind: str, state: int | None = None) -> None:
        """Lokalna kopia grafiki po zmianie na serwerze — usunięta, pobierze się nowa."""
        g = library.game(int(game_id))
        if g and kind in ("box", "snap", "logo"):
            art.delete(g["es"], g["name"], kind)
            if state is not None:
                with library.db() as c:
                    c.execute(f"UPDATE games SET art_{kind}=? WHERE id=?", (state, g["id"]))

    def art_choose(self, game_id: int, kind: str, url: str) -> dict:
        sid = self._sid(game_id)
        if not sid:
            return {"ok": False, "reason": "Serwer nie ma tej gry albo nie odpowiada."}
        r = self._admin_call("PUT", f"games/{sid}/art?" + urllib.parse.urlencode({"kind": kind, "url": url}))
        if r.get("ok"):
            self._drop_local_art(game_id, kind, art.HAS)
            g = library.game(int(game_id))
            r["url"] = art.media_url(g["es"], g["name"], kind) + f"&t={int(time.time())}" if g else ""
        return r

    def art_clear(self, game_id: int, kind: str) -> dict:
        sid = self._sid(game_id)
        if not sid:
            return {"ok": False, "reason": "Serwer nie ma tej gry albo nie odpowiada."}
        r = self._admin_call("DELETE", f"games/{sid}/art?" + urllib.parse.urlencode({"kind": kind}))
        if r.get("ok"):
            self._drop_local_art(game_id, kind, art.MISSING)
        return r

    def meta_save(self, game_id: int, edits: dict) -> dict:
        sid = self._sid(game_id)
        if not sid:
            return {"ok": False, "reason": "Serwer nie ma tej gry albo nie odpowiada."}
        r = self._admin_call("PUT", f"games/{sid}/meta", {"edits": edits or {}})
        if r.get("ok"):
            metadata._store(int(game_id), edits=dict(edits or {}))      # od razu na ekranie
        return r

    def meta_fetch_full(self, game_id: int) -> dict:
        sid = self._sid(game_id)
        if not sid:
            return {"ok": False, "reason": "Serwer nie ma tej gry albo nie odpowiada."}
        return self._admin_call("PUT", f"games/{sid}/meta/fetch?full=1")

    def system_logo_candidates(self, es: str) -> list:
        r = self._admin_call("GET", f"systems/{urllib.parse.quote(es)}/logos")
        return [dict(c, url=self._via_server(c.get("url", ""))) for c in (r.get("list") or [])] if r.get("ok") else []

    def system_logo_choose(self, es: str, cid: str) -> dict:
        r = self._admin_call("PUT", f"systems/{urllib.parse.quote(es)}/logo?" + urllib.parse.urlencode({"id": cid}))
        if r.get("ok"):
            logos.drop(es)                                            # stara kopia logo — pobierze się nowe
        return r

    # ── opcje systemu (przytrzymane A na logo) ──
    def system_options(self, es: str) -> dict:
        info = systems.info(es)
        sc = (self._cfg.get("systems") or {}).get(es) or {}
        has_emu = bool(sc.get("exe") and Path(sc["exe"]).is_file())
        shown = next((s_ for s_ in self.list_systems() if s_["es"] == es), None) or {}
        return {"es": es, "display": shown.get("display") or info["display"], "default_name": info["display"],
                "logo": shown.get("logo", ""), "glow": bool(shown.get("logo_glow")), "admin": self._is_admin(),
                "emulator": sc.get("label", "") if has_emu else "",
                "options": emulators.options_for(info, self._cfg.get("emu_root", "")),
                "install": installer.plan_for(es, self._cfg.get("emu_root", "")) if not has_emu else []}

    def system_set(self, es: str, data: dict) -> dict:
        """Na tym komputerze: emulator systemu i ukrycie systemu. Nazwa i poświata logo —
        na serwerze (administrator), dla wszystkich urządzeń."""
        shared = {k: data[k] for k in ("name", "glow") if k in data}
        if shared:
            r = self._admin_call("PUT", f"systems/{urllib.parse.quote(es)}", shared)
            if not r.get("ok"):
                return r
        sc = self._cfg.setdefault("systems", {}).setdefault(es, {})
        if "enabled" in data:
            sc["enabled"] = bool(data["enabled"])
        if data.get("emulator"):
            o = data["emulator"]
            sc.update(label=o["label"], exe=o["exe"], args=o.get("args", ""), enabled=True)
            self._record_choice(es, o)
        config.save(self._cfg)
        return {"ok": True}

    def _record_choice(self, es: str, opt: dict) -> None:
        """Wybór emulatora systemu → konto gracza (inne komputery konta — ten sam)."""
        from emustart import emuchoice
        threading.Thread(target=lambda: _quiet(emuchoice.record_system, self._cfg, self._profile, es, opt),
                         daemon=True, name="wybor-emulatora").start()

    # ── pobieranie emulatorów ──
    def install_missing(self) -> list:
        """Systemy w bibliotece bez emulatora i co dla nich pobrać (z serwera)."""
        installer.refresh_index(self._cfg)
        out = []
        for s in library.systems_summary():
            sc = (self._cfg.get("systems") or {}).get(s["es"]) or {}
            if not sc.get("enabled", True) or (sc.get("exe") and Path(sc["exe"]).is_file()):
                continue
            steps = installer.plan_for(s["es"], self._cfg.get("emu_root", ""))
            out.append({"es": s["es"], "system": s["display"], "games": s["games"],
                        "steps": steps})
        return out

    def install_start(self, es_list: list) -> dict:
        job = getattr(self, "_install_job", None)
        if job and not job.finished:
            return {"ok": False, "reason": "Pobieranie emulatorów już trwa."}
        root = self._cfg.get("emu_root", "")
        items = [(es, installer.plan_for(es, root)) for es in es_list]
        items = [(es, st) for es, st in items if st]
        if not items:
            return {"ok": False, "reason": "Nie ma czego pobrać."}

        def done(job):
            config.save(self._cfg)
        self._install_job = installer.Job(self._cfg, items, on_done=done)
        self._install_job.start()
        return {"ok": True}

    def install_status(self) -> dict | None:
        job = getattr(self, "_install_job", None)
        return job.status() if job else None

    def install_cancel(self) -> None:
        job = getattr(self, "_install_job", None)
        if job:
            job.cancel.set()

    def _emu_sync(self) -> None:
        """Emulatory jak na serwerze: brakujące dla systemów biblioteki — instalacja, starsze —
        aktualizacja (działający emulator — przy następnej okazji). W tle, bez pytania."""
        if not self._cfg.get("emu_root"):
            return
        for j in (getattr(self, "_install_job", None), getattr(self, "_emu_job", None)):
            if j and not j.finished:
                return
        installer.refresh_index(self._cfg)
        hidden = {es for es, sc in (self._cfg.get("systems") or {}).items() if sc.get("enabled") is False}
        items = installer.sync_items(self._cfg, [s["es"] for s in library.systems_summary() if s["es"] not in hidden])
        if not items:
            return

        def done(job):
            config.save(self._cfg)
            if job.installed:
                log.info("emulatory z serwera: %s", ", ".join(job.installed))
            if job.postponed:
                log.info("emulatory: później (działają): %s", ", ".join(job.postponed))
        self._emu_job = installer.Job(self._cfg, items, on_done=done, quiet_busy=True)
        self._emu_job.start()

    def _emu_sync_quiet(self) -> None:
        try:
            self._emu_sync()
        except Exception:
            log.exception("emulatory z serwera")

    def emu_sync_status(self) -> dict | None:
        """Pobieranie emulatorów z serwera w tle — do paska u góry ekranu."""
        job = getattr(self, "_emu_job", None)
        return job.status() if job else None

    # ── pady ──
    def pads_state(self, battery: bool = False) -> dict:
        from emustart import xinput
        lst = pads.connected(battery=bool(battery))
        for p in lst:
            p["buttons"] = xinput.buttons(p["slot"]) or 0
        po = self._cfg.get("pad_order") or {}
        return {"pads": lst, "order": pads.order(self._cfg, lst),
                "mode": po.get("mode", "windows"), "manual": po.get("manual", [])}

    def pads_set(self, mode: str, manual: list | None = None) -> dict:
        self._cfg["pad_order"] = {"mode": mode if mode in ("windows", "wireless_first", "manual")
                                  else "windows", "manual": [int(x) for x in (manual or [])]}
        config.save(self._cfg)
        return self.pads_state()

    # ── profile ──
    def profiles_list(self) -> dict:
        profiles.import_from_nas(self._cfg)
        profiles.align_account_name(self._cfg)
        lst = profiles.visible(self._cfg, profiles.all_profiles())
        for p in lst:
            p["pin"] = profiles.pin_state(self._cfg, p["id"])
        # token konta i skrót PIN-u zostają w programie, nie w UI
        return {"profiles": [self._public_profile(p) for p in lst], "current": self._profile}

    def _pin_block(self, pid: int) -> dict | None:
        st = profiles.pin_state(self._cfg, int(pid), fresh=True)
        if st == "ok":
            return None
        prof = profiles.get(int(pid)) or {}
        n = prof.get("name", "")
        why = {"need_pin": f"Profil „{n}” — podaj PIN tego urządzenia.",
               "need_password": f"Konto „{n}” — podaj hasło konta.",
               "set_password": f"Konto „{n}” nie ma jeszcze hasła — ustaw je."}.get(st, "")
        return {"ok": False, "need": st, "name": n, "reason": why}

    def profile_pin_check(self) -> dict:
        """Przy starcie: czy bieżący profil jest dostępny na tym urządzeniu (hasło konta, PIN)."""
        prof = profiles.get(self._profile) or {}
        return {"state": profiles.pin_state(self._cfg, self._profile), "name": prof.get("name", "")}

    # konto na serwerze — hasło (emustart/accounts.py)
    def profile_login(self, pid: int, password: str) -> dict:
        ok, why = profiles.account_login(self._cfg, int(pid), str(password))
        return {"ok": ok, "reason": why}

    def profile_set_password(self, pid: int, password: str, old: str = "") -> dict:
        ok, why = profiles.account_password(self._cfg, int(pid), str(password), str(old or ""))
        return {"ok": ok, "reason": why}

    def profile_logout(self, pid: int) -> dict:
        profiles.account_logout(int(pid))
        return {"ok": True}

    def profile_join(self, name: str, password: str) -> dict:
        prof, why = profiles.account_join(self._cfg, str(name), str(password))
        if why == profiles.MISSING:
            return {"ok": False, "missing": True, "name": str(name).strip(),
                    "reason": f"Nie ma konta „{str(name).strip()}”."}
        return {"ok": bool(prof), "reason": why, "profile": prof and self._public_profile(prof)}

    # PIN — tylko to urządzenie
    def profile_unlock(self, pid: int, pin: str) -> dict:
        ok, why = profiles.local_pin_check(int(pid), str(pin))
        return {"ok": ok, "reason": why}

    def profile_set_pin(self, pid: int, pin: str, old: str = "") -> dict:
        ok, why = profiles.local_pin_set(int(pid), str(pin), str(old or ""))
        return {"ok": ok, "reason": why}

    def profile_clear_pin(self, pid: int, pin: str) -> dict:
        ok, why = profiles.local_pin_clear(int(pid), str(pin))
        return {"ok": ok, "reason": why}

    @staticmethod
    def _public_profile(p: dict) -> dict:
        """Profil dla interfejsu — bez tokenu konta i skrótu PIN-u."""
        out = {k: v for k, v in p.items() if k not in ("pin_token", "pin_hash")}
        out["has_pin"] = bool(p.get("pin_hash"))
        return out

    def profile_select(self, pid: int) -> dict:
        if not profiles.get(int(pid)):
            return {"ok": False}
        block = self._pin_block(pid)
        if block:
            return block
        self._profile = int(pid)
        library.meta_set("last_profile", str(self._profile))
        self._user_load(self._profile)
        self._pull_saves()
        return {"ok": True}

    def _pull_saves(self) -> None:
        """W tle: nowsze wersje paczek zapisów tego profilu z serwera (lokalnie bez zmian);
        bez gry, która właśnie działa albo wysyła zapisy."""
        pid = self._profile

        def run():
            skip = set()
            s = getattr(self, "_session", None)
            if s and s.game:
                skip.add(syncclient.game_ids(s.game))
            try:
                syncclient.pull_all(self._cfg, pid, skip)
            except Exception:
                log.exception("zapisy z serwera")
        launcher.start_post(run, "zapisy z serwera")

    def profile_create(self, name: str) -> dict:
        """Nowy profil lokalny (z serwerem nowe konto — zaproszenie albo panel). Nazwa zajęta
        (bez względu na wielkość liter) → exists: UI prosi o hasło tego konta (profile_join)."""
        try:
            p = profiles.create(name, cfg=self._cfg)
        except profiles.ProfileExists as ex:
            return {"ok": False, "exists": ex.name, "reason": str(ex)}
        except (ValueError, OSError) as ex:
            return {"ok": False, "reason": str(ex)}
        return {"ok": True, "profile": self._public_profile(p)}

    def profile_rename(self, pid: int, name: str) -> dict:
        try:
            profiles.rename(int(pid), name, self._cfg)
        except ValueError as ex:
            return {"ok": False, "reason": str(ex)}
        return {"ok": True}

    def profile_delete(self, pid: int) -> dict:
        if int(pid) == self._profile:
            return {"ok": False, "reason": "Nie można usunąć profilu, który właśnie gra."}
        try:
            profiles.delete(int(pid))
        except ValueError as ex:
            return {"ok": False, "reason": str(ex)}
        return {"ok": True}

    # ── narzędzie „Grafiki” ──
    def _thin_sync(self, force: bool = False) -> dict:
        """Biblioteka z serwera; po pierwszej — emulatory dla nowych systemów (zalecane przez serwer)."""
        from emustart import libsync
        r = libsync.sync(self._cfg, force=force)
        if r.get("ok") and self._cfg.get("emu_root"):
            self.autodetect()
        if r.get("changed"):
            self._lib_version += 1
        return r

    def _thin_loop(self) -> None:
        """Przy starcie i co 5 minut: zmiany z serwera (grafiki, opisy, logo, nowe gry — także
        ustawione przez administratora na innym urządzeniu). W trakcie gry — bez sieci."""
        while True:
            if not (self._session and self._session.phase in ("preparing", "downloading", "extracting", "running")):
                try:
                    self._thin_sync()
                except Exception:
                    log.exception("biblioteka z serwera")
                try:
                    self._emu_sync()
                except Exception:
                    log.exception("emulatory z serwera")
            time.sleep(300)

    def rescan(self) -> dict:
        """Odświeżenie biblioteki z serwera (Start → Odśwież bibliotekę z serwera)."""
        if self._scan["running"]:
            return {"ok": False}
        self._scan.update(running=True, text="Start…", done=0, total=0, result=None)
        from emustart import libsync

        def prog(text, done, total):
            self._scan.update(text=text, done=done, total=total)

        def run():
            try:
                if not libsync.thin(self._cfg):
                    self._scan["result"] = {"ok": False, "reason": "Najpierw zaloguj się do serwera EmuStart."}
                    return
                self._scan["result"] = libsync.sync(self._cfg, prog, force=True)
                if self._scan["result"].get("ok") and self._cfg.get("emu_root"):
                    self.autodetect()
            except Exception as ex:
                log.exception("scan")
                self._scan["result"] = {"ok": False, "reason": str(ex)}
            finally:
                self._scan["running"] = False
        threading.Thread(target=run, daemon=True, name="scan").start()
        return {"ok": True}

    def scan_status(self) -> dict:
        return dict(self._scan)

    # ── ustawienia ──
    def get_settings(self) -> dict:
        cfg = self._cfg
        rows = []
        for s_ in sorted(library.systems_summary(), key=lambda x: x["display"].lower()):
            es = s_["es"]
            sc = (cfg.get("systems") or {}).get(es) or {}
            rows.append({"es": es, "display": s_["display"], "known": es in systems.SYSTEMS,
                         "enabled": sc.get("enabled", True), "folders": 1,
                         "label": sc.get("label", ""), "exe": sc.get("exe", ""),
                         "args": sc.get("args", "")})
        return {"emu_root": cfg.get("emu_root", ""),
                "cache_dir": str(config.cache_dir(cfg)),
                "cache_recent": cfg.get("cache_recent", 10),
                "fullscreen": cfg.get("fullscreen", True),
                "hide_arcade_clones": cfg.get("hide_arcade_clones", True),
                "games_logo": cfg.get("games_logo", False),
                "pad_backend": cfg.get("pad_backend", "python"),
                "profile_settings": cfg.get("profile_settings", True),
                "stream_play": cfg.get("stream_play", True),
                "stream_min_seconds": cfg.get("stream_min_seconds", 0),
                "winfsp": self._winfsp,
                "server_url": cfg.get("server_url", ""),
                "server_key": cfg.get("server_key", ""),
                "server_keykind": {"tpm": "TPM", "dpapi": "DPAPI", "plik": "plik"}.get(cfg.get("server_keykind", ""), ""),
                "server_account": cfg.get("server_account", ""),
                "is_admin": self._is_admin(),
                "server_via": netsrc.current_kind(cfg),
                "enroll": ({k: cfg["enroll_pending"].get(k) for k in ("url", "code", "account", "mode")}
                           if cfg.get("enroll_pending") else None),
                **{k: bool(cfg.get(k)) for k in self.HIDE_KEYS},
                "ask_profile": profiles.ask_at_start(),
                "machine_profile": profiles.machine_owner(),
                "profile_names": {str(p["id"]): p["name"] for p in profiles.all_profiles()},
                "systems": rows}

    # ── logowanie na serwerze: konto + hasło + klucz urządzenia (emustart/enroll.py) ──
    def server_enroll(self, text: str, mode: str, account: str = "", password: str = "", code: str = "") -> dict:
        """Ustawienia → Zaloguj (emustart/enroll.py):
        - mode "code" — kod logowania ze strony serwera (albo z panelu): link / kod QR (adresy,
          odcisk certyfikatu, kod, konto) albo adres serwera + `code` wpisany ręcznie. Konto
          wynika z kodu, bez hasła i bez czekania na zatwierdzenie;
        - mode "existing" — „Mam konto”: nazwa i hasło; zatwierdza inne urządzenie konta albo
          administrator. Hasło idzie tylko w prośbie — program go nie zapisuje. Prośba czeka do 24 h.
        Klucz urządzenia: TPM, bez niego DPAPI."""
        import secrets
        import socket
        from emustart import keystore
        account = (account or "").strip()
        login = netsrc.parse_login(text)
        if not login.get("ok"):
            return login
        if mode == "code":
            login["code"] = login.get("code") or (code or "").strip()
            if not login["code"]:
                return {"ok": False, "reason": "Podaj kod logowania ze strony serwera."}
            account = login.get("account") or account
        elif mode == "existing":
            if not account:
                return {"ok": False, "reason": "Podaj nazwę konta."}
            if not password:
                return {"ok": False, "reason": "Podaj hasło konta."}
        else:
            return {"ok": False, "reason": "Nowe konto zakłada się z zaproszenia — zaloguj się na stronie serwera."}
        old = self._cfg.get("enroll_pending")
        if old and (old.get("account") != account or old.get("mode") != mode or login.get("code")):
            self.server_enroll_cancel()
            old = None
        if old:
            name, kind, pem = old["keyname"], old["keykind"], old["pubkey"]
        else:
            name = f"EmuStart-{secrets.token_hex(6)}"
            try:
                kind, pem = keystore.create(name)
            except Exception as ex:
                log.exception("klucz urządzenia")
                return {"ok": False, "reason": f"Nie udało się utworzyć klucza urządzenia: {ex}"}
        body = {"mode": mode, "account": account if mode == "existing" else "",
                "password": password if mode == "existing" else "", "name": socket.gethostname(),
                "class": "pc", "app": "EmuStart", "version": __version__, "pubkey": pem, "hw": netsrc.hw_id(),
                "login_code": login.get("code", "")}
        r: dict = {"ok": False, "reason": "Serwer nie odpowiada."}
        fp = ""
        for url, _kind, ca in login["endpoints"]:          # pierwszy osiągalny adres serwera
            fp = "" if ca else login["fp"]
            r = netsrc._post(url, "/v1/enroll/request", body, fp=fp, timeout=8)
            if not r.get("unreachable"):
                break
        if not r.get("ok"):
            if not old:
                keystore.delete(name, kind)
            return {"ok": False, "reason": r.get("reason", "")}
        self._cfg["enroll_pending"] = {"url": r["url"], "fp": fp, "rid": r["rid"], "code": r["pretty"],
                                       "account": account, "mode": mode, "keyname": name, "keykind": kind,
                                       "pubkey": pem, "expires": r["expires"]}
        if login["fp"]:                                    # kod QR: odcisk z kanału zaufanego (aparat / panel)
            netsrc.learn(self._cfg, {"tls": {"fp": login["fp"]},
                                     "endpoints": [{"url": u, "kind": k, **({"ca": True} if c else {})}
                                                   for u, k, c in login["endpoints"]]}, trusted=True)
        config.save(self._cfg)
        log.info("prośba o zalogowanie na %s (%s, konto %s, klucz: %s)", r["url"], mode, account or "z kodu", kind)
        out = {"ok": True, "code": r["pretty"], "account": account, "url": r["url"], "key": kind,
               "expires_in": max(0, r["expires"] - time.time())}
        if r.get("status") == "approved":                  # kod logowania — od razu
            p = self.server_enroll_poll()
            out.update(status=p.get("status"), account=p.get("account") or account)
        return out

    def server_scan(self) -> dict:
        """Kod QR — tylko na telefonie (aparat); na komputerze: Zaloguj → link ze strony serwera."""
        return {"ok": False, "reason": "Na komputerze: Zaloguj → wklej link ze strony serwera (Pokaż kod logowania)."}

    def server_enroll_poll(self) -> dict:
        p = self._cfg.get("enroll_pending")
        if not p:
            return {"ok": False, "status": "none"}
        r = netsrc._post(p["url"], "/v1/enroll/poll", {"rid": p["rid"]}, fp=p.get("fp", ""))
        st = r.get("status") if r.get("ok") else "error"
        if st == "approved":
            from emustart import keystore
            old = (self._cfg.get("server_keyname"), self._cfg.get("server_keykind"))
            self._cfg.update(server_url=p["url"], server_key=netsrc.DEV + r["device"], server_keyname=p["keyname"],
                             server_keykind=p["keykind"], server_account=r.get("account") or p["account"])
            self._cfg.pop("enroll_pending", None)
            config.save(self._cfg)
            if old[0] and old[0] != p["keyname"]:
                keystore.delete(old[0], old[1] or "")      # klucz poprzedniego logowania
            netsrc.learn(self._cfg, r, trusted=True)        # adresy i odcisk — z połączenia szyfrowanego
            netsrc._info.clear()
            netsrc.drop_token()
            netsrc.use_device(self._cfg)
            nasfs.reset()
            threading.Thread(target=lambda: netsrc.info(self._cfg, fresh=True), daemon=True,
                             name="adresy-serwera").start()
            log.info("zalogowano na serwerze %s (konto %s)", p["url"], self._cfg["server_account"])
            self._adopt_account(self._cfg["server_account"])
            self._pull_saves()                          # zapisy gier tego konta z serwera
            from emustart import libsync
            if libsync.thin(self._cfg):
                threading.Thread(target=self._thin_sync, daemon=True, name="biblioteka-serwera").start()
        elif st in ("denied", "expired"):
            self.server_enroll_cancel()
        return {"ok": True, "status": st, "account": r.get("account") or p["account"], "code": p["code"],
                "reason": r.get("reason", "")}

    def server_logout(self, force: bool = False) -> dict:
        """Ustawienia → Wyloguj z serwera: serwer unieważnia to urządzenie, klucz urządzenia
        i zapamiętane adresy znikają z tego komputera. Gry, zapisy i profile zostają.
        Najpierw zapisy: po ostatniej grze (wysyłanie w tle) i zaległe — gdy czegoś nie udało
        się wysłać, bez `force` nie wylogowuje (gracz wybiera: jeszcze raz / mimo to / anuluj)."""
        from emustart import keystore
        s = getattr(self, "_session", None)
        if s and s.phase in ("preparing", "downloading", "extracting", "running", "conflict"):
            return {"ok": False, "reason": "Trwa gra — wyloguj się po jej zakończeniu."}
        if self._cfg.get("server_key") and not force:
            launcher.wait_post(120)                     # zapisy po ostatniej grze
            try:
                syncclient.retry(self._cfg)
            except Exception:
                log.exception("zapisy przed wylogowaniem")
            left = syncclient.pending(self._cfg)
            if left:
                return {"ok": False, "ask": True, "pending": len(left), "games": [g["name"] for g in left[:8]],
                        "reason": f"Zapisy {len(left)} {'gry' if len(left) == 1 else 'gier'} nie doszły na serwer."}
        if self._cfg.get("server_key"):
            r = netsrc.authed_post(self._cfg, "/v1/auth/logout", {})
            if not r.get("ok"):
                log.info("wylogowanie na serwerze: %s", r.get("reason", ""))
        keystore.delete(self._cfg.get("server_keyname", ""), self._cfg.get("server_keykind", ""))
        for k in ("server_url", "server_key", "server_keyname", "server_keykind", "server_account", "server_fp"):
            self._cfg.pop(k, None)
        config.save(self._cfg)
        netsrc.forget_server()
        nasfs.reset()
        log.info("wylogowano z serwera")
        return {"ok": True}

    def _adopt_account(self, account: str) -> None:
        """Po zatwierdzeniu: konto z serwera jako profil tego komputera — zamiast pustego
        profilu „Gracz” z pierwszego uruchomienia (usuwany, jeśli nigdy nie był używany)."""
        try:
            profiles.import_from_nas(self._cfg)
            profiles.align_account_name(self._cfg)
            mine = next((p for p in profiles.all_profiles()
                         if (p.get("nas_name") or p["name"]).lower() == account.lower()), None)
            if not mine:
                return
            if profiles.setup_needed():
                for p in profiles.all_profiles():
                    if p["id"] != mine["id"] and profiles.unused_placeholder(p):
                        profiles.delete(p["id"])
                profiles.finish_setup(mine["id"], ask=False)
            self._profile = mine["id"]
            library.meta_set("last_profile", str(mine["id"]))
            self._user_load(self._profile)
            log.info("profil tego komputera: konto %s", account)
        except Exception:
            log.exception("konto z serwera jako profil")

    def server_enroll_cancel(self) -> dict:
        from emustart import keystore
        p = self._cfg.pop("enroll_pending", None)
        if p:
            keystore.delete(p.get("keyname", ""), p.get("keykind", ""))
            config.save(self._cfg)
        return {"ok": True}

    def enroll_pending(self) -> dict:
        """Prośby o dołączenie, które to urządzenie może zatwierdzić (pokazywane przy otwarciu)."""
        if not self._cfg.get("server_url") or not self._cfg.get("server_key"):
            return {"ok": True, "requests": []}
        r = netsrc.authed_post(self._cfg, "/v1/enroll/pending", {})
        return r if r.get("ok") else {"ok": True, "requests": []}

    def enroll_decide(self, req_id: str, allow: bool, games: bool = False) -> dict:
        return netsrc.authed_post(self._cfg, "/v1/enroll/decide", {"id": req_id, "allow": bool(allow),
                                                                  "games": bool(games)})

    def emulator_options(self, es: str) -> list:
        """Emulatory systemu znalezione na tym komputerze (wybór w Ustawieniach)."""
        return emulators.options_for(systems.info(es), self._cfg.get("emu_root", ""))

    def save_settings(self, data: dict) -> dict:
        cfg = self._cfg
        if "emu_root" in data:
            new_root = str(data["emu_root"]).strip()
            if new_root != cfg.get("emu_root"):
                # nowy folder emulatorów: brakujące i starsze emulatory z serwera (w tle)
                threading.Timer(1.0, self._emu_sync_quiet).start()
            cfg["emu_root"] = new_root
        if "cache_dir" in data:
            cd = str(data["cache_dir"]).strip()
            cfg["cache_dir"] = "" if Path(cd) == paths.DEFAULT_CACHE else cd
        if "cache_recent" in data:
            try:
                cfg["cache_recent"] = max(1, int(data["cache_recent"]))
            except (TypeError, ValueError):
                pass
        if data.get("pad_backend") in ("python", "browser", "none"):
            cfg["pad_backend"] = data["pad_backend"]
        for k in ("fullscreen", "hide_arcade_clones", "games_logo", *self.HIDE_KEYS):
            if k in data:
                cfg[k] = bool(data[k])
        if "stream_play" in data:
            cfg["stream_play"] = bool(data["stream_play"])
        if "stream_min_seconds" in data:
            try:
                cfg["stream_min_seconds"] = max(0, min(600, int(data["stream_min_seconds"])))
            except (TypeError, ValueError):
                pass
        if "profile_settings" in data:
            cfg["profile_settings"] = bool(data["profile_settings"])
        if data.get("look_scope") in ("profile", "machine"):
            cfg["look_scope"] = data["look_scope"]
        if "ask_profile" in data:
            library.meta_set("ask_profile", "1" if data["ask_profile"] else "0")
        if "machine_profile" in data and profiles.get(int(data["machine_profile"])):
            profiles.set_machine_owner(int(data["machine_profile"]))
        if any(k in data for k in self.USER_KEYS) or "look_scope" in data:
            self._user_save()
        if "systems" in data:
            cfg.setdefault("systems", {})
            for es, sc in data["systems"].items():
                cur = cfg["systems"].setdefault(es, {})
                for k in ("enabled", "label", "exe", "args"):
                    if k in sc:
                        cur[k] = sc[k]
                if sc.get("exe"):
                    self._record_choice(es, sc)
        config.save(cfg)
        return {"ok": True}

    def look_save(self, data: dict) -> dict:
        """Ustawienia wyglądu z edytora (web/look.js) — liczby i proste napisy."""
        self._cfg["look"] = clean_look(data)
        config.save(self._cfg)
        self._user_save()
        return {"ok": True}

    def autodetect(self) -> dict:
        """Domyślny emulator dla każdego systemu biblioteki, który jeszcze go nie ma."""
        cfg = self._cfg
        found_sys = {r["es"]: [] for r in library.systems_summary()}     # systemy z biblioteki serwera
        if not found_sys:
            return {"ok": False, "reason": "Biblioteka z serwera jest pusta — odśwież ją (Start → Odśwież bibliotekę z serwera)."}
        cfg.setdefault("systems", {})
        emulators.forget_scan()
        found = 0
        for es in sorted(found_sys):
            cur = cfg["systems"].setdefault(es, {"enabled": True})
            if cur.get("exe") and Path(cur["exe"]).is_file():
                continue
            opt = _recommended_option(es, cfg) or emulators.default_option(systems.info(es), cfg.get("emu_root", ""))
            if opt:
                cur.update(label=opt["label"], exe=opt["exe"], args=opt["args"])
                found += 1
        config.save(cfg)
        return {"ok": True, "assigned": found}

    def browse(self, path: str = "") -> dict:
        """Przeglądarka folderów dla pada: dyski albo podfoldery `path`.
        Przy folderze liczba rozpoznanych systemów (pomaga trafić w kolekcję)."""
        import os
        if not path:
            drives = []
            for d in os.listdrives():
                label = winutil.volume_label(d)
                kind = winutil.drive_kind(d)
                drives.append({"name": d.rstrip("\\") + (f"  {label}" if label else ""),
                               "path": d, "info": kind})
            return {"path": "", "parent": None, "entries": drives, "systems": 0}
        p = Path(path)
        try:
            subs = sorted((e for e in os.scandir(p) if e.is_dir(follow_symlinks=True)
                           and not e.name.startswith(("$", "."))
                           and e.name not in ("System Volume Information",)),
                          key=lambda e: e.name.lower())
        except OSError as ex:
            return {"path": str(p), "parent": str(p.parent) if p.parent != p else "",
                    "entries": [], "systems": 0, "error": str(ex)}
        entries = []
        recognized = 0
        for e in subs:
            es = systems.match_folder(e.name)
            if es:
                recognized += 1
            entries.append({"name": e.name, "path": e.path,
                            "info": systems.info(es)["display"] if es else ""})
        parent = "" if p.parent == p else str(p.parent)
        return {"path": str(p), "parent": parent, "entries": entries, "systems": recognized}

    def pick_folder(self, start: str = "") -> str:
        if not self._window:
            return self._pick_folder_dialog(start)    # interfejs w przeglądarce
        import webview
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                              directory=start or "")
        return res[0] if res else ""

    @staticmethod
    def _pick_folder_dialog(start: str = "") -> str:
        """Okno wyboru folderu Windows bez okna EmuStart (tryb przeglądarki)."""
        import os
        import subprocess
        ps = ("Add-Type -AssemblyName System.Windows.Forms;"
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description = 'EmuStart - wybierz folder';"
              "$d.SelectedPath = $env:EMUSTART_START;"
              "$f = New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
              "if ($d.ShowDialog($f) -eq 'OK') { [Console]::Out.Write($d.SelectedPath) }")
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True,
                               text=True, timeout=600, env={**os.environ, "EMUSTART_START": start or ""},
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return r.stdout.strip()
        except Exception as ex:
            log.warning("okno wyboru folderu: %s", ex)
            return ""

    def post_status(self) -> str:
        """Tytuł gry, której save'y idą właśnie na NAS ('' = nic)."""
        return launcher.post_busy()

    def _shutdown(self) -> None:
        """Przerywa zadania w tle przed wyjściem z programu (grafiki, pobieranie
        emulatorów, kopiowanie do cache). Część z nich (pula wątków grafik)
        zatrzymywałaby zamknięcie procesu aż do końca pracy — nawet godzinami."""
        for job in (getattr(self, "_install_job", None),):
            if job:
                job.cancel.set()
        for j in self._pin_jobs.values():
            j["cancel"].set()
        for s in self._background:
            s.cancel.set()
        if self._session:
            self._session.cancel.set()
        try:
            from emustart import vfs
            vfs.shutdown()                # dysk strumieniowy (WinFsp)
        except Exception:
            log.exception("dysk strumieniowy")
        # save'y ostatniej gry muszą dojść na NAS (inaczej zostaną „do wysłania”)
        if launcher.post_busy() and not launcher.wait_post(timeout=120):
            log.warning("zamknięcie w trakcie wysyłania save'ów — dokończy następne uruchomienie")

    def quit(self) -> None:
        if self._session:
            self._session.kill()
        if self._window:
            self._window.destroy()
        elif getattr(self, "browser_mode", False):
            # interfejs w przeglądarce (okno się nie uruchomiło) — koniec procesu
            import os
            import threading
            def bye():
                self._shutdown()
                os._exit(0)
            threading.Timer(0.3, bye).start()

    def now(self) -> float:
        return time.time()


_LOOK_KEY = re.compile(r"^[a-z0-9_]{1,24}$")
_LOOK_STR = re.compile(r"^(#[0-9a-fA-F]{6}|[a-z0-9_-]{1,20})$")


def clean_look(data) -> dict:
    """Odrzuca wszystko poza {klucz: liczba | kolor #rrggbb | słowo}."""
    out = {}
    if not isinstance(data, dict):
        return out
    for k, v in list(data.items())[:64]:
        if not isinstance(k, str) or not _LOOK_KEY.match(k):
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)) and math.isfinite(v) and abs(v) < 10000:
            out[k] = v
        elif isinstance(v, str) and _LOOK_STR.match(v):
            out[k] = v
    return out


def _quiet(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:
        log.exception("%s", getattr(fn, "__name__", "zadanie"))


def _recommended_option(es: str, cfg: dict) -> dict:
    """Emulator zalecany przez serwer dla systemu (ten sam co na komputerze administratora —
    stany zgodne): RetroArch z tym rdzeniem albo pierwszy samodzielny emulator."""
    from emustart import libsync
    rec = libsync.recommended(es)
    if not rec.get("emu"):
        return {}
    opts = emulators.options_for(systems.info(es), cfg.get("emu_root", ""))
    if rec["emu"] == "retroarch" and rec.get("core"):
        want = f"{rec['core']}_libretro".lower()
        return next((o for o in opts if Path(o["exe"]).stem.lower() == "retroarch"
                     and want in (o.get("args") or "").lower()), {})
    if rec["emu"] == "standalone":
        return next((o for o in opts if Path(o["exe"]).stem.lower() != "retroarch"), {})
    return {}
