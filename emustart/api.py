"""api — metody wywoływane z UI (pywebview js_api).

Wszystkie zwracają zwykłe dicty/listy (JSON). Długie operacje (skan, start gry,
pobieranie przypiętych) działają w wątkach; UI odpytuje ich stan.
"""

from __future__ import annotations

import collections
import json
import logging
import math
import re
import threading
import urllib.error
import time
from pathlib import Path

from emustart import (__version__, art, art_sources, cache, ingame, installer, launchbox, logos, sysinfo, metadata, pads, profiles, uipad, config, emulators, launcher, library,
                      paths, scanner, systems, winutil)

log = logging.getLogger("emustart.api")

_REGIONS = {"USA": "USA", "Europe": "Europa", "Japan": "Japonia", "World": "Świat",
            "Germany": "Niemcy", "France": "Francja", "Spain": "Hiszpania", "Italy": "Włochy",
            "UK": "Wielka Brytania", "Australia": "Australia", "Korea": "Korea", "Brazil": "Brazylia",
            "Canada": "Kanada", "Asia": "Azja", "China": "Chiny", "Taiwan": "Tajwan",
            "Netherlands": "Holandia", "Sweden": "Szwecja", "Poland": "Polska"}


def _regions(tags: str) -> list:
    """'(USA, Europe) (Rev 1)' → ['USA', 'Europa'] (tylko znane nazwy regionów)."""
    out = []
    for group in re.findall(r"\(([^)]*)\)", tags or ""):
        parts = [p.strip() for p in group.split(",")]
        if parts and all(p in _REGIONS for p in parts):
            out += [_REGIONS[p] for p in parts]
    return out


def _players(v) -> int:
    """'2', '1-4', '4' → największa liczba graczy (0 = brak danych)."""
    nums = [int(n) for n in re.findall(r"\d+", str(v or ""))]
    return max(nums) if nums else 0


