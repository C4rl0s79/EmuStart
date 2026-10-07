"""arcade — nazwy i zależności setów arcade z `mame.exe -listxml`.

Za PyLinksWeb (`services/mame.py`): źródłem prawdy jest MAME użytkownika, nie DAT.
Pełny XML ma ~300 MB, ale generuje się w kilka sekund i parsuje strumieniowo.
Indeks odświeżamy tylko po zmianie wersji MAME. Sety FBNeo w ogromnej większości
nazywają się tak samo, więc ten sam indeks obsługuje oba foldery.
"""

from __future__ import annotations

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from emustart import library

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_mame(cfg: dict) -> str:
    exe = cfg.get("mame_exe") or ""
    if exe and Path(exe).is_file():
        return exe
    root = Path(cfg.get("emu_root") or "")
    for cand in (root / "MAME" / "mame.exe", root / "mame" / "mame.exe"):
        if cand.is_file():
            return str(cand)
    return ""


def _version(exe: str) -> str:
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True,
                           timeout=60, creationflags=_NO_WINDOW)
        return (r.stdout or "").strip().splitlines()[0]
    except Exception:
        return ""


def _category(el) -> str:
    if el.get("isdevice") == "yes" or el.get("runnable") == "no":
        return "device"
    if el.get("isbios") == "yes":
        return "bios"
    if el.get("ismechanical") == "yes":
        return "mech"
    return "arcade"


SUPPORT_DEFAULT = r"D:\emu\dat\Support Files"


def _ensure_columns() -> None:
    """Rok, producent, gracze, gatunek (do filtrowania) — dodane w 0.8.0."""
    con = library.db()
    cols = {r[1] for r in con.execute("PRAGMA table_info(arcade_sets)")}
    if "genre" not in cols:
        with con:
            for col in ("year", "maker", "players", "genre"):
                con.execute(f"ALTER TABLE arcade_sets ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        library.meta_set("mame_version", "")       # wymusza ponowne wczytanie z MAME


def needs_refresh() -> bool:
    _ensure_columns()
    return not library.meta_get("mame_version")


def parse_catver(cfg: dict) -> dict:
    """{set: 'Shooter / Flying Vertical'} z catver.ini (sekcja [Category])."""
    cands = [Path(cfg.get("mame_support_dir") or SUPPORT_DEFAULT) / "catver.ini"]
    mame = find_mame(cfg)
    if mame:
        cands += [Path(mame).parent / "catver.ini", Path(mame).parent / "folders" / "catver.ini"]
    for f in cands:
        if not f.is_file():
            continue
        out, on = {}, False
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("["):
                on = line.lower() == "[category]"
                continue
            if on and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    return {}


def genre_of(category: str) -> str:
    """'Shooter / Flying Vertical' → 'Shooter'; '* Mature *' pomijamy."""
    c = category.replace("* Mature *", "").strip()
    return c.split(" / ")[0].strip() if c else ""


def refresh(cfg: dict, progress=None) -> dict:
    """Odświeża tabelę arcade_sets, jeśli zmieniła się wersja MAME."""
    _ensure_columns()
    exe = find_mame(cfg)
    if not exe:
        return {"ok": False, "reason": "nie znaleziono mame.exe"}
    ver = _version(exe)
    if not ver:
        return {"ok": False, "reason": "mame.exe nie odpowiada"}
    if ver == library.meta_get("mame_version"):
        return {"ok": True, "version": ver, "cached": True}
    if progress:
        progress("Czytam listę gier z MAME…")
    tmp = Path(tempfile.gettempdir()) / "emustart_listxml.xml"
    try:
        with open(tmp, "wb") as fh:
            subprocess.run([exe, "-listxml"], stdout=fh, stderr=subprocess.DEVNULL,
                           timeout=900, creationflags=_NO_WINDOW)
        catver = parse_catver(cfg)
        rows = []
        for _ev, el in ET.iterparse(str(tmp), events=("end",)):
            if el.tag != "machine":
                continue
            nm = el.get("name") or ""
            inp = el.find("input")
            rows.append((nm, el.findtext("description") or nm, el.get("cloneof") or "",
                         el.get("romof") or "", _category(el),
                         (el.findtext("year") or "").strip("?"), el.findtext("manufacturer") or "",
                         (inp.get("players") or "") if inp is not None else "",
                         genre_of(catver.get(nm, ""))))
            el.clear()
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    with library.db() as c:
        c.execute("DELETE FROM arcade_sets")
        c.executemany("INSERT OR REPLACE INTO arcade_sets(name, title, parent, romof, category, "
                      "year, maker, players, genre) VALUES(?,?,?,?,?,?,?,?,?)", rows)
    library.meta_set("mame_version", ver)
    return {"ok": True, "version": ver, "sets": len(rows)}


def lookup(names) -> dict:
    """{set: row} dla podanych nazw setów."""
    out = {}
    con = library.db()
    names = list(names)
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        q = "SELECT * FROM arcade_sets WHERE name IN (%s)" % ",".join("?" * len(chunk))
        for r in con.execute(q, chunk):
            out[r["name"]] = dict(r)
    return out


def dependencies(setname: str) -> list:
    """Sety, z których dany set dziedziczy ROM-y (rodzic, BIOS) — w kolejności."""
    out, seen = [], {setname}
    con = library.db()
    cur = setname
    while True:
        r = con.execute("SELECT romof FROM arcade_sets WHERE name=?", (cur,)).fetchone()
        nxt = r["romof"] if r else ""
        if not nxt or nxt in seen:
            return out
        out.append(nxt)
        seen.add(nxt)
        cur = nxt
