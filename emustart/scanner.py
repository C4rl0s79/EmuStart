"""scanner — skan `rom_root\\<system>\\` do bazy.

Gra może składać się z wielu plików i wszystkie trafiają do `games.files`, bo
cache musi skopiować komplet:
  .m3u  → playlista + płyty, które wskazuje
  .cue/.gdi → arkusz + ścieżki (.bin/.raw)
  kilka płyt „(Disc N)” bez .m3u → jedna gra, playlistę dopisze launcher
  folder (PS3, Vita, X360) → cały katalog jako jedna gra
Gry, które zniknęły z NAS-a, są usuwane z bazy (wraz z wpisem cache).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from emustart import arcade, library, systems

_DISC_RE = re.compile(r"\s*\((?:disc|disk|cd)\s*\d+[^)]*\)", re.I)
_TAG_SPLIT = re.compile(r"\s+(?=[(\[])")


def split_title(name: str) -> tuple:
    """'Final Fantasy VII (USA) (Disc 1)' → ('Final Fantasy VII', '(USA) (Disc 1)')."""
    parts = _TAG_SPLIT.split(name, maxsplit=1)
    return parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")


def _walk(base: Path, max_depth: int = 3) -> list:
    """[(rel, size, mtime)] — DirEntry.stat() na Windows nie robi dodatkowego
    zapytania do serwera, więc skan przez Tailscale jest znośny."""
    out = []
    stack = [(base, 0)]
    while stack:
        d, depth = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            if depth < max_depth:
                                stack.append((Path(e.path), depth + 1))
                        elif e.is_file():
                            st = e.stat()
                            out.append((str(Path(e.path).relative_to(base)),
                                        st.st_size, st.st_mtime))
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def _sheet_refs(path: Path) -> list:
    """Pliki, które wskazuje .m3u / .cue / .gdi (ścieżki względne do jego katalogu)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    ext = path.suffix.lower()
    refs = []
    if ext == ".m3u":
        refs = [ln.strip() for ln in text.splitlines()
                if ln.strip() and not ln.strip().startswith("#")]
    elif ext == ".cue":
        refs = re.findall(r'FILE\s+"([^"]+)"', text, re.I) or \
            re.findall(r'FILE\s+(\S+)\s+\w+', text, re.I)
    elif ext == ".gdi":
        for line in text.splitlines()[1:]:
            m = re.search(r'"([^"]+)"', line)
            parts = line.split()
            if m:
                refs.append(m.group(1))
            elif len(parts) >= 5:
                refs.append(parts[4])
    return refs


def _norm_rel(rel_dir: str, ref: str) -> str:
    return os.path.normpath(os.path.join(rel_dir, ref)).lower()


def _group_files(base: Path, files: list, exts: set) -> list:
    """Pliki systemu → gry: [{rel, files:[(rel,size,mtime)], multidisc}]."""
    by_rel = {f[0].lower(): f for f in files}
    covered = set()
    games = []

    # 1) .m3u / .cue / .gdi pochłaniają pliki, które wskazują
    for ext in (".m3u", ".cue", ".gdi"):
        if ext.lstrip(".") not in exts and ext != ".cue":
            continue
        for f in files:
            if not f[0].lower().endswith(ext) or f[0].lower() in covered:
                continue
            rel_dir = os.path.dirname(f[0])
            parts = [f]
            for ref in _sheet_refs(base / f[0]):
                hit = by_rel.get(_norm_rel(rel_dir, ref))
                if hit:
                    parts.append(hit)
                    # płyta z m3u może mieć własny .cue z .bin-ami
                    if hit[0].lower().endswith(".cue"):
                        for r2 in _sheet_refs(base / hit[0]):
                            h2 = by_rel.get(_norm_rel(os.path.dirname(hit[0]), r2))
                            if h2:
                                parts.append(h2)
            for p in parts:
                covered.add(p[0].lower())
            games.append({"rel": f[0], "files": parts, "multidisc": False})

    # 2) pozostałe pliki z rozszerzeniem systemu; płyty bez .m3u łączymy w jedną grę
    loose = [f for f in files if f[0].lower() not in covered
             and Path(f[0]).suffix.lower().lstrip(".") in exts]
    discs: dict = {}
    for f in loose:
        stem = Path(f[0]).stem
        if _DISC_RE.search(stem):
            key = (os.path.dirname(f[0]).lower(), _DISC_RE.sub("", stem).lower())
            discs.setdefault(key, []).append(f)
        else:
            games.append({"rel": f[0], "files": [f], "multidisc": False})
    for group in discs.values():
        group.sort(key=lambda f: f[0].lower())
        games.append({"rel": group[0][0], "files": group, "multidisc": len(group) > 1})
    return games