class Api:
    def __init__(self):
        self._cfg = config.load()
        self._window = None
        self._fetcher = art.Fetcher(logos=lambda: bool(self._cfg.get("games_logo")))
        self._session: launcher.Session | None = None
        self._background: list = []          # sesje, które jeszcze kopiują w tle
        self._pin_jobs: dict = {}            # game_id → Session-like (kopiowanie przypiętych)
        self._scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}
        ingame.restore_pending()          # ustawienia padów po ewentualnej awarii
        profiles.adopt_existing_install()  # aktualizacja: komputer już ma swój profil
        self._uipad = uipad.UiPad(self._uipad_active) if self.pad_backend() == "python" else None
        self._profile = self._initial_profile()
        threading.Thread(target=lambda: profiles.sync_pending(self._cfg), daemon=True,
                         name="sync-pending").start()
        threading.Thread(target=self._profiles_bootstrap, daemon=True, name="profiles").start()
        threading.Thread(target=self._arcade_meta, daemon=True, name="arcade-meta").start()
        threading.Thread(target=self._platforms_meta, daemon=True, name="platforms").start()
        # Menu w grze: stan czyta UI (ingame_poll), Python niczego nie wywołuje
        # w oknie. evaluate_js przy grze na pełnym ekranie potrafiło czekać 20 s
        # i blokowało w tym czasie wszystkie wywołania z UI.
        self._menu = {"open": False, "items": [], "message": "", "title": "", "seq": 0}
        self._menu_inputs: collections.deque = collections.deque()
        self._menu_lock = threading.Lock()

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
        for arg in sys.argv:
            if arg.startswith("--pady="):
                v = arg.split("=", 1)[1].lower()
                return {"python": "python", "przegladarka": "browser", "browser": "browser",
                        "brak": "none", "none": "none"}.get(v, "python")
        v = self._cfg.get("pad_backend", "python")
        return v if v in ("python", "browser", "none") else "python"

    def _arcade_meta(self) -> None:
        """Rok/producent/gracze/gatunek setów arcade (filtry) — raz, w tle."""
        from emustart import arcade
        try:
            if arcade.needs_refresh() and arcade.find_mame(self._cfg):
                log.info("arcade: %s", arcade.refresh(self._cfg))
        except Exception:
            log.exception("arcade meta")

    def _platforms_meta(self) -> None:
        """Dane platform LaunchBoksa (Platforms.xml, ~75 KB) — dla baz zbudowanych
        przed 0.10, które ich nie mają; potem opisy platform dla karuzeli."""
        try:
            if launchbox.ready() and launchbox.platform("psx") is None:
                log.info("platformy LaunchBox: %s", launchbox.fetch_platforms())
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

    # ── profile: rzeczy per gracz (EmuStart, RetroAchievements) ──
    USER_KEYS = ("look", "games_logo", "hide_arcade_clones")

    def _profiles_bootstrap(self) -> None:
        """W tle przy starcie: profile z NAS (czysta instalacja), konto RA
        zalogowane w emulatorach → profil tego komputera, ustawienia EmuStart profilu."""
        try:
            profiles.import_from_nas(self._cfg)
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
        profiles.finish_setup(int(pid), bool(ask))
        self.profile_select(int(pid))
        threading.Thread(target=self._ra_adopt, daemon=True).start()
        return {"ok": True}

    def _look_shared(self) -> bool:
        return self._cfg.get("look_scope", "profile") != "machine"

    def _user_load(self, pid: int) -> None:
        """Ustawienia EmuStart profilu (wygląd itp.); profil bez nich dziedziczy bieżące."""
        data = profiles.json_get(self._cfg, pid, "emustart.json")
        if not data:
            return
        changed = False
        for k in self.USER_KEYS:
            if k == "look" and not self._look_shared():
                continue                      # wygląd tego komputera
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

    # ── stan ogólny ──
    def get_state(self) -> dict:
        cfg = self._cfg
        rom_ok = any(Path(r).is_dir() for r in config.rom_roots(cfg))
        return {
            "version": __version__,
            "configured": config.is_configured(cfg),
            "rom_roots": config.rom_roots(cfg), "rom_online": rom_ok,
            "network_mode": cfg.get("network_mode", "auto"),
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

    HIDE_KEYS = {"hide_beta": "beta", "hide_demo": "demo", "hide_pirate": "pirate",
                 "hide_unl": "unl", "hide_program": "program"}

    def _hide_groups(self) -> list:
        return [g for k, g in self.HIDE_KEYS.items() if self._cfg.get(k)]

    def list_systems(self) -> list:
        out = []
        summary = library.systems_summary()
        logos.fetch_missing(self._cfg, [s["es"] for s in summary])
        groups = self._hide_groups()
        clones = [s["es"] for s in summary if systems.info(s["es"])["kind"] == "arcade"] \
            if self._cfg.get("hide_arcade_clones", True) else []
        counts = library.visible_counts(groups, clones) if groups or clones else None
        for s in summary:
            if counts is not None:
                s["games"] = counts.get(s["es"], 0)
            info = systems.info(s["es"])
            emu = (self._cfg.get("systems") or {}).get(s["es"]) or {}
            if not emu.get("enabled", True) or not s["games"]:
                continue
            lg = logos.url_for(s["es"], self._cfg)
            out.append({"es": s["es"], "display": emu.get("name") or s["display"], "games": s["games"],
                        "cached": s["cached"], "logo": lg["url"], "logo_glow": lg["glow"],
                        "emulator": emu.get("label", ""), "kind": info["kind"]})
        return out

    def list_games(self, es: str) -> list:
        hide = self._cfg.get("hide_arcade_clones", True) and systems.info(es)["kind"] == "arcade"
        rows = library.list_games(es, self._profile, hide)
        groups = self._hide_groups()
        if groups:
            rows = [r for r in rows if not library.is_hidden(r["name"], groups)]
        try:
            metadata.prepare_system(self._cfg, es)     # dane do filtrów (rdb, offline)
        except Exception:
            log.exception("metadane systemu %s", es)
        fields = metadata.system_fields(es)
        for r in rows:
            f = fields.get(r["id"], {})
            if f.get("title"):
                r["title"] = f["title"]
            r["genre"], r["year"] = f.get("genre", ""), str(f.get("year", ""))[:4]
            r["players"] = _players(f.get("players", ""))
            r["developer"], r["publisher"] = f.get("developer", ""), f.get("publisher", "")
            r["regions"] = _regions(r["tags"])
            r["logo"] = art.media_url(es, r["name"], "logo") if r["art_logo"] == art.HAS else ""
            r["box"] = art.media_url(es, r["name"], "box") if r["art_box"] == art.HAS else ""
            r["snap"] = art.media_url(es, r["name"], "snap") if r["art_snap"] == art.HAS else ""
        return rows

    def request_art(self, game_ids: list) -> None:
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

    def game_detail(self, game_id: int) -> dict:
        g = library.game(int(game_id))
        if not g:
            return {}
        info = systems.info(g["es"])
        row = library.db().execute(
            "SELECT * FROM cache WHERE game_id=?", (g["id"],)).fetchone()
        play = library.db().execute(
            "SELECT * FROM play WHERE game_id=? AND profile_id=?",
            (g["id"], self._profile)).fetchone()
        meta = metadata.ensure_local(self._cfg, g)
        meta = {k: v for k, v in meta.items() if not k.startswith("_")}
        if meta.get("genre"):
            meta["genre"] = metadata.norm_genres(meta["genre"])
        return {"id": g["id"], "title": meta.get("title") or g["title"], "tags": g["tags"],
                "name": g["name"], "meta": meta,
                "meta_online": bool(metadata.get(g["id"]).get("_online")),
                "meta_edits": metadata.get(g["id"]).get("_edits", {}),
                "system": info["display"], "size": g["size"], "files": len(g["files"]),
                "file": g["rel"], "cached": bool(row and row["complete"]),
                "pinned": bool(row and row["pinned"]),
                "plays": play["plays"] if play else 0,
                "seconds": play["seconds"] if play else 0,
                "last": play["last"] if play else 0,
                "copying": g["id"] in self._copying()}

    # ── uruchamianie ──
    def launch(self, game_id: int, state: str = "") -> dict:
        if self._session and self._session.phase in ("preparing", "downloading",
                                                     "extracting", "running"):
            return {"ok": False, "reason": "Inna gra jest właśnie uruchamiana."}
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
                                         start_state=state or "")
        self._session.start()
        return {"ok": True}

    def launch_status(self) -> dict:
        return self._session.status() if self._session else {"phase": "idle"}

    def launch_cancel(self) -> None:
        if self._session:
            self._session.abort()

    def launch_play_now(self) -> None:
        if self._session:
            self._session.request_play_now()

    def launch_dismiss(self) -> None:
        s = self._session
        if s and s.phase in ("finished", "error", "cancelled"):
            self._session = None

    def _on_running(self, s) -> None:
        """Okno EmuStart zostaje pod spodem; pilnujemy tylko, żeby emulator
        dostał fokus, gdy pokaże swoje okno."""
        def focus():
            end = time.monotonic() + 15
            while time.monotonic() < end and s.proc and s.proc.poll() is None:
                wins = winutil.windows_of(s.proc.pid)
                if wins:
                    winutil.bring_to_front(wins[0])
                    return
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

    def _start_pin_copy(self, g: dict) -> None:
        rom_dir = library.game_dir(g)
        if not (rom_dir / g["rel"]).exists():
            return    # offline — dociągniemy przy następnym uruchomieniu gry
        prog = cache.Progress(cache.missing_bytes(self._cfg, g), len(g["files"]))
        cancel = threading.Event()

        def run():
            try:
                cache.copy_game(self._cfg, g, rom_dir, prog, cancel)
            except cache.Cancelled:
                pass
            except Exception:
                log.exception("pin copy")
        t = threading.Thread(target=run, daemon=True, name="pin-copy")
        self._pin_jobs[g["id"]] = {"thread": t, "prog": prog, "cancel": cancel,
                                   "title": g["title"]}
        t.start()

    # ── opcje gry (przytrzymane A) ──
    def _effective_emu(self, g: dict) -> dict:
        own = library.db().execute("SELECT * FROM game_emu WHERE game_id=?", (g["id"],)).fetchone()
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
        states = []
        if emu["exe"]:
            ad = ingame.adapter_for(emu["exe"])
            meta = metadata.ensure_local(self._cfg, g)
            learned = [r["prefix"] for r in library.db().execute(
                "SELECT prefix FROM states WHERE game_id=? AND family=?", (g["id"], ad.family))]
            states = ingame.list_states(ad, g, meta, learned)
            launcher.resume_from_nas(self._cfg, self._profile, g)
            res = library.get_resume(self._profile, g["id"])
            if res and res["path"] and Path(res["path"]).is_file() and res["family"] == ad.family:
                states.insert(0, {"path": res["path"], "name": "Quicksave EmuStart",
                                  "time": res["created"], "resume": True})
        return {"id": g["id"], "title": g["title"], "emulator": emu, "options": opts,
                "states": states[:30]}

    def set_game_emulator(self, game_id: int, opt: dict | None) -> dict:
        with library.db() as c:
            if not opt:
                c.execute("DELETE FROM game_emu WHERE game_id=?", (int(game_id),))
            else:
                c.execute("INSERT OR REPLACE INTO game_emu VALUES(?,?,?,?)",
                          (int(game_id), opt["label"], opt["exe"], opt.get("args", "")))
        return {"ok": True}

    def meta_fetch(self, game_id: int, full: bool = False) -> dict:
        """Opisy z sieci w tle; UI odpytuje game_detail. `full` = także TheGamesDB
        (limit miesięczny — tylko na wyraźne żądanie z opcji gry)."""
        g = library.game(int(game_id))
        if not g:
            return {"ok": False}

        def run():
            try:
                metadata.fetch_online(self._cfg, g, tgdb=bool(full))
            except Exception:
                log.exception("metadane %s", g["name"])
        threading.Thread(target=run, daemon=True, name="meta").start()
        return {"ok": True}

    def meta_save(self, game_id: int, edits: dict) -> dict:
        metadata.set_edits(int(game_id), edits or {})
        return {"ok": True}

    def art_candidates(self, game_id: int, kind: str, query: str = "") -> list:
        g = library.game(int(game_id))
        if not g or kind not in ("box", "snap", "logo"):
            return []
        setname = Path(g["rel"]).stem if systems.info(g["es"])["kind"] == "arcade" else ""
        return art_sources.candidates(self._cfg, g["es"], g["name"], kind, query or "", setname)

    def art_choose(self, game_id: int, kind: str, url: str) -> dict:
        g = library.game(int(game_id))
        data = art_sources.fetch(url) if g else None
        if not art_sources.is_image(data):
            return {"ok": False, "reason": "Nie udało się pobrać tej grafiki."}
        art.save(g["es"], g["name"], kind, data)
        library.set_art(g["id"], kind, art.HAS)
        return {"ok": True, "url": art.media_url(g["es"], g["name"], kind) + f"?t={int(time.time())}"}

    def art_clear(self, game_id: int, kind: str) -> dict:
        g = library.game(int(game_id))
        if g:
            art.media_path(g["es"], g["name"], kind).unlink(missing_ok=True)
            library.set_art(g["id"], kind, art.MISSING)
        return {"ok": True}

    # ── opcje systemu (przytrzymane A na logo) ──
    def system_options(self, es: str) -> dict:
        info = systems.info(es)
        sc = (self._cfg.get("systems") or {}).get(es) or {}
        lg = logos.url_for(es, self._cfg)
        has_emu = bool(sc.get("exe") and Path(sc["exe"]).is_file())
        return {"es": es, "display": sc.get("name") or info["display"],
                "default_name": info["display"], "logo": lg["url"], "glow": lg["glow"],
                "logo_choice": sc.get("logo", ""), "emulator": sc.get("label", "") if has_emu else "",
                "options": emulators.options_for(info, self._cfg.get("emu_root", "")),
                "install": installer.plan_for(es, self._cfg.get("emu_root", "")) if not has_emu else []}

    def system_set(self, es: str, data: dict) -> dict:
        sc = self._cfg.setdefault("systems", {}).setdefault(es, {})
        if "name" in data:
            name = str(data["name"] or "").strip()
            if name and name != systems.info(es)["display"]:
                sc["name"] = name
            else:
                sc.pop("name", None)
        if "glow" in data:
            sc["logo_glow"] = bool(data["glow"])
        if "enabled" in data:
            sc["enabled"] = bool(data["enabled"])
        if data.get("emulator"):
            o = data["emulator"]
            sc.update(label=o["label"], exe=o["exe"], args=o.get("args", ""), enabled=True)
        config.save(self._cfg)
        return {"ok": True}

    def system_logo_candidates(self, es: str) -> list:
        return logos.candidates(self._cfg, es)

    def system_logo_choose(self, es: str, cid: str) -> dict:
        ok = logos.choose(self._cfg, es, cid)
        if ok:
            sc = self._cfg["systems"][es]
            sc.setdefault("logo_glow", False)
            config.save(self._cfg)
        return {"ok": ok, **logos.url_for(es, self._cfg)} if ok else             {"ok": False, "reason": "Nie udało się pobrać tego logo."}

    def system_rescan(self, es: str) -> dict:
        """Ponowny skan jednego systemu (w tle)."""
        found, _u = scanner.collect_systems(self._cfg)
        dirs = found.get(es)
        if not dirs:
            return {"ok": False, "reason": "Folder systemu jest niedostępny."}

        def run():
            try:
                scanner.scan_system(es, dirs)
            except Exception:
                log.exception("skan %s", es)
        threading.Thread(target=run, daemon=True, name="scan-one").start()
        return {"ok": True}

    # ── pobieranie emulatorów ──
    def install_missing(self) -> list:
        """Systemy w bibliotece bez emulatora i co dla nich pobrać."""
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
        return {"profiles": profiles.all_profiles(), "current": self._profile}

    def profile_select(self, pid: int) -> dict:
        if not profiles.get(int(pid)):
            return {"ok": False}
        self._profile = int(pid)
        library.meta_set("last_profile", str(self._profile))
        self._user_load(self._profile)
        return {"ok": True}

    def profile_create(self, name: str) -> dict:
        try:
            p = profiles.create(name)
        except ValueError as ex:
            return {"ok": False, "reason": str(ex)}
        return {"ok": True, "profile": p}

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
    def art_overview(self) -> dict:
        rows = library.db().execute("""
            SELECT es, COUNT(*) n, SUM(art_box=1) box, SUM(art_snap=1) snap,
                   SUM(art_box=0 OR art_snap=0) unchecked
            FROM games WHERE hidden=0 GROUP BY es""").fetchall()
        names = {s["es"]: s["display"] for s in library.systems_summary()}
        systems_ = sorted(({"es": r["es"], "display": names.get(r["es"], r["es"]),
                            "games": r["n"], "box": r["box"] or 0, "snap": r["snap"] or 0,
                            "unchecked": r["unchecked"] or 0} for r in rows),
                          key=lambda x: x["display"].lower())
        keys = self._cfg.get("art_keys") or {}
        desc = dict(library.db().execute("""
            SELECT g.es, SUM(m.data LIKE '%"description"%' OR m.data LIKE '%"wiki"%')
            FROM games g LEFT JOIN game_meta m ON m.game_id=g.id WHERE g.hidden=0 GROUP BY g.es""").fetchall())
        logo_n = dict(library.db().execute(
            "SELECT es, SUM(art_logo=1) FROM games WHERE hidden=0 GROUP BY es").fetchall())
        for s_ in systems_:
            s_["desc"] = desc.get(s_["es"]) or 0
            s_["logo"] = logo_n.get(s_["es"]) or 0
        upd = getattr(self, "_lb_update", None)
        return {"systems": systems_, "sources": art_sources.Sources(self._cfg).enabled(),
                "launchbox": launchbox.status(), "games_logo": bool(self._cfg.get("games_logo")),
                "lb_update": upd.status() if upd else None,
                "keys_from": keys.get("source", ""),
                "job": self._art_job.status() if getattr(self, "_art_job", None) else None}

    def art_start(self, es: str = "", mode: str = "art") -> dict:
        """mode: art | meta | meta_wiki | all (grafiki + metadane) | shrink (zmniejsz zapisane)."""
        job = getattr(self, "_art_job", None)
        if job and not job.finished:
            return {"ok": False, "reason": "Pobieranie już trwa."}
        if mode != "shrink":
            try:
                art.check_space()
            except art.DiskFull as ex:
                return {"ok": False, "reason": str(ex)}
        self._art_job = art.Job(self._cfg, es or None, art=mode in ("art", "all"),
                                meta=mode in ("meta", "meta_wiki", "all"), wiki=mode == "meta_wiki",
                                shrink=mode == "shrink")
        self._art_job.start()
        return {"ok": True}

    def art_status(self) -> dict | None:
        job = getattr(self, "_art_job", None)
        return job.status() if job else None

    def art_cancel(self) -> None:
        job = getattr(self, "_art_job", None)
        if job:
            job.cancel.set()

    def launchbox_update(self) -> dict:
        upd = getattr(self, "_lb_update", None)
        if upd and not upd.finished:
            return {"ok": False, "reason": "Pobieranie bazy LaunchBox już trwa."}
        self._lb_update = launchbox.Updater()
        self._lb_update.start()
        return {"ok": True}

    def launchbox_status(self) -> dict:
        upd = getattr(self, "_lb_update", None)
        return {"db": launchbox.status(), "update": upd.status() if upd else None}

    def import_pylinks_keys(self) -> dict:
        keys = art_sources.import_pylinks_keys()
        if not keys:
            return {"ok": False, "reason": "Nie znaleziono config.json PyLinksWeb."}
        self._cfg["art_keys"] = keys
        config.save(self._cfg)
        return {"ok": True, "sources": art_sources.Sources(self._cfg).enabled(),
                "from": keys["source"]}

    # ── skan ──
    def rescan(self) -> dict:
        if self._scan["running"]:
            return {"ok": False}
        self._scan.update(running=True, text="Start…", done=0, total=0, result=None)

        def prog(text, done, total):
            self._scan.update(text=text, done=done, total=total)

        def run():
            try:
                self._scan["result"] = scanner.scan_all(self._cfg, prog)
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
        found, unknown = scanner.collect_systems(cfg)
        rows = []
        for es in sorted(found, key=lambda e: systems.info(e)["display"].lower()):
            info = systems.info(es)
            sc = (cfg.get("systems") or {}).get(es) or {}
            known = es in systems.SYSTEMS
            rows.append({"es": es, "display": info["display"], "known": known,
                         "enabled": sc.get("enabled", True if known else False),
                         "folders": len(found[es]),
                         "label": sc.get("label", ""), "exe": sc.get("exe", ""),
                         "args": sc.get("args", "")})
        for d in unknown:
            if d.name in found:
                continue
            rows.append({"es": d.name, "display": d.name, "known": False, "enabled": False,
                         "folders": 1, "label": "", "exe": "", "args": ""})
        return {"rom_roots": config.rom_roots(cfg), "emu_root": cfg.get("emu_root", ""),
                "cache_dir": str(config.cache_dir(cfg)),
                "cache_recent": cfg.get("cache_recent", 10),
                "network_mode": cfg.get("network_mode", "auto"),
                "lan_threshold_mbps": cfg.get("lan_threshold_mbps", 200),
                "fullscreen": cfg.get("fullscreen", True),
                "hide_arcade_clones": cfg.get("hide_arcade_clones", True),
                "games_logo": cfg.get("games_logo", False),
                "pad_backend": cfg.get("pad_backend", "python"),
                "profile_settings": cfg.get("profile_settings", True),
                **{k: bool(cfg.get(k)) for k in self.HIDE_KEYS},
                "ask_profile": profiles.ask_at_start(),
                "machine_profile": profiles.machine_owner(),
                "profile_names": {str(p["id"]): p["name"] for p in profiles.all_profiles()},
                "systems": rows}

    def emulator_options(self, es: str) -> list:
        return emulators.options_for(systems.info(es), self._cfg.get("emu_root", ""))

    def save_settings(self, data: dict) -> dict:
        cfg = self._cfg
        for k in ("emu_root", "network_mode"):
            if k in data:
                cfg[k] = str(data[k]).strip()
        if "rom_roots" in data:
            cfg["rom_roots"] = [str(r).strip() for r in data["rom_roots"] if str(r).strip()]
            cfg["rom_root"] = cfg["rom_roots"][0] if cfg["rom_roots"] else ""
        if "cache_dir" in data:
            cd = str(data["cache_dir"]).strip()
            cfg["cache_dir"] = "" if Path(cd) == paths.DEFAULT_CACHE else cd
        for k in ("cache_recent", "lan_threshold_mbps"):
            if k in data:
                try:
                    cfg[k] = max(1, int(data[k]))
                except (TypeError, ValueError):
                    pass
        if data.get("pad_backend") in ("python", "browser", "none"):
            cfg["pad_backend"] = data["pad_backend"]
        for k in ("fullscreen", "hide_arcade_clones", "games_logo", *self.HIDE_KEYS):
            if k in data:
                cfg[k] = bool(data[k])
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
        config.save(cfg)
        return {"ok": True}

    def look_save(self, data: dict) -> dict:
        """Ustawienia wyglądu z edytora (web/look.js) — liczby i proste napisy."""
        self._cfg["look"] = clean_look(data)
        config.save(self._cfg)
        self._user_save()
        return {"ok": True}

    def autodetect(self) -> dict:
        """Domyślny emulator dla każdego folderu, który jeszcze go nie ma."""
        cfg = self._cfg
        found_sys, _unknown = scanner.collect_systems(cfg)
        if not found_sys:
            return {"ok": False, "reason": "Brak dostępnych folderów z grami: "
                    + ", ".join(config.rom_roots(cfg))}
        cfg.setdefault("systems", {})
        emulators.forget_scan()
        found = 0
        for es in sorted(found_sys):
            cur = cfg["systems"].setdefault(es, {"enabled": True})
            if cur.get("exe") and Path(cur["exe"]).is_file():
                continue
            opt = emulators.default_option(systems.info(es), cfg.get("emu_root", ""))
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
        import webview
        if not self._window:
            return ""
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                              directory=start or "")
        return res[0] if res else ""

    def _shutdown(self) -> None:
        """Przerywa zadania w tle przed wyjściem z programu (grafiki, pobieranie
        emulatorów, kopiowanie do cache). Część z nich (pula wątków grafik)
        zatrzymywałaby zamknięcie procesu aż do końca pracy — nawet godzinami."""
        for job in (getattr(self, "_art_job", None), getattr(self, "_install_job", None),
                    getattr(self, "_lb_update", None)):
            if job:
                job.cancel.set()
        for j in self._pin_jobs.values():
            j["cancel"].set()
        for s in self._background:
            s.cancel.set()
        if self._session:
            self._session.cancel.set()

    def quit(self) -> None:
        if self._session:
            self._session.kill()
        if self._window:
            self._window.destroy()

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
