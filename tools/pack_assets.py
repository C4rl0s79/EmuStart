"""pack_assets — wbudowane logo platform (assets/systems/*.png) w jednym pliku programu.

Wywoływane przez emustart.spec przy budowie: `assets/systems.media` (SQLite, ten sam układ co
media/<system>.media — tabela art(file, data, mtime)) zamiast 40 osobnych plików w _internal.
W repozytorium zostają źródłowe PNG; uruchomienie ze źródeł czyta je wprost.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def build(src: Path, out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(str(out))
    con.execute("CREATE TABLE art (file TEXT PRIMARY KEY, data BLOB NOT NULL, mtime REAL NOT NULL)")
    n = 0
    for f in sorted(src.glob("*.png")):
        con.execute("INSERT INTO art(file, data, mtime) VALUES(?,?,?)",
                    (f.name, sqlite3.Binary(f.read_bytes()), f.stat().st_mtime))
        n += 1
    con.commit()
    con.execute("VACUUM")
    con.close()
    return n


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "build" / "systems.media"
    print(build(root / "assets" / "systems", out), "logo →", out)
