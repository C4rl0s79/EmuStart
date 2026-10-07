"""art — okładki i zrzuty z serwera miniatur libretro.

Kolekcja jest znormalizowana do nazw No-Intro/Redump, a libretro nazywa
miniatury dokładnie tak samo, więc trafienie jest pewne bez zgadywania. Arcade
dostaje opis z MAME („10-Yard Fight (World, set 1)”) i to też jest nazwa miniatury.

Pliki: data/media/<es>/<nazwa>.box.png / .snap.png. Pobieranie w tle,
najpierw to, o co UI pyta teraz (zaznaczona gra), potem reszta kolejki.
"""

from __future__ import annotations

import heapq
import io
import itertools
import logging
import os
import shutil
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


MIN_FREE = 2 * 1024 ** 3          # zapas wolnego miejsca, poniżej którego nie zapisujemy
# największy sensowny rozmiar na ekranie (okładka w podglądzie, logo w wierszu/podglądzie)
LIMITS = {"box": (900, 900), "snap": (960, 720), "logo": (1000, 400)}


class DiskFull(OSError):
    pass


def check_space() -> None:
    try:
        paths.MEDIA.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(paths.MEDIA).free
    except OSError:
        return
    if free < MIN_FREE:
        raise DiskFull(f"Na dysku z grafikami zostało {free / 1024 ** 3:.1f} GB — "
                       f"pobieranie zatrzymane (zapas {MIN_FREE // 1024 ** 3} GB).")


def shrink(kind: str, data: bytes) -> bytes:
    """Zmniejsza grafikę do rozmiaru ekranowego i zapisuje jako WebP (przeglądarka
    rozpoznaje format po zawartości, nazwa pliku zostaje .png). Zwykle ~8× mniej."""
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) < 400_000:
        return data
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
        im.thumbnail(LIMITS.get(kind, (1000, 1000)))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.mode or "transparency" in im.info else "RGB")
        out = io.BytesIO()
        im.save(out, "WEBP", quality=85, method=4)
        small = out.getvalue()
        return small if len(small) < len(data) else data
    except Exception:
        return data


