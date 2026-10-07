"""api — metody wywoływane z UI (pywebview js_api).

Wszystkie zwracają zwykłe dicty/listy (JSON). Długie operacje (skan, start gry,
pobieranie przypiętych) działają w wątkach; UI odpytuje ich stan.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from pathlib import Path

from emustart import (__version__, art, art_sources, cache, config, emulators, launcher, library,
                      paths, scanner, systems, winutil)

log = logging.getLogger("emustart.api")

_LOGO_ALIAS = {"FBNEO": "ARCADE", "SUPERGRAFX": "PCENGINE", "SNESMSU1": "SNES"}


def _logo(plat: str) -> str:
    for key in (plat, _LOGO_ALIAS.get(plat, "")):
        if key and (paths.ASSETS / "systems" / f"{key}.png").is_file():
            return f"/assets/systems/{key}.png"
    return ""


class Api:
    def __init__(self):
        self._cfg = config.load()
        self._window = None
        self._fetcher = art.Fetcher()
        self._session: launcher.Session | None = None
        self._background: list = []          # sesje, które jeszcze kopiują w tle
        self._pin_jobs: dict = {}            # game_id → Session-like (kopiowanie przypiętych)
        self._scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}
        self._profile = library.default_profile()
        # Menu w grze: stan czyta UI (ingame_poll), Python niczego nie wywołuje
        # w oknie. evaluate_js przy grze na pełnym ekranie potrafiło czekać 20 s
        # i blokowało w tym czasie wszystkie wywołania z UI.
        self._menu = {"open": False, "items": [], "message": "", "title": "", "seq": 0}
        self._menu_inputs: collections.deque = collections.deque()
        self._menu_lock = threading.Lock()

    def attach(self, window) -> None:
        self._window = window

    # ── stan ogólny ──
    def get_state(self) -> dict:
        cfg = self._cfg
        rom_ok = Path(cfg.get("rom_root") or "").is_dir()
        return {
            "version": __version__,
            "configured": config.is_configured(cfg),
            "rom_root": cfg.get("rom_root"), "rom_online": rom_ok,
            "network_mode": cfg.get("network_mode", "auto"),
            "systems": self.list_systems(),
            "scan": dict(self._scan),
            "cache": cache.usage(cfg),
            "copying": self._copying(),
        }

    def list_systems(self) -> list:
        out = []
        for s in library.systems_summary():
            info = systems.info(s["es"])
            emu = (self._cfg.get("systems") or {}).get(s["es"]) or {}
            if not emu.get("enabled", True) or not s["games"]:
                continue
            out.append({"es": s["es"], "display": s["display"], "games": s["games"],
                        "cached": s["cached"], "logo": _logo(info["plat"]),
                        "emulator": emu.get("label", ""), "kind": info["kind"]})
        return out

    def list_games(self, es: str) -> list:
        hide = self._cfg.get("hide_arcade_clones", True) and systems.info(es)["kind"] == "arcade"
        rows = library.list_games(es, self._profile, hide)
        for r in rows:
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
        return {"id": g["id"], "title": g["title"], "tags": g["tags"], "name": g["name"],
                "system": info["display"], "size": g["size"], "files": len(g["files"]),
                "file": g["rel"], "cached": bool(row and row["complete"]),
                "pinned": bool(row and row["pinned"]),
                "plays": play["plays"] if play else 0,
                "seconds": play["seconds"] if play else 0,
                "last": play["last"] if play else 0,
                "copying": g["id"] in self._copying()}

    # ── uruchamianie ──
    def launch(self, game_id: int) -> dict:
        if self._session and self._session.phase in ("preparing", "downloading",
                                                     "extracting", "running"):
            return {"ok": False, "reason": "Inna gra jest właśnie uruchamiana."}
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
                                         ui=self if self._window else None)
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
        sysrow = library.system_row(g["es"]) or {}
        rom_dir = Path(sysrow.get("rom_dir") or "")
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
        root = Path(cfg.get("rom_root") or "")
        folders = sorted((d.name for d in root.iterdir() if d.is_dir()),
                         key=str.lower) if root.is_dir() else list(cfg.get("systems", {}))
        rows = []
        for es in folders:
            info = systems.info(es)
            sc = (cfg.get("systems") or {}).get(es) or {}
            rows.append({"es": es, "display": info["display"], "known": es in systems.SYSTEMS
                         or es.lower() in systems.SYSTEMS,
                         "enabled": sc.get("enabled", True),
                         "label": sc.get("label", ""), "exe": sc.get("exe", ""),
                         "args": sc.get("args", "")})
        return {"rom_root": cfg.get("rom_root", ""), "emu_root": cfg.get("emu_root", ""),
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
        for k in ("rom_root", "emu_root", "network_mode"):
            if k in data:
                cfg[k] = str(data[k]).strip()
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
        root = Path(cfg.get("rom_root") or "")
        if not root.is_dir():
            return {"ok": False, "reason": f"Folder z grami niedostępny: {root}"}
        cfg.setdefault("systems", {})
        emulators.forget_scan()
        found = 0
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            cur = cfg["systems"].setdefault(d.name, {"enabled": True})
            if cur.get("exe") and Path(cur["exe"]).is_file():
                continue
            opt = emulators.default_option(systems.info(d.name), cfg.get("emu_root", ""))
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
