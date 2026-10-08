"""cache — lokalne kopie gier z NAS-a.

Układ: `<cache_dir>\\<es>\\<ścieżka względna jak na NAS>`, dzięki czemu względne
odwołania w .m3u/.cue działają bez przepisywania.

Kopiowanie idzie do `<plik>.part` — małe pliki dopisywane po kolei, duże blokami
w kilku strumieniach naraz (emustart/partial.py, mapa bloków w `.part.map`);
przerwane pobieranie przez Tailscale wznawia się od brakujących bloków. Plik w cache uznajemy za aktualny,
gdy rozmiar zgadza się z NAS-em, a data modyfikacji nie jest starsza.

Limit: `cache_recent` ostatnio uruchomionych gier + przypięte (poza limitem).
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

from emustart import config, library

CHUNK = 4 * 1024 * 1024


class Cancelled(Exception):
    pass


class Progress:
    """Stan kopiowania czytany przez UI (z innego wątku)."""

    def __init__(self, total: int, files: int):
        self.total = total
        self.done = 0
        self.files = files
        self.file_index = 0
        self.started = time.monotonic()
        self._window = []           # (czas, bajty) — prędkość z ostatnich sekund
        self.lock = threading.Lock()

    def add(self, n: int) -> None:
        with self.lock:
            self.done += n
            now = time.monotonic()
            self._window.append((now, self.done))
            while len(self._window) > 2 and now - self._window[0][0] > 5:
                self._window.pop(0)

    def speed(self) -> float:
        """B/s z ostatnich ~5 s."""
        with self.lock:
            if len(self._window) < 2:
                return 0.0
            (t0, b0), (t1, b1) = self._window[0], self._window[-1]
            return (b1 - b0) / (t1 - t0) if t1 > t0 else 0.0

    def snapshot(self) -> dict:
        sp = self.speed()
        left = max(0, self.total - self.done)
        return {"done": self.done, "total": self.total, "left": left,
                "speed": sp, "eta": (left / sp) if sp > 0 else None,
                "file": self.file_index, "files": self.files}


def root(cfg: dict) -> Path:
    return config.cache_dir(cfg)


def local_path(cfg: dict, game: dict) -> Path:
    return root(cfg) / game["es"] / game["rel"]


def _local_file(cfg: dict, game: dict, rel: str) -> Path:
    return root(cfg) / game["es"] / rel


def _fresh(local: Path, size: int, mtime: float) -> bool:
    try:
        st = local.stat()
    except OSError:
        return False
    return st.st_size == size and st.st_mtime >= mtime - 2


def is_complete(cfg: dict, game: dict) -> bool:
    return all(_fresh(_local_file(cfg, game, rel), size, mt)
               for rel, size, mt in game["files"])


def missing_bytes(cfg: dict, game: dict) -> int:
    total = 0
    for rel, size, mt in game["files"]:
        if _fresh(_local_file(cfg, game, rel), size, mt):
            continue
        part = _local_file(cfg, game, rel).with_name(Path(rel).name + ".part")
        mapf = part.with_name(part.name + ".map")
        if mapf.exists():                  # pobieranie blokami: brakujące bloki z mapy
            from emustart import partial
            try:
                m = mapf.read_bytes()
                full = sum(m[:-1]) * partial.BLOCK + (size - (len(m) - 1) * partial.BLOCK if m and m[-1] else 0)
                total += max(0, size - full)
                continue
            except OSError:
                pass
        have = part.stat().st_size if part.exists() else 0
        total += max(0, size - have)
    return total


def copy_game(cfg: dict, game: dict, rom_dir: Path, prog: Progress,
              cancel: threading.Event, extra: list | None = None) -> None:
    """Kopiuje brakujące pliki gry (i `extra` = [(src, dst, size, mtime)])."""
    jobs = []
    for rel, size, mt in game["files"]:
        jobs.append((rom_dir / rel, _local_file(cfg, game, rel), size, mt))
    jobs += extra or []
    _mark(game["id"], complete=0)
    for i, (src, dst, size, mt) in enumerate(jobs, 1):
        prog.file_index = i
        if _fresh(dst, size, mt):
            continue
        _copy_one(src, dst, size, mt, prog, cancel)
    _mark(game["id"], complete=1, size=game["size"])


def _copy_one(src: Path, dst: Path, size: int, mtime: float,
              prog: Progress, cancel: threading.Event) -> None:
    from emustart import partial
    if size >= partial.MIN_SIZE:
        # duże pliki: bloki, kilka strumieni naraz, wznawianie z mapą bloków;
        # wirtualny dysk (vfs) może w tym czasie czytać grę i prosić o bloki poza kolejką
        partial.open_partial(src, dst, size, mtime).run(prog, cancel)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        part.unlink()
        have = 0
    with open(src, "rb") as fi, open(part, "ab") as fo:
        fi.seek(have)
        while True:
            if cancel.is_set():
                raise Cancelled()
            buf = fi.read(CHUNK)
            if not buf:
                break
            fo.write(buf)
            prog.add(len(buf))
    if part.stat().st_size != size:
        raise OSError(f"Niepełna kopia: {src.name}")
    os.replace(part, dst)
    os.utime(dst, (time.time(), mtime))


def _mark(game_id: int, **cols) -> None:
    keys = ", ".join(f"{k}=excluded.{k}" for k in cols)
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    with library.db() as c:
        c.execute(f"""INSERT INTO cache(game_id, {names}) VALUES(?, {marks})
                      ON CONFLICT(game_id) DO UPDATE SET {keys}""",
                  (game_id, *cols.values()))


def touch(game_id: int) -> None:
    _mark(game_id, last_used=time.time())


def set_pinned(game_id: int, pinned: bool) -> None:
    _mark(game_id, pinned=1 if pinned else 0)


def remove(cfg: dict, game: dict) -> None:
    base = root(cfg) / game["es"]
    for rel, _s, _m in game["files"]:
        for p in (base / rel, base / (rel + ".part")):
            try:
                p.unlink()
            except OSError:
                pass
    if game.get("is_dir"):
        shutil.rmtree(base / game["rel"], ignore_errors=True)
    _prune_dirs(base)
    with library.db() as c:
        c.execute("UPDATE cache SET complete=0, size=0 WHERE game_id=?", (game["id"],))


def _prune_dirs(base: Path) -> None:
    for d in sorted((p for p in base.rglob("*") if p.is_dir()),
                    key=lambda p: len(p.parts), reverse=True):
        try:
            d.rmdir()
        except OSError:
            pass


def enforce_limit(cfg: dict, keep_ids=()) -> list:
    """Usuwa z cache gry spoza `cache_recent` ostatnich (przypięte nietykalne)."""
    limit = max(1, int(cfg.get("cache_recent") or 10))
    rows = library.db().execute("""
        SELECT game_id FROM cache
        WHERE pinned=0 AND (complete=1 OR size>0 OR last_used>0)
        ORDER BY last_used DESC""").fetchall()
    removed = []
    for r in rows[limit:]:
        if r["game_id"] in keep_ids:
            continue
        g = library.game(r["game_id"])
        if g:
            remove(cfg, g)
            removed.append(g["title"])
        with library.db() as c:
            c.execute("UPDATE cache SET last_used=0 WHERE game_id=?", (r["game_id"],))
    return removed


def usage(cfg: dict) -> dict:
    r = library.db().execute("""SELECT COUNT(*) n, COALESCE(SUM(size),0) b,
                                SUM(pinned) p FROM cache WHERE complete=1""").fetchone()
    return {"games": r["n"], "bytes": r["b"], "pinned": r["p"] or 0}
