"""art — okładki i zrzuty z serwera miniatur libretro.

Kolekcja jest znormalizowana do nazw No-Intro/Redump, a libretro nazywa
miniatury dokładnie tak samo, więc trafienie jest pewne bez zgadywania. Arcade
dostaje opis z MAME („10-Yard Fight (World, set 1)”) i to też jest nazwa miniatury.

Pliki: data/media/<es>/<nazwa>.box.png / .snap.png. Pobieranie w tle,
najpierw to, o co UI pyta teraz (zaznaczona gra), potem reszta kolejki.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from emustart import art_sources, library, paths, systems

log = logging.getLogger("emustart.art")

KINDS = {"box": "Named_Boxarts", "snap": "Named_Snaps"}
HAS, MISSING = 1, 2


def media_path(es: str, name: str, kind: str) -> Path:
    return paths.MEDIA / es / f"{systems.thumb_name(name)}.{kind}.png"


def media_url(es: str, name: str, kind: str) -> str:
    rel = f"{es}/{systems.thumb_name(name)}.{kind}.png"
    return "/media/" + urllib.parse.quote(rel)


def save(es: str, name: str, kind: str, data: bytes) -> None:
    out = media_path(es, name, kind)
    out.parent.mkdir(parents=True, exist_ok=True)
    # dwa wątki mogą pobierać tę samą grafikę — każdy pisze do własnego pliku
    # tymczasowego, a przegrany wyścig nie jest błędem
    tmp = out.with_name(f"{out.name}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    try:
        tmp.replace(out)
    except PermissionError:
        tmp.unlink(missing_ok=True)


def _download(es: str, name: str, kind: str) -> int | None:
    """Tylko libretro (dokładnie + po liście plików): HAS / MISSING / None."""
    data = art_sources.from_libretro(es, name, kind)
    if data:
        save(es, name, kind, data)
        return HAS
    # None = brak sieci; nie zapisujemy „brak”, spróbujemy później
    return MISSING if art_sources.online() else None


class Fetcher:
    """Kolejka priorytetowa pobierania miniatur, kilka wątków."""

    def __init__(self, workers: int = 4):
        self._heap: list = []
        self._queued: set = set()
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._prio = itertools.count(0, -1)   # nowsze żądania wyprzedzają starsze
        self.done = 0
        for i in range(workers):
            threading.Thread(target=self._work, daemon=True, name=f"art{i}").start()

    def request(self, game_ids, urgent: bool = True) -> None:
        with self._cv:
            for gid in game_ids:
                if gid in self._queued:
                    if not urgent:
                        continue
                prio = next(self._prio) if urgent else 1
                heapq.heappush(self._heap, (prio, next(self._seq), gid))
                self._queued.add(gid)
            self._cv.notify_all()

    def pending(self) -> int:
        with self._cv:
            return len(self._queued)

    def _work(self) -> None:
        while True:
            with self._cv:
                while not self._heap:
                    self._cv.wait()
                _p, _s, gid = heapq.heappop(self._heap)
                if gid not in self._queued:
                    continue
                self._queued.discard(gid)
            try:
                self._fetch(gid)
            except Exception:
                log.exception("art %s", gid)

    def _fetch(self, gid: int) -> None:
        r = library.db().execute("SELECT es, name, art_box, art_snap FROM games WHERE id=?",
                                 (gid,)).fetchone()
        if not r:
            return
        for kind, state in (("box", r["art_box"]), ("snap", r["art_snap"])):
            if state:
                continue
            if media_path(r["es"], r["name"], kind).exists():
                library.set_art(gid, kind, HAS)
                continue
            res = _download(r["es"], r["name"], kind)
            if res:
                library.set_art(gid, kind, res)
        self.done += 1


class Job:
    """Zbiorcze pobieranie brakujących grafik (narzędzie „Grafiki”).

    Pełny łańcuch źródeł (art_sources.Sources). Bierze gry bez okładki lub zrzutu,
    także te oznaczone wcześniej jako „brak” — mogły się pojawić nowe źródła.
    """

    def __init__(self, cfg: dict, es: str | None = None, workers: int = 4):
        self.es = es
        self.sources = art_sources.Sources(cfg)
        self.cancel = threading.Event()
        self.total = self.done = 0
        self.found = {"box": 0, "snap": 0}
        self.by_source: dict = {}
        self.missing = 0
        self.current = ""
        self.started = time.monotonic()
        self.finished = False
        self._lock = threading.Lock()
        self._workers = workers
        self.thread = threading.Thread(target=self._run, daemon=True, name="art-job")

    def start(self) -> None:
        self.thread.start()

    def status(self) -> dict:
        el = time.monotonic() - self.started
        rate = self.done / el if el > 1 and self.done else 0
        return {"running": not self.finished, "es": self.es, "total": self.total,
                "done": self.done, "found": dict(self.found), "missing": self.missing,
                "by_source": dict(self.by_source), "current": self.current,
                "eta": (self.total - self.done) / rate if rate else None,
                "cancelled": self.cancel.is_set()}

    def _run(self) -> None:
        try:
            q = "SELECT id, es, name, title, art_box, art_snap FROM games "                 "WHERE hidden=0 AND (art_box!=1 OR art_snap!=1)"
            args = ()
            if self.es:
                q += " AND es=?"
                args = (self.es,)
            rows = [dict(r) for r in library.db().execute(q + " ORDER BY es, title", args)]
            self.total = len(rows)
            with ThreadPoolExecutor(self._workers) as pool:
                for _ in pool.map(self._one, rows):
                    pass
        except Exception:
            log.exception("art job")
        finally:
            self.finished = True

    def _one(self, g: dict) -> None:
        if self.cancel.is_set():
            return
        self.current = f"{g['title']}"
        need = set()
        for kind in ("box", "snap"):
            if g[f"art_{kind}"] == HAS:
                continue
            if media_path(g["es"], g["name"], kind).exists():
                library.set_art(g["id"], kind, HAS)
                continue
            need.add(kind)
        got = self.sources.find(g["es"], g["name"], need) if need else {}
        if need and not got and not art_sources.online():
            self.done += 1          # brak sieci — nie oznaczamy jako „brak grafiki”
            return
        with self._lock:
            for kind in need:
                if kind in got:
                    data, src = got[kind]
                    save(g["es"], g["name"], kind, data)
                    library.set_art(g["id"], kind, HAS)
                    self.found[kind] += 1
                    self.by_source[src] = self.by_source.get(src, 0) + 1
                else:
                    library.set_art(g["id"], kind, MISSING)
            if need - set(got):
                self.missing += 1
            self.done += 1
