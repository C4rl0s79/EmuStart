"""api — metody wywoływane z UI (pywebview js_api).

Wszystkie zwracają zwykłe dicty/listy (JSON). Długie operacje (skan, start gry,
pobieranie przypiętych) działają w wątkach; UI odpytuje ich stan.
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
from pathlib import Path

from emustart import (__version__, art, art_sources, cache, ingame, installer, logos, metadata, pads, profiles, uipad, config, emulators, launcher, library,
                      paths, scanner, systems, winutil)

log = logging.getLogger("emustart.api")

class Api:
    def __init__(self):
        self._cfg = config.load()
        self._window = None
        self._fetcher = art.Fetcher()
        self._session: launcher.Session | None = None
        self._background: list = []          # sesje, które jeszcze kopiują w tle
        self._pin_jobs: dict = {}            # game_id → Session-like (kopiowanie przypiętych)
        self._scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}
        ingame.restore_pending()          # ustawienia padów po ewentualnej awarii
        self._uipad = uipad.UiPad(self._uipad_active)
        self._profile = self._initial_profile()
        threading.Thread(target=lambda: profiles.sync_pending(self._cfg), daemon=True,
                         name="sync-pending").start()
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
        return bool(hwnd) and winutil.foreground() == hwnd

    def ui_pad_poll(self) -> list:
        """Zdarzenia padów XInput dla UI: [{a, up}]."""
        return self._uipad.poll()

    def _initial_profile(self) -> int:
        ids = [p["id"] for p in profiles.all_profiles()]
        try:
            last = int(library.meta_get("last_profile", "0"))
        except ValueError:
            last = 0
        return last if last in ids else ids[0]

    def attach(self, window) -> None:
        self._window = window

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
            "py_pad": bool(self._window) and uipad.xinput.available(),
            "profile": profiles.get(self._profile),
            "profiles": len(profiles.all_profiles()),
        }

    def list_systems(self) -> list:
        out = []
        logos.fetch_missing(self._cfg, [s["es"] for s in library.systems_summary()])
        for s in library.systems_summary():
            info = systems.info(s["es"])
            emu = (self._cfg.get("systems") or {}).get(s["es"]) or {}
            if not emu.get("enabled", True) or not s["games"]:
                continue
            lg = logos.url_for(s["es"])
            out.append({"es": s["es"], "display": s["display"], "games": s["games"],
                        "cached": s["cached"], "logo": lg["url"], "logo_glow": lg["glow"],
                        "emulator": emu.get("label", ""), "kind": info["kind"]})
        return out

    def list_games(self, es: str) -> list:
        hide = self._cfg.get("hide_arcade_clones", True) and systems.info(es)["kind"] == "arcade"
        rows = library.list_games(es, self._profile, hide)
        titles = {r["game_id"]: json.loads(r["edits"]).get("title")
                  for r in library.db().execute(
                      "SELECT game_id, edits FROM game_meta WHERE edits LIKE '%\"title\"%'")}
        for r in rows:
            if titles.get(r["id"]):
                r["title"] = titles[r["id"]]
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
            r = con.execute("SELECT es,name,art_box,art_snap FROM games WHERE id=?",
                            (int(gid),)).fetchone()
            if r:
                out[str(gid)] = {
                    "box": art.media_url(r["es"], r["name"], "box") if r["art_box"] == art.HAS else "",
                    "snap": art.media_url(r["es"], r["name"], "snap") if r["art_snap"] == art.HAS else "",
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
        if not g or kind not in ("box", "snap"):
            return []
        return art_sources.candidates(self._cfg, g["es"], g["name"], kind, query or "")

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
    def pads_state(self) -> dict:
        from emustart import xinput
        lst = pads.connected()
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
        return {"profiles": profiles.all_profiles(), "current": self._profile}

    def profile_select(self, pid: int) -> dict:
        if not profiles.get(int(pid)):
            return {"ok": False}
        self._profile = int(pid)
        library.meta_set("last_profile", str(self._profile))
        return {"ok": True}

    def profile_create(self, name: str) -> dict:
        try:
            p = profiles.create(name)
        except ValueError as ex:
            return {"ok": False, "reason": str(ex)}
        return {"ok": True, "profile": p}

    def profile_rename(self, pid: int, name: str) -> dict:
        try:
            profiles.rename(int(pid), name)
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
        return {"systems": systems_, "sources": art_sources.Sources(self._cfg).enabled(),
                "keys_from": keys.get("source", ""),
                "job": self._art_job.status() if getattr(self, "_art_job", None) else None}

    def art_start(self, es: str = "") -> dict:
        job = getattr(self, "_art_job", None)
        if job and not job.finished:
            return {"ok": False, "reason": "Pobieranie grafik już trwa."}
        self._art_job = art.Job(self._cfg, es or None)
        self._art_job.start()
        return {"ok": True}

    def art_status(self) -> dict | None:
        job = getattr(self, "_art_job", None)
        return job.status() if job else None

    def art_cancel(self) -> None:
        job = getattr(self, "_art_job", None)
        if job:
            job.cancel.set()

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
        for k in ("fullscreen", "hide_arcade_clones"):
            if k in data:
                cfg[k] = bool(data[k])
        if "systems" in data:
            cfg.setdefault("systems", {})
            for es, sc in data["systems"].items():
                cur = cfg["systems"].setdefault(es, {})
                for k in ("enabled", "label", "exe", "args"):
                    if k in sc:
                        cur[k] = sc[k]
        config.save(cfg)
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

    def pick_folder(self, start: str = "") -> str:
        import webview
        if not self._window:
            return ""
        res = self._window.create_file_dialog(webview.FileDialog.FOLDER,
                                              directory=start or "")
        return res[0] if res else ""

    def quit(self) -> None:
        if self._session:
            self._session.kill()
        if self._window:
            self._window.destroy()

    def now(self) -> float:
        return time.time()
