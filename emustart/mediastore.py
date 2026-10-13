"""mediastore — grafiki gier w jednym pliku na system: `media/<system>.media` (SQLite).

Wcześniej każda okładka, zrzut i logo były osobnym plikiem (`media/<system>/<gra>.box.png`)
— na serwerze ok. 100 tys. małych plików: wolne kopiowanie, przenoszenie i kopie zapasowe.
Teraz jeden plik na system. SQLite, a nie ZIP/zstd: grafika czytana i podmieniana w miejscu
(ZIP przy każdej zmianie trzeba przepisać w całości, zstd nie da się czytać wybiórczo),
kilka wątków zapisuje naraz (pobieranie grafik), przerwany zapis niczego nie psuje.
Grafiki są już w WebP — bez dodatkowej kompresji.

Adresy się nie zmieniają: `/media/<system>/<plik>` (plik = `<nazwa>.<rodzaj>.png`).
Logo systemów (`media/_systems`) i obrazki stanów (`media/_stany`) zostają plikami.

Przeniesienie starych plików: `migrate()` — każdy plik zapisany w bazie i odczytany z niej
z powrotem (ta sama zawartość) jest dopiero wtedy usuwany; folder znika, gdy jest pusty.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from emustart import paths

log = logging.getLogger("emustart.mediastore")

_local = threading.local()
_init_lock = threading.Lock()
_ES = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
EXT = ".media"
IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def valid_es(es: str) -> bool:
    return bool(_ES.match(es or "")) and not es.startswith("_")


def path_of(es: str) -> Path:
    return paths.MEDIA / f"{es}{EXT}"


def _con(es: str, create: bool = True) -> sqlite3.Connection | None:
    if not valid_es(es):
        raise ValueError(f"zła nazwa systemu: {es!r}")
    p = path_of(es)
    cache = getattr(_local, "cons", None)
    if cache is None:
        cache = _local.cons = {}
    hit = cache.get(es)
    if hit is not None and hit[0] == str(p):
        return hit[1]
    if not create and not p.is_file():
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=30)
    with _init_lock:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("CREATE TABLE IF NOT EXISTS art (file TEXT PRIMARY KEY, data BLOB NOT NULL, "
                    "mtime REAL NOT NULL)")
        con.commit()
    cache[es] = (str(p), con)
    return con


def close_all() -> None:
    """Zamyka połączenia tego wątku (testy, usuwanie pliku systemu)."""
    for _p, con in (getattr(_local, "cons", None) or {}).values():
        try:
            con.close()
        except sqlite3.Error:
            pass
    _local.cons = {}


def get(es: str, file: str) -> tuple[bytes, float] | None:
    con = _con(es, create=False)
    if con is None:
        return None
    r = con.execute("SELECT data, mtime FROM art WHERE file=?", (file,)).fetchone()
    return (bytes(r[0]), float(r[1])) if r else None


def mtime(es: str, file: str) -> float:
    con = _con(es, create=False)
    if con is None:
        return 0.0
    r = con.execute("SELECT mtime FROM art WHERE file=?", (file,)).fetchone()
    return float(r[0]) if r else 0.0


def exists(es: str, file: str) -> bool:
    return mtime(es, file) > 0


def put(es: str, file: str, data: bytes, when: float | None = None) -> None:
    con = _con(es)
    with con:
        con.execute("INSERT OR REPLACE INTO art(file, data, mtime) VALUES(?,?,?)",
                    (file, sqlite3.Binary(data), when or time.time()))


def delete(es: str, file: str) -> bool:
    con = _con(es, create=False)
    if con is None:
        return False
    with con:
        return con.execute("DELETE FROM art WHERE file=?", (file,)).rowcount > 0


def rename(es: str, old: str, new: str) -> bool:
    """Grafika pod nową nazwą (gdy nowej jeszcze nie ma)."""
    con = _con(es, create=False)
    if con is None:
        return False
    with con:
        if con.execute("SELECT 1 FROM art WHERE file=?", (new,)).fetchone():
            return False
        return con.execute("UPDATE art SET file=? WHERE file=?", (new, old)).rowcount > 0


def files(es: str) -> list:
    con = _con(es, create=False)
    if con is None:
        return []
    return [r[0] for r in con.execute("SELECT file FROM art")]


def systems_with_media() -> list:
    try:
        return sorted(f.name[:-len(EXT)] for f in paths.MEDIA.glob(f"*{EXT}") if valid_es(f.name[:-len(EXT)]))
    except OSError:
        return []


def drop_system(es: str) -> int:
    """Usuwa wszystkie grafiki systemu (plik systemu znika). Zwraca ich liczbę."""
    con = _con(es, create=False)
    if con is None:
        return 0
    with con:
        n = con.execute("DELETE FROM art").rowcount
    con.execute("VACUUM")
    return n


def migrate(progress=None) -> int:
    """Stare pliki `media/<system>/*.png` → `media/<system>.media`. Plik usuwany dopiero, gdy
    jego zawartość jest w bazie (odczyt kontrolny). Zwraca liczbę przeniesionych."""
    moved = 0
    try:
        dirs = [d for d in paths.MEDIA.iterdir() if d.is_dir() and valid_es(d.name)]
    except OSError:
        return 0
    for d in dirs:
        try:
            todo = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMG]
        except OSError:
            continue
        if not todo:
            _rmdir_if_empty(d)
            continue
        log.info("grafiki %s: przenoszę %d plików do %s", d.name, len(todo), path_of(d.name).name)
        con = _con(d.name)
        for i in range(0, len(todo), 500):
            batch = []
            for f in todo[i:i + 500]:
                try:
                    batch.append((f, f.read_bytes(), f.stat().st_mtime))
                except OSError as ex:
                    log.warning("grafika %s: %s", f, ex)
            with con:
                for f, data, mt in batch:
                    # nowsza grafika już w bazie (zapisana po migracji) — zostaje
                    r = con.execute("SELECT mtime FROM art WHERE file=?", (f.name,)).fetchone()
                    if r is None or float(r[0]) < mt:
                        con.execute("INSERT OR REPLACE INTO art(file, data, mtime) VALUES(?,?,?)",
                                    (f.name, sqlite3.Binary(data), mt))
            for f, data, _mt in batch:
                r = con.execute("SELECT data FROM art WHERE file=?", (f.name,)).fetchone()
                if r is not None and (bytes(r[0]) == data or _newer(con, f)):
                    try:
                        f.unlink()
                        moved += 1
                    except OSError:
                        pass
            if progress:
                progress(d.name, min(i + 500, len(todo)), len(todo))
        _rmdir_if_empty(d)
    if moved:
        log.info("grafiki: %d plików przeniesionych do plików systemów (.media)", moved)
    return moved


def _newer(con, f: Path) -> bool:
    """W bazie jest nowsza wersja tego pliku — stary plik można usunąć."""
    try:
        r = con.execute("SELECT mtime FROM art WHERE file=?", (f.name,)).fetchone()
        return r is not None and float(r[0]) >= f.stat().st_mtime
    except OSError:
        return False


def _rmdir_if_empty(d: Path) -> None:
    try:
        for leftover in d.glob("*.tmp"):
            leftover.unlink(missing_ok=True)
        if not any(d.iterdir()):
            d.rmdir()
    except OSError:
        pass


def migrate_later() -> None:
    def run():
        try:
            migrate()
        except Exception:
            log.exception("przenoszenie grafik")
    threading.Thread(target=run, daemon=True, name="grafiki-media").start()


def file_name(path_rel: str) -> tuple[str, str] | None:
    """'snes/Gra (USA).box.png' → ('snes', 'Gra (USA).box.png'); logo systemów i obrazki
    stanów (foldery z „_”) — None (zostają plikami)."""
    parts = path_rel.replace("\\", "/").split("/")
    if len(parts) != 2 or not valid_es(parts[0]) or not parts[1] or parts[1] in (".", "..") \
            or os.sep in parts[1] or "/" in parts[1]:
        return None
    return parts[0], parts[1]
