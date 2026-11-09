"""goglib — gry GOG w kliencie (EmuStart / silnik Lumen): katalog z serwera z kopią offline, kolejka zadań
(instalacja, wznowienie, DLC, naprawa), stan, uruchamianie i śledzenie gry.

Metody `Api.gog_*` (emustart/api.py) wołają tylko ten moduł. Wszystkie dane gry pochodzą z serwera EmuStart
(emustart/goginstall.ServerSource) — nigdy z GOG. Zainstalowana gra uruchamia się bez serwera.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from pathlib import Path

from emustart import config, gogfinal, goginstall as gi
from emustart.gogarchive import BUILD_ID, PRODUCT_ID

log = logging.getLogger("emustart.goglib")

PHASES = {"queued": "W kolejce", "manifest": "Manifest z serwera", "download": "Instalowanie z serwera",
          "verify": "Weryfikacja plików", "finalize": "Finalizacja", "done": "Zainstalowano",
          "error": "Błąd", "cancelled": "Przerwano"}


def _catalog_path() -> Path:
    return gi.gog_dir() / "catalog.json"


class GogService:
    def __init__(self, cfg_fn, source_fn=None):
        self._cfg = cfg_fn
        self._source_fn = source_fn or (lambda: gi.ServerSource(self._cfg()))
        self._lock = threading.Lock()
        self._queue: deque = deque()
        self._job: dict | None = None
        self._last: dict = {}               # pid → wynik ostatniego zadania (komunikat)
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._running: dict = {}            # pid → {"proc", "dir", "started"}
        self.catalog_error = ""

    # ── katalog ────────────────────────────────────────────────────────────
    def catalog(self, refresh: bool = True) -> dict:
        """Gry GOG z serwera (świeże albo ostatnia kopia, gdy serwera nie ma) + lokalne instalacje."""
        games, fresh = None, False
        if refresh:
            try:
                games = self._source_fn().catalog()
                fresh = True
                gi._atomic(_catalog_path(), json.dumps({"games": games, "at": time.time()}, ensure_ascii=False).encode())
                self.catalog_error = ""
            except Exception as ex:                       # serwer niedostępny — kopia offline
                self.catalog_error = str(ex)
                log.info("GOG: katalog z serwera niedostępny: %s", ex)
        if games is None:
            try:
                games = json.loads(_catalog_path().read_text(encoding="utf-8")).get("games") or []
            except (OSError, ValueError):
                games = []
        running = set(self.running_ids())                  # jeden przegląd procesów na cały katalog
        installs = {e["product_id"]: self.summary(e, running) for e in gi.entries()}
        # zainstalowana gra, której serwer już nie ma (albo nie znamy katalogu) — nadal w bibliotece
        known = {g["product_id"] for g in games}
        for pid, s in installs.items():
            if pid not in known:
                games.append({"product_id": pid, "title": s["title"], "slug": "", "builds": [], "local_only": True})
        return {"games": games, "installs": installs, "fresh": fresh, "error": self.catalog_error}

    def summary(self, e: dict, running=None) -> dict:
        pid = e["product_id"]
        running = set(self.running_ids()) if running is None else running
        return {"product_id": pid, "title": e.get("title") or pid, "state": e.get("state"),
                "build_id": e.get("build_id"), "version": e.get("version") or "", "hash": e.get("hash"),
                "install_dir": e.get("install_dir"), "languages": e.get("languages") or [],
                "primary": e.get("primary") or "", "dlcs": e.get("dlcs") or [], "error": e.get("error") or "",
                "admin_steps": len((e.get("finalize") or {}).get("admin") or []),
                "runtimes": e.get("runtimes") or [], "pending_dlc": (e.get("pending_dlc") or {}).get("dlcs") or [],
                "installed_at": e.get("installed_at") or 0, "last_played": e.get("last_played") or 0,
                "launch": (e.get("launch") or {}).get("name") or "", "running": pid in running}

    def options(self, pid: str, base_dir: str = "", langs_pref=()) -> dict:
        """Co można wybrać przy instalacji: buildy z serwera, ich kompletne języki i DLC, katalog, miejsce."""
        pid = _pid(pid)
        cat = self.catalog(refresh=True)
        g = next((x for x in cat["games"] if x["product_id"] == pid), None)
        if not g or not g.get("builds"):
            return {"ok": False, "reason": cat["error"] or "Serwer nie udostępnia tej gry."}
        b = g["builds"][0]
        pref = [str(x).lower() for x in langs_pref or []]
        langs = [x for x in pref if x in b["languages"]]
        target = str(gi.target_for(base_dir, b)) if base_dir else ""
        return {"ok": True, "product_id": pid, "title": g["title"], "builds": g["builds"], "build_id": b["build_id"],
                "languages": b["languages"], "default_languages": langs or [], "dlcs": b["dlcs"],
                "dependencies": b["dependencies"], "size": b["size"], "target": target,
                "free": gi.free_bytes(Path(target)) if target else None,
                "installed": cat["installs"].get(pid), "update_available": self._update_note(cat["installs"].get(pid), b)}

    @staticmethod
    def _update_note(inst: dict | None, newest: dict) -> str:
        if inst and inst.get("hash") and inst["hash"] != newest["hash"]:
            return ("Serwer ma nowszy build tej gry. Aktualizacja zainstalowanej gry nie jest jeszcze obsługiwana — "
                    "zainstalowana wersja działa dalej; nowy build: usuń grę i zainstaluj ponownie (zapisy w "
                    "folderze zapisów zostają).")
        return ""

    ART_KINDS = ("grid", "hero", "logo", "icon")
    ART_MAX_AGE = 7 * 86400

    def art(self, pid: str, kind: str) -> dict:
        """Grafika gry z serwera (okładka, tło, logo, ikona z folderu GOG Managera) — kopia na dysku, żeby
        biblioteka działała bez serwera. {"path": plik} albo {"path": ""}."""
        pid = _pid(pid)
        if kind not in self.ART_KINDS:
            raise ValueError("zły rodzaj grafiki")
        d = gi.gog_dir() / "art" / pid
        have = next((p for p in (d / f"{kind}.jpg", d / f"{kind}.png") if p.is_file()), None)
        miss = d / f"{kind}.none"
        fresh = lambda p: time.time() - p.stat().st_mtime < self.ART_MAX_AGE      # noqa: E731
        if have and fresh(have):
            return {"path": str(have)}
        if not have and miss.is_file() and fresh(miss):
            return {"path": ""}
        try:
            data, ctype = self._source_fn().art(pid, kind)
        except Exception as ex:                        # serwer niedostępny — stara kopia albo nic
            log.info("GOG %s: grafika %s: %s", pid, kind, ex)
            return {"path": str(have) if have else ""}
        d.mkdir(parents=True, exist_ok=True)
        if data is None:
            miss.write_bytes(b"")
            return {"path": str(have) if have else ""}
        out = d / f"{kind}{'.png' if ctype == 'image/png' else '.jpg'}"
        for p in (d / f"{kind}.jpg", d / f"{kind}.png", miss):
            if p != out:
                p.unlink(missing_ok=True)
        gi._atomic(out, data)
        return {"path": str(out)}

    # ── kolejka ────────────────────────────────────────────────────────────
    def enqueue(self, kind: str, pid: str, **args) -> dict:
        pid = _pid(pid)
        with self._lock:
            busy = [j for j in list(self._queue) + ([self._job] if self._job else []) if j["pid"] == pid]
            if busy:
                return {"ok": False, "reason": "Ta gra ma już zadanie w kolejce."}
            self._queue.append({"kind": kind, "pid": pid, "args": args, "phase": "queued", "done": 0, "total": 0,
                                "title": args.get("title") or pid, "t0": time.monotonic()})
            if not self._worker or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._loop, daemon=True, name="gog-zadania")
                self._worker.start()
        return {"ok": True}

    def cancel(self, pid: str) -> dict:
        pid = _pid(pid)
        with self._lock:
            for j in list(self._queue):
                if j["pid"] == pid:
                    self._queue.remove(j)
                    return {"ok": True}
            if self._job and self._job["pid"] == pid:
                self._cancel.set()
                return {"ok": True}
        return {"ok": False, "reason": "Brak zadania tej gry."}

    def _loop(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._job = None
                    return
                job = self._job = self._queue.popleft()
                self._cancel.clear()
            self._run(job)

    def _run(self, job: dict) -> None:
        pid, a = job["pid"], job["args"]

        def prog(done, total):
            job["done"], job["total"] = done, total

        def phase(p):
            job["phase"] = p

        try:
            src = self._source_fn()
            if job["kind"] == "install" and a.get("resume"):
                e = gi.resume(src, pid, self._cancel, prog, phase)
            elif job["kind"] == "install":
                e = gi.install(src, pid, a.get("build", ""), a.get("langs") or [], a.get("primary", ""),
                               a.get("dlcs") or [], a.get("target", ""), self._cancel, prog, phase)
            elif job["kind"] == "dlc":
                e = gi.add_dlc(src, pid, a.get("dlcs") or [], self._cancel, prog, phase)
            elif job["kind"] == "repair":
                e = gi.repair(src, pid, self._cancel, prog, phase)
            else:
                raise gi.GogError("nieznane zadanie")
            msg = "Zainstalowano." if e["state"] == gi.ST_INSTALLED else \
                "Pliki zainstalowane — dokończ instalację krokiem administratora." if e["state"] == gi.ST_NEEDS_ADMIN else e["state"]
            self._last[pid] = {"ok": True, "message": msg, "at": time.time()}
            job["phase"] = "done"
        except gi.Cancelled:
            self._last[pid] = {"ok": False, "message": "Przerwano — postęp zapisany, można wznowić.", "at": time.time()}
            job["phase"] = "cancelled"
        except Exception as ex:
            log.warning("GOG %s: %s", pid, ex)
            self._last[pid] = {"ok": False, "message": str(ex), "at": time.time()}
            job["phase"] = "error"

    def status(self) -> dict:
        with self._lock:
            jobs = [self._job] if self._job else []
            jobs += list(self._queue)
            out = []
            for j in jobs:
                el = max(0.001, time.monotonic() - j["t0"])
                speed = j["done"] / el if j["phase"] == "download" else 0
                out.append({"product_id": j["pid"], "kind": j["kind"], "title": j["title"], "phase": j["phase"],
                            "label": PHASES.get(j["phase"], j["phase"]), "done": j["done"], "total": j["total"],
                            "speed": speed, "active": j is self._job, "cancellable": True})
        return {"jobs": out, "last": self._last, "running": self.running_ids()}

    # ── uruchamianie ───────────────────────────────────────────────────────
    def launch(self, pid: str, task: int | None = None) -> dict:
        """Zainstalowana gra — bez serwera. Stan ≠ installed (finalizacja, uszkodzenie) → odmowa z powodem."""
        pid = _pid(pid)
        e = gi.load_entry(pid)
        if not e:
            return {"ok": False, "reason": "Gra nie jest zainstalowana."}
        if e["state"] == gi.ST_NEEDS_ADMIN:
            return {"ok": False, "reason": "Instalacja wymaga jeszcze kroku administratora (wpisy rejestru)."}
        if e["state"] != gi.ST_INSTALLED:
            return {"ok": False, "reason": e.get("error") or "Instalacja nie jest ukończona."}
        if pid in self.running_ids():
            return {"ok": False, "reason": "Gra już działa."}
        root = Path(e["install_dir"])
        try:
            spec = gogfinal.launch_spec(root, pid, task)
        except gogfinal.FinalizeError as ex:
            return {"ok": False, "reason": str(ex)}
        try:
            proc = gogfinal.start(spec)
        except OSError as ex:
            return {"ok": False, "reason": f"Nie udało się uruchomić gry: {ex}"}
        self._running[pid] = {"proc": proc, "dir": str(root), "started": time.time()}
        e["last_played"] = time.time()
        gi.save_entry(e)
        return {"ok": True, "message": f"Uruchomiono: {spec['name'] or e.get('title')}"}

    def running_ids(self) -> list:
        """Gra działa, dopóki żyje jej proces albo dowolny proces z plikiem w katalogu gry (launcher, który
        uruchomił grę i się zamknął, nie kończy sesji)."""
        out = []
        for pid, r in list(self._running.items()):
            alive = r["proc"].poll() is None
            if not alive:
                alive = bool(gogfinal.processes_under(Path(r["dir"])))
            if alive:
                out.append(pid)
            elif time.time() - r["started"] > 5:          # krótka chwila na start procesu gry po launcherze
                self._running.pop(pid, None)
            else:
                out.append(pid)
        return out

    # ── pozostałe operacje ─────────────────────────────────────────────────
    def play_tasks(self, pid: str) -> dict:
        e = gi.load_entry(_pid(pid))
        if not e or e.get("state") not in (gi.ST_INSTALLED, gi.ST_NEEDS_ADMIN, gi.ST_DAMAGED):
            return {"ok": False, "reason": "Gra nie jest zainstalowana."}
        try:
            return {"ok": True, "tasks": gogfinal.play_tasks(Path(e["install_dir"]), pid)}
        except gogfinal.FinalizeError as ex:
            return {"ok": False, "reason": str(ex)}

    def verify(self, pid: str) -> dict:
        try:
            r = gi.verify(_pid(pid))
        except gi.GogError as ex:
            return {"ok": False, "reason": str(ex)}
        return {"ok": True, **r}

    def uninstall(self, pid: str) -> dict:
        pid = _pid(pid)
        if pid in self.running_ids():
            return {"ok": False, "reason": "Zamknij grę przed usunięciem."}
        if self._job and self._job["pid"] == pid:
            return {"ok": False, "reason": "Gra ma zadanie w toku — przerwij je najpierw."}
        try:
            r = gi.uninstall(pid)
        except gi.GogError as ex:
            return {"ok": False, "reason": str(ex)}
        self._last.pop(pid, None)
        msg = "Usunięto grę." if r["dir_removed"] else \
            f"Usunięto pliki gry. Zostało {r['left']} plików (zapisy / pliki gracza) w {r['dir']}."
        return {"ok": True, "message": msg, **r}

    def finish_admin(self, pid: str, elevate=None) -> dict:
        """Kroki HKLM z prośbą o uprawnienia administratora (okno UAC) — tylko na wyraźne żądanie gracza."""
        pid = _pid(pid)
        e = gi.load_entry(pid)
        if not e or e.get("state") != gi.ST_NEEDS_ADMIN:
            return {"ok": False, "reason": "Ta gra nie czeka na krok administratora."}
        acts = (e.get("finalize") or {}).get("admin") or []
        ok = gogfinal.run_admin_registry(acts, gi.gog_dir() / "admin" / pid, elevate)
        if not ok:
            return {"ok": False, "reason": "Kroku administratora nie wykonano (odmowa w oknie UAC albo błąd)."}
        e["finalize"]["admin_done"] = acts
        e["finalize"]["admin"] = []
        e["state"] = gi.ST_INSTALLED
        e["error"] = ""
        gi.save_entry(e)
        return {"ok": True, "message": "Instalacja ukończona."}

    def install_runtimes(self, pid: str, elevate=None) -> dict:
        """Biblioteki systemowe z zależności (Visual C++, DirectX) — jawnie, każdy instalator z UAC."""
        pid = _pid(pid)
        e = gi.load_entry(pid)
        if not e or e.get("state") != gi.ST_INSTALLED:
            return {"ok": False, "reason": "Gra nie jest zainstalowana."}
        steps = gogfinal.runtime_steps(Path(e["install_dir"]), e.get("dependencies") or [])
        if not steps:
            return {"ok": True, "message": "Ta gra nie ma bibliotek systemowych do zainstalowania."}
        res = gogfinal.run_runtimes(steps, elevate)
        e["runtimes"] = [{"id": k, "done": v} for k, v in res.items()]
        gi.save_entry(e)
        bad = [k for k, v in res.items() if not v]
        return {"ok": not bad, "message": "Biblioteki zainstalowane." if not bad else "",
                "reason": f"Nie zainstalowano: {', '.join(bad)} (odmowa UAC albo instalator zgłosił błąd / nowszą wersję)." if bad else ""}


def _pid(pid) -> str:
    s = str(pid or "")
    if not PRODUCT_ID.match(s):
        raise ValueError(f"zły identyfikator gry GOG: {pid!r}")
    return s


def check_target(cfg: dict, target: str) -> str:
    """Powód odrzucenia katalogu instalacji albo ''. Gry GOG nie mogą leżeć w cache gier emulowanych —
    czyszczenie cache (cache.enforce_limit) nie może ich dotknąć."""
    if not target or not os.path.isabs(target):
        return "Podaj pełną ścieżkę katalogu instalacji gier GOG."
    t = Path(os.path.abspath(target))
    try:
        cache = Path(os.path.abspath(config.cache_dir(cfg)))
        if t == cache or cache in t.parents:
            return "Katalog gier GOG nie może leżeć w pamięci podręcznej gier emulowanych (jest czyszczona automatycznie)."
    except Exception:
        pass
    return ""


def valid_build(b) -> str:
    s = str(b or "")
    return s if BUILD_ID.match(s) else ""