def _folder_games(base: Path, exts: set) -> list:
    """Systemy z grami-katalogami (PS3): każdy podkatalog to gra, pliki obok też."""
    games = []
    try:
        entries = list(os.scandir(base))
    except OSError:
        return games
    for e in entries:
        try:
            if e.is_dir():
                files = [(os.path.join(e.name, r), s, m) for r, s, m in _walk(Path(e.path), 8)]
                if files:
                    games.append({"rel": e.name, "files": files, "is_dir": True,
                                  "multidisc": False})
            elif Path(e.name).suffix.lower().lstrip(".") in exts:
                st = e.stat()
                games.append({"rel": e.name, "files": [(e.name, st.st_size, st.st_mtime)],
                              "multidisc": False})
        except OSError:
            continue
    return games


def scan_system(es: str, rom_dir: Path, progress=None) -> int:
    info = systems.info(es)
    exts = systems.ext_set(info)
    if not exts:   # nieznany system: wszystko poza oczywistymi śmieciami
        exts = {Path(f[0]).suffix.lower().lstrip(".") for f in _walk(rom_dir, 1)}
        exts -= systems.JUNK_EXTS
        exts.discard("")
    if "folder" in exts:
        found = _folder_games(rom_dir, exts - {"folder"})
    else:
        found = _group_files(rom_dir, _walk(rom_dir), exts)

    arc = {}
    if info["kind"] == "arcade":
        arc = arcade.lookup(Path(g["rel"]).stem for g in found)

    now = time.time()
    rows = []
    for g in found:
        stem = Path(g["rel"]).stem if not g.get("is_dir") else g["rel"]
        parent, hidden = "", 0
        if info["kind"] == "arcade":
            a = arc.get(stem)
            name = a["title"] if a else stem
            if a:
                parent = a["parent"]
                hidden = 1 if a["category"] in ("bios", "device") else 0
        else:
            name = _DISC_RE.sub("", stem) if g["multidisc"] else stem
        title, tags = split_title(name)
        rows.append((es, g["rel"], name, title or name, tags,
                     json.dumps(g["files"]), sum(f[1] for f in g["files"]),
                     1 if g.get("is_dir") else 0, 1 if g["multidisc"] else 0,
                     parent, hidden, now))

    with library.db() as c:
        c.executemany("""
            INSERT INTO games(es, rel, name, title, tags, files, size, is_dir,
                              multidisc, parent, hidden, seen)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(es, rel) DO UPDATE SET
              name=excluded.name, title=excluded.title, tags=excluded.tags,
              files=excluded.files, size=excluded.size, is_dir=excluded.is_dir,
              multidisc=excluded.multidisc, parent=excluded.parent,
              hidden=excluded.hidden, seen=excluded.seen""", rows)
        gone = [r["id"] for r in c.execute(
            "SELECT id FROM games WHERE es=? AND seen<?", (es, now))]
        for i in range(0, len(gone), 500):
            chunk = gone[i:i + 500]
            marks = ",".join("?" * len(chunk))
            c.execute(f"DELETE FROM games WHERE id IN ({marks})", chunk)
            c.execute(f"DELETE FROM cache WHERE game_id IN ({marks})", chunk)
        c.execute("""INSERT OR REPLACE INTO systems(es, display, rom_dir, games, scanned)
                     VALUES(?,?,?,?,?)""", (es, info["display"], str(rom_dir), len(rows), now))
    return len(rows)


def scan_all(cfg: dict, progress=None) -> dict:
    """Skan wszystkich włączonych systemów. progress(text, done, total)."""
    root = Path(cfg.get("rom_root") or "")
    if not root.is_dir():
        return {"ok": False, "reason": f"Folder z grami niedostępny: {root}"}
    syscfg = cfg.get("systems") or {}
    targets = [d for d in sorted(root.iterdir(), key=lambda p: p.name.lower())
               if d.is_dir() and (syscfg.get(d.name) or {}).get("enabled", True)]
    if any(systems.info(d.name)["kind"] == "arcade" for d in targets):
        arcade.refresh(cfg, (lambda t: progress(t, 0, len(targets))) if progress else None)
    total = 0
    for i, d in enumerate(targets):
        if progress:
            progress(f"Skanuję {systems.info(d.name)['display']}…", i, len(targets))
        total += scan_system(d.name, d)
    with library.db() as c:   # systemy usunięte z NAS-a albo wyłączone
        keep = [d.name for d in targets]
        marks = ",".join("?" * len(keep)) or "''"
        c.execute(f"DELETE FROM systems WHERE es NOT IN ({marks})", keep)
    if progress:
        progress("Gotowe", len(targets), len(targets))
    return {"ok": True, "systems": len(targets), "games": total}