def save(es: str, name: str, kind: str, data: bytes) -> None:
    check_space()
    data = shrink(kind, data)
    out = media_path(es, name, kind)
    out.parent.mkdir(parents=True, exist_ok=True)
    # dwa wątki mogą pobierać tę samą grafikę — każdy pisze do własnego pliku
    # tymczasowego, a przegrany wyścig nie jest błędem
    tmp = out.with_name(f"{out.name}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        tmp.replace(out)
    except PermissionError:
        tmp.unlink(missing_ok=True)
    except OSError:
        tmp.unlink(missing_ok=True)       # nie zostawiamy pustych .tmp
        raise


def cleanup_tmp() -> int:
    """Pozostałości po przerwanych zapisach (np. pełny dysk)."""
    n = 0
    if paths.MEDIA.is_dir():
        for f in paths.MEDIA.glob("*/*.tmp"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


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

    def __init__(self, workers: int = 4, logos=lambda: False):
        self._logos = logos          # czy pobierać też logo gier (ustawienie)
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
        r = library.db().execute("SELECT es, name, rel, art_box, art_snap, art_logo FROM games WHERE id=?",
                                 (gid,)).fetchone()
        if not r:
            return
        if self._logos() and not r["art_logo"]:
            self._fetch_logo(gid, r)
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


    def _fetch_logo(self, gid: int, r) -> None:
        from emustart import launchbox
        if media_path(r["es"], r["name"], "logo").exists():
            library.set_art(gid, "logo", HAS)
            return
        if not launchbox.ready():
            return
        setname = Path(r["rel"]).stem if systems.info(r["es"])["kind"] == "arcade" else ""
        g = launchbox.find_game(r["es"], r["name"], setname)
        for img in launchbox.images(g["id"], "logo")[:2] if g else []:
            data = art_sources.fetch(img["url"])
            if art_sources.is_image(data):
                save(r["es"], r["name"], "logo", data)
                library.set_art(gid, "logo", HAS)
                return
        if art_sources.online():
            library.set_art(gid, "logo", MISSING)


class Job:
    """Zbiorcze pobieranie brakujących grafik (narzędzie „Grafiki”).

    Pełny łańcuch źródeł (art_sources.Sources). Bierze gry bez okładki lub zrzutu,
    także te oznaczone wcześniej jako „brak” — mogły się pojawić nowe źródła.
    """

    def __init__(self, cfg: dict, es: str | None = None, workers: int = 4,
                 art: bool = True, meta: bool = False, wiki: bool = False, shrink: bool = False):
        self.es = es
        self.do_shrink = shrink
        self.error = ""
        self.saved = 0                   # bajty odzyskane przez zmniejszanie
        self.cfg = cfg
        self.do_art, self.do_meta, self.do_wiki = art, meta, wiki
        self.kinds = ("box", "snap") + (("logo",) if cfg.get("games_logo") else ())
        self.meta_found = {"description": 0, "wiki": 0}
        self.sources = art_sources.Sources(cfg)
        self.cancel = threading.Event()
        self.total = self.done = 0
        self.found = {"box": 0, "snap": 0, "logo": 0}
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
                "meta": dict(self.meta_found), "mode": {"art": self.do_art, "meta": self.do_meta,
                                                        "wiki": self.do_wiki, "shrink": self.do_shrink},
                "error": self.error, "saved": self.saved,
                "eta": (self.total - self.done) / rate if rate else None,
                "cancelled": self.cancel.is_set()}

    def _run(self) -> None:
        if self.do_shrink:
            return self._run_shrink()
        try:
            cond = []
            if self.do_art:
                cond += [f"art_{k}!=1" for k in self.kinds]
            if self.do_meta:
                cond.append("1")         # metadane: sprawdzamy każdą grę (pomijanie w _one)
            q = ("SELECT id, es, name, title, rel, art_box, art_snap, art_logo FROM games "
                 "WHERE hidden=0 AND (" + " OR ".join(cond or ["0"]) + ")")
            args = ()
            if self.es:
                q += " AND es=?"
                args = (self.es,)
            rows = [dict(r) for r in library.db().execute(q + " ORDER BY es, title", args)]
            self.total = len(rows)
            with ThreadPoolExecutor(self._workers) as pool:
                for _ in pool.map(self._one_safe, rows):
                    pass
        except Exception:
            log.exception("art job")
        finally:
            self.finished = True

    def _one_safe(self, g: dict) -> None:
        try:
            self._one(g)
        except DiskFull as ex:
            if not self.error:
                self.error = str(ex)
                log.warning("%s", ex)
            self.cancel.set()
        except OSError as ex:
            if getattr(ex, "errno", 0) == 28 or getattr(ex, "winerror", 0) == 112:
                self.error = "Brak miejsca na dysku — pobieranie zatrzymane."
                self.cancel.set()
            else:
                log.warning("grafika %s: %s", g.get("name"), ex)
                with self._lock:
                    self.done += 1

    def _run_shrink(self) -> None:
        """Zmniejsza już zapisane grafiki (WebP, rozmiar ekranowy)."""
        try:
            cleanup_tmp()
            files = [f for f in paths.MEDIA.glob("*/*.png")] if paths.MEDIA.is_dir() else []
            self.total = len(files)

            def one(f: Path) -> None:
                if self.cancel.is_set():
                    return
                self.current = f"{f.parent.name}/{f.name}"
                try:
                    data = f.read_bytes()
                    kind = f.name.rsplit(".", 2)[-2] if f.name.count(".") >= 2 else ""
                    small = shrink(kind, data)
                    if len(small) < len(data):
                        tmp = f.with_name(f.name + ".shrink.tmp")
                        tmp.write_bytes(small)
                        os.replace(tmp, f)
                        with self._lock:
                            self.saved += len(data) - len(small)
                            self.found["box"] += 1
                except OSError as ex:
                    log.warning("zmniejszanie %s: %s", f, ex)
                with self._lock:
                    self.done += 1

            with ThreadPoolExecutor(self._workers) as pool:
                for _ in pool.map(one, files):
                    pass
            log.info("zmniejszono %d grafik, odzyskano %.1f GB", self.found["box"], self.saved / 1024 ** 3)
        except Exception:
            log.exception("zmniejszanie grafik")
        finally:
            self.finished = True

    def _one(self, g: dict) -> None:
        if self.cancel.is_set():
            return
        self.current = f"{g['title']}"
        if self.do_meta:
            try:
                from emustart import metadata
                metadata.ensure_local(self.cfg, g)
                if metadata.fill_launchbox(self.cfg, g):
                    self.meta_found["description"] += 1
                if self.do_wiki and not metadata.get(g["id"]).get("description") \
                        and metadata.fill_wikipedia(g):
                    self.meta_found["wiki"] += 1
            except Exception:
                log.exception("metadane %s", g["name"])
        if not self.do_art:
            with self._lock:
                self.done += 1
            return
        need = set()
        for kind in self.kinds:
            if g[f"art_{kind}"] == HAS:
                continue
            if media_path(g["es"], g["name"], kind).exists():
                library.set_art(g["id"], kind, HAS)
                continue
            need.add(kind)
        setname = Path(g["rel"]).stem if systems.info(g["es"])["kind"] == "arcade" else ""
        got = self.sources.find(g["es"], g["name"], need, setname) if need else {}
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
