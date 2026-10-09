"""partial — plik gry pobierany z NAS-a blokami, równolegle i na żądanie.

Plik `<gra>.part` ma od razu pełny rozmiar; obok `<gra>.part.map` zapamiętuje,
które bloki (BLOCK bajtów) są już pobrane — przerwane pobieranie wznawia się
od brakujących bloków. Kilka wątków pobiera kolejne brakujące bloki od
początku pliku (na łączu przez Tailscale 4 strumienie dają ok. 1,5× więcej niż
jeden). `read()` (wirtualny dysk, emustart/vfs.py) czyta z pliku lokalnego,
a brakujący blok pobiera natychmiast, poza kolejką, i zleca kilka następnych.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("emustart.partial")

BLOCK = 4 * 1024 * 1024
STREAMS = 4
READ_AHEAD = 8                 # tyle bloków za żądanym trafia na początek kolejki
MIN_SIZE = 32 * 1024 * 1024    # mniejsze pliki — jednym strumieniem, jak dotąd

# pliki pobierane właśnie teraz (ścieżka docelowa → Partial) — dla wirtualnego dysku
ACTIVE: dict = {}
_active_lock = threading.Lock()


class Partial:
    def __init__(self, src: Path, dst: Path, size: int, mtime: float):
        self.src, self.dst, self.size, self.mtime = Path(src), Path(dst), size, mtime
        self.part = self.dst.with_name(self.dst.name + ".part")
        self.mapf = self.dst.with_name(self.dst.name + ".part.map")
        self.blocks = max(1, (size + BLOCK - 1) // BLOCK)
        self.lock = threading.Condition()
        self.busy: set = set()            # bloki pobierane w tej chwili
        self.prio: list = []              # bloki chciane przez czytającego (kolejność)
        self.error: Exception | None = None
        self.progress = None              # cache.Progress pobierania (także bloki na żądanie)
        self.finished = False             # wszystkie bloki są
        self.final = False                # .part przemianowany na plik docelowy
        self._map_dirty = 0
        self._dsrc = None                 # otwarty plik na NAS-ie dla odczytów na żądanie
        self._dlock = threading.Lock()
        self._prepare()

    # ── stan na dysku ──
    def _prepare(self) -> None:
        self.dst.parent.mkdir(parents=True, exist_ok=True)
        done = bytearray(self.blocks)
        have = self.part.stat().st_size if self.part.exists() else 0
        if self.part.exists() and self.mapf.exists():
            m = self.mapf.read_bytes()
            if len(m) == self.blocks and have == self.size:
                done = bytearray(m)
        elif have:
            # stary format: .part dopisywany po kolei — pełne bloki z początku są dobre
            for i in range(min(self.blocks, have // BLOCK)):
                done[i] = 1
            if have >= self.size and self.size:
                done = bytearray(b"\1" * self.blocks)
        if have > self.size:
            self.part.unlink()
            done = bytearray(self.blocks)
        self.done = done
        with open(self.part, "ab") as f:        # pełny rozmiar od razu (plik rzadki)
            _set_sparse(f)
            f.truncate(self.size)
        self._save_map()
        self.finished = all(self.done)

    def _save_map(self) -> None:
        tmp = self.mapf.with_name(self.mapf.name + ".tmp")
        tmp.write_bytes(bytes(self.done))
        os.replace(tmp, self.mapf)
        self._map_dirty = 0

    def done_bytes(self) -> int:
        n = sum(self.done)
        if n and self.done[-1]:
            n -= 1
            return n * BLOCK + (self.size - (self.blocks - 1) * BLOCK)
        return n * BLOCK

    # ── pobieranie ──
    def _next(self) -> int | None:
        """Pod blokadą: blok do pobrania (najpierw chciane, potem kolejne od początku)."""
        while self.prio:
            i = self.prio.pop(0)
            if not self.done[i] and i not in self.busy:
                return i
        for i in range(self.blocks):
            if not self.done[i] and i not in self.busy:
                return i
        return None

    def _fetch(self, i: int, fsrc) -> int:
        off = i * BLOCK
        n = min(BLOCK, self.size - off)
        fsrc.seek(off)
        data = fsrc.read(n)
        if len(data) != n:
            raise OSError(f"krótki odczyt z NAS-a ({self.src.name}, blok {i})")
        with open(self.part, "r+b") as f:
            f.seek(off)
            f.write(data)
        if self.progress:
            self.progress.add(n)
        with self.lock:
            self.done[i] = 1
            self.busy.discard(i)
            self._map_dirty += 1
            if self._map_dirty >= 8:
                self._save_map()
            if all(self.done):
                self.finished = True
                self._save_map()
            self.lock.notify_all()
        return n

    def _worker(self, progress, cancel) -> None:
        try:
            with open(self.src, "rb", buffering=0) as fsrc:
                while not (cancel and cancel.is_set()):
                    with self.lock:
                        if self.error or self.finished:
                            return
                        i = self._next()
                        if i is None:
                            return
                        self.busy.add(i)
                    try:
                        self._fetch(i, fsrc)
                    except Exception:
                        with self.lock:
                            self.busy.discard(i)
                        raise
        except Exception as ex:
            with self.lock:
                self.error = self.error or ex
                self.lock.notify_all()

    def run(self, progress=None, cancel=None, streams: int = STREAMS) -> None:
        """Pobiera brakujące bloki (STREAMS wątków); po wszystkim plik docelowy."""
        with _active_lock:
            ACTIVE[str(self.dst).lower()] = self
        self.error = None
        try:
            self.progress = progress       # łączny rozmiar postępu = tylko brakujące bloki
            ths = [threading.Thread(target=self._worker, args=(progress, cancel), daemon=True,
                                    name=f"pobieranie-{k}") for k in range(streams)]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
            with self.lock:
                self._save_map()
            if self.error:
                raise self.error
            if cancel and cancel.is_set():
                from emustart.cache import Cancelled
                raise Cancelled()
            self.finalize()
        finally:
            if self.final or self.error or (cancel and cancel.is_set()):
                with _active_lock:
                    ACTIVE.pop(str(self.dst).lower(), None)

    def close_demand(self) -> None:
        with self._dlock:
            if self._dsrc is not None:
                self._dsrc.close()
                self._dsrc = None

    def finalize(self) -> bool:
        """.part → plik docelowy. Gdy wirtualny dysk trzyma plik otwarty, przemianowanie
        się nie uda — wtedy później (vfs zwalnia plik i woła finalize ponownie)."""
        if self.final or not self.finished:
            return self.final
        try:
            os.replace(self.part, self.dst)
            os.utime(self.dst, (time.time(), self.mtime))
            self.mapf.unlink(missing_ok=True)
            self.final = True
            with _active_lock:
                ACTIVE.pop(str(self.dst).lower(), None)
        except PermissionError:
            log.info("plik %s w użyciu — dokończenie po grze", self.dst.name)
        return self.final

    # ── czytanie na żądanie (wirtualny dysk) ──
    def read(self, offset: int, length: int, timeout: float = 120) -> bytes:
        if offset >= self.size or length <= 0:
            return b""
        length = min(length, self.size - offset)
        first, last = offset // BLOCK, (offset + length - 1) // BLOCK
        self.want(first, last)
        end = time.monotonic() + timeout
        for i in range(first, last + 1):
            self._ensure(i, end)
        path = self.dst if self.final else self.part
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read(length)

    def want(self, first: int, last: int) -> None:
        with self.lock:
            ahead = [i for i in range(first, min(self.blocks, last + 1 + READ_AHEAD)) if not self.done[i]]
            self.prio = ahead + [i for i in self.prio if i not in ahead]

    def _ensure(self, i: int, end: float) -> None:
        with self.lock:
            if self.done[i]:
                return
            mine = i not in self.busy
            if mine:
                self.busy.add(i)
        if mine:
            # czytający pobiera brakujący blok sam — bez czekania na kolejkę; plik na
            # NAS-ie zostaje otwarty (każde otwarcie to kilka wymian po ~40 ms)
            try:
                with self._dlock:
                    if self._dsrc is None:
                        self._dsrc = open(self.src, "rb", buffering=0)
                    self._fetch(i, self._dsrc)
            except Exception:
                with self.lock:
                    self.busy.discard(i)
                raise
            return
        with self.lock:
            while not self.done[i]:
                if self.error:
                    raise self.error
                left = end - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"blok {i} z {self.src.name} nie dotarł")
                self.lock.wait(min(left, 1.0))


def active(dst: Path) -> Partial | None:
    with _active_lock:
        return ACTIVE.get(str(dst).lower())


def _set_sparse(f) -> None:
    """Plik rzadki (NTFS): zapis bloku daleko w pliku nie wypełnia zerami całej
    luki przed nim — bez tego pobranie bloku z końca gry 2 GB zapisywałoby 2 GB zer."""
    if os.name != "nt":
        return
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        h = msvcrt.get_osfhandle(f.fileno())
        ret = wintypes.DWORD()
        ctypes.windll.kernel32.DeviceIoControl(wintypes.HANDLE(h), 0x000900C4, None, 0, None, 0,
                                               ctypes.byref(ret), None)   # FSCTL_SET_SPARSE
    except Exception:
        pass


def open_partial(src: Path, dst: Path, size: int, mtime: float) -> Partial:
    """Wspólny obiekt dla pliku: pobieranie w tle i wirtualny dysk (czytanie na
    żądanie) muszą działać na tej samej mapie bloków."""
    key = str(dst).lower()
    with _active_lock:
        p = ACTIVE.get(key)
        if p is None or p.size != size:
            p = Partial(src, dst, size, mtime)
            ACTIVE[key] = p
        return p
