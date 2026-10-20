"""scanner — skan `rom_root\\<system>\\` do bazy.

Gra może składać się z wielu plików i wszystkie trafiają do `games.files`, bo
cache musi skopiować komplet:
  .m3u  → playlista + płyty, które wskazuje
  .cue/.gdi → arkusz + ścieżki (.bin/.raw)
  kilka płyt „(Disc N)” / „(Disc A)” bez .m3u → jedna gra (także płyty .cue z .bin-ami
        i podtytułem po numerze: „(Disc 2) - Second Act”), playlistę dopisze launcher
  folder (PS3, Vita, X360) → cały katalog jako jedna gra; płyty .iso obok folderów
        łączą się jak wyżej
Gry, które zniknęły z NAS-a, są usuwane z bazy (wraz z wpisem cache).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from emustart import arcade, config, library, systems, translations

_DISC_RE = re.compile(r"\s*\((?:disc|disk|cd)\s*\d+[^)]*\)", re.I)
# płyta zestawu: „(Disc 1)”, „(Disc A)”, „(Disk 2 of 3)”, „(CD 1)”, „(Disc 1 - Part One)”
_DISC_TOKEN = re.compile(r"\s*\((?:disc|disk|cd)\s*(\d+|[a-z])(?![a-z])[^)]*\)", re.I)
_TAG_SPLIT = re.compile(r"\s+(?=[(\[])")
# „Disk N” to tu osobne programy / dyski danych, nie płyty jednej gry
_NO_MERGE = re.compile(r"\b(?:cd32|cdtv)\b|disk\s*writer", re.I)
NO_MERGE_SYSTEMS = {"amigawhddemos"}


def disc_of(stem: str):
    """(tytuł przed płytą, numer płyty do sortowania, reszta po płycie) albo None.
    „Gra (USA) (Disc B) (Rev 1)” → („Gra (USA)”, 2, „(Rev 1)”)."""
    m = _DISC_TOKEN.search(stem)
    if not m or not stem[:m.start()].strip():
        return None
    d = m.group(1)
    idx = int(d) if d.isdigit() else ord(d.upper()) - ord("A") + 1
    return stem[:m.start()].rstrip(), idx, stem[m.end():].strip(" -_")


# ścieżki płyt (.bin, .wav…) i obrazy CloneCD (.img obok .ccd) — nie wpisy playlisty
TRACK_EXTS = {".bin", ".raw", ".wav", ".sub", ".ape", ".flac", ".ogg", ".mp3"}


def playlist_entries(rels: list) -> list:
    """Pliki płyt zestawu do playlisty .m3u (.cue, .chd, .iso, .ccd, .gdi…), bez ścieżek
    .bin / .wav i bez .img obok .ccd; brak takich — wszystkie."""
    ccd = {str(Path(r).with_suffix("")).lower() for r in rels if r.lower().endswith(".ccd")}
    out = [r for r in rels if Path(r).suffix.lower() not in TRACK_EXTS
           and not (r.lower().endswith(".img") and str(Path(r).with_suffix("")).lower() in ccd)]
    return out or list(rels)


def mergeable(es: str, stem: str) -> bool:
    return es not in NO_MERGE_SYSTEMS and not _NO_MERGE.search(f"{es} {stem}")


def _merge_discs(units: list, es: str = "") -> list:
    """Jednostki [(plik wejściowy, [pliki])] → gry; płyty jednej gry („(Disc N)”, „(Disc A)”)
    w jednym folderze łączą się w jedną (kolejność płyt, pozycja = pierwsza płyta). Tytuł =
    część przed „(Disc …)”; dopiski po płycie (podtytuł płyty) nie rozbijają zestawu — chyba
    że ten sam numer płyty powtarza się (np. dwie wersje regionalne), wtedy rozdzielają."""
    games, groups = [], {}
    for entry, files in units:
        stem = Path(entry[0]).stem
        d = disc_of(stem) if mergeable(es, stem) else None
        if not d:
            games.append({"rel": entry[0], "files": files, "multidisc": False})
            continue
        key = (os.path.dirname(entry[0]).lower(), d[0].lower())
        groups.setdefault(key, []).append((d, entry, files))
    for members in groups.values():
        idxs = [m[0][1] for m in members]
        if len(set(idxs)) < len(idxs):
            # ten sam numer płyty dwa razy — rozdzielamy po dopisku za płytą
            sub: dict = {}
            for m in members:
                sub.setdefault(m[0][2].lower(), []).append(m)
            parts = list(sub.values())
        else:
            parts = [members]
        for part in parts:
            part.sort(key=lambda m: (m[0][1], m[1][0].lower()))
            files = [f for _d, _e, fs in part for f in fs]
            tails = {m[0][2] for m in part}
            # ten sam dopisek na każdej płycie (np. „(USA)”, „(Rev 1)”) zostaje w nazwie, jak dotąd
            name = part[0][0][0] + (f" {part[0][0][2]}" if len(tails) == 1 and part[0][0][2] else "")
            games.append({"rel": part[0][1][0], "files": files, "multidisc": len(part) > 1,
                          "set_name": name if len(part) > 1 else ""})
    return games


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


def _group_files(base: Path, files: list, exts: set, es: str = "") -> list:
    """Pliki systemu → gry: [{rel, files:[(rel,size,mtime)], multidisc}]."""
    by_rel = {f[0].lower(): f for f in files}
    covered = set()
    games, units = [], []

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
            if ext == ".m3u":
                games.append({"rel": f[0], "files": parts, "multidisc": False})
            else:
                units.append((f, parts))      # płyta .cue / .gdi — może należeć do zestawu

    # 2) pozostałe pliki z rozszerzeniem systemu; płyty bez .m3u łączymy w jedną grę
    units += [(f, [f]) for f in files if f[0].lower() not in covered
              and Path(f[0]).suffix.lower().lstrip(".") in exts]
    return games + _merge_discs(units, es)


def _folder_games(base: Path, exts: set, es: str = "") -> list:
    """Systemy z grami-katalogami (PS3, X360 GOD/XEX): każdy podkatalog to gra, pliki obok
    też — płyty .iso „(Disc N)” obok folderów łączą się w jedną grę (foldery osobno)."""
    games, loose = [], []
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
                f = (e.name, st.st_size, st.st_mtime)
                loose.append((f, [f]))
        except OSError:
            continue
    return games + _merge_discs(loose, es)


def _scan_dir(rom_dir: Path, exts: set, es: str = "") -> list:
    if not exts:   # nieznany system: wszystko poza oczywistymi śmieciami
        exts = {Path(f[0]).suffix.lower().lstrip(".") for f in _walk(rom_dir, 1)}
        exts -= systems.JUNK_EXTS
        exts.discard("")
    if "folder" in exts:
        return _folder_games(rom_dir, exts - {"folder"}, es)
    return _group_files(rom_dir, _walk(rom_dir), exts, es)


def scan_system(es: str, rom_dirs, progress=None) -> int:
    """Skan systemu z jednego lub kilku folderów. Ta sama gra (ta sama ścieżka
    względna) w kilku folderach liczy się raz — wygrywa folder wcześniejszy."""
    if isinstance(rom_dirs, (str, Path)):
        rom_dirs = [rom_dirs]
    rom_dirs = [Path(d) for d in rom_dirs]
    info = systems.info(es)
    es_kind = info["kind"]
    exts = systems.ext_set(info)
    found, seen = [], set()
    for d in rom_dirs:
        # kolekcja tłumaczeń: ścieżka liczona od folderu nadrzędnego („snes\\Gra.sfc”), bo
        # tłumaczenie często ma tę samą nazwę pliku co oryginał (język tylko w nazwie kolekcji)
        pool = es_kind != "arcade" and translations.is_pool(d.name, d.parent.name)
        ptrans = translations.pool_trans(d.name, d.parent.name, systems.match_folder) if pool else None
        for g in _scan_dir(d, exts, es):
            g["pool"] = ptrans
            if pool:
                g["rel"] = os.path.join(d.name, g["rel"])
                g["files"] = [(os.path.join(d.name, f[0]), *f[1:]) for f in g["files"]]
            key = g["rel"].lower()
            if key in seen:
                continue
            seen.add(key)
            g["src"] = str(d.parent if pool else d)
            found.append(g)

    arc = {}
    if info["kind"] == "arcade":
        arc = arcade.lookup(Path(g["rel"]).stem for g in found)

    now = time.time()
    games = []
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
            name = g.get("set_name") or stem if g["multidisc"] else stem
        if es in systems.WHD_SYSTEMS:
            title, tags = systems.whd_title(stem, demo=es == "amigawhddemos")
            name = f"{title} {tags}".strip()
        else:
            title, tags = split_title(name)
        games.append({"g": g, "name": name, "title": title or name, "tags": tags,
                      "parent": parent, "hidden": hidden, "src": g["src"],
                      "trans": "", "tbase": "", "pool": g.get("pool")})
    if info["kind"] != "arcade":
        # tłumaczenia (kolekcja „[T-En] …”, tag w nazwie) — wersje gier, nie osobne pozycje
        translations.mark(games)
    rows = [(es, x["g"]["rel"], x["name"], x["title"], x["tags"],
             json.dumps(x["g"]["files"]), sum(f[1] for f in x["g"]["files"]),
             1 if x["g"].get("is_dir") else 0, 1 if x["g"]["multidisc"] else 0,
             x["parent"], x["hidden"], now, x["src"], x["trans"], x["tbase"]) for x in games]

    # zmiana nazwy gry (np. arcade: nazwa setu → pełna nazwa z indeksu) — grafiki za nią
    old = {r["rel"]: r["name"] for r in library.db().execute("SELECT rel, name FROM games WHERE es=?", (es,))}
    from emustart import art
    for x in games:
        prev = old.get(x["g"]["rel"])
        if prev and prev != x["name"]:
            art.rename(es, prev, x["name"])
    with library.db() as c:
        c.executemany("""
            INSERT INTO games(es, rel, name, title, tags, files, size, is_dir,
                              multidisc, parent, hidden, seen, src, trans, tbase)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(es, rel) DO UPDATE SET
              name=excluded.name, title=excluded.title, tags=excluded.tags,
              files=excluded.files, size=excluded.size, is_dir=excluded.is_dir,
              multidisc=excluded.multidisc, parent=excluded.parent,
              hidden=excluded.hidden, seen=excluded.seen, src=excluded.src,
              trans=excluded.trans, tbase=excluded.tbase""", rows)
        gone = [r["id"] for r in c.execute(
            "SELECT id FROM games WHERE es=? AND seen<?", (es, now))]
        for i in range(0, len(gone), 500):
            chunk = gone[i:i + 500]
            marks = ",".join("?" * len(chunk))
            c.execute(f"DELETE FROM games WHERE id IN ({marks})", chunk)
            c.execute(f"DELETE FROM cache WHERE game_id IN ({marks})", chunk)
        c.execute("""INSERT OR REPLACE INTO systems(es, display, rom_dir, games, scanned)
                     VALUES(?,?,?,?,?)""", (es, info["display"],
                                            json.dumps([str(d) for d in rom_dirs]), len(rows), now))
    return len(rows)


def collect_systems(cfg: dict) -> tuple:
    """({system: [foldery]}, [nieznane foldery]) ze wszystkich folderów z grami.

    Foldery rozpoznawane po nazwie ES, No-Intro/Redump albo libretro. Nieznane
    (np. zrzuty archiwalne „(Flux)”) są pomijane, chyba że włączono je ręcznie
    w ustawieniach — wtedy są osobnym systemem o nazwie folderu."""
    syscfg = cfg.get("systems") or {}
    merge = {k: v for k, v in (cfg.get("system_merge") or {}).items() if k != v}
    found, unknown = {}, []
    for root in config.rom_roots(cfg):
        try:
            subdirs = sorted((d for d in Path(root).iterdir() if d.is_dir()),
                             key=lambda p: p.name.lower())
        except OSError:
            continue
        pools = []
        for d in list(subdirs):
            # kolekcja tłumaczeń w folderze z grami: „[T-En] Collection\<system>\”
            if translations.is_pool(d.name) and not systems.match_folder(translations.strip_pool_tag(d.name)):
                try:
                    pools += sorted((x for x in d.iterdir() if x.is_dir()), key=lambda p: p.name.lower())
                except OSError:
                    pass
                subdirs.remove(d)
        for d in subdirs + pools:
            # „Games”/„Demos” same w sobie nic nie mówią — w folderze „WHDLoad”
            # znaczą WHDLoad, stąd druga próba z nazwą folderu nadrzędnego
            es = systems.match_folder(d.name) or systems.match_folder(f"{Path(root).name} {d.name}")
            if not es and translations.is_pool(d.name, d.parent.name):
                # „Nintendo - Super Famicom [T-En] Collection”, „… - Speed Hacks”
                es = translations.split_system(d.name, systems.match_folder)[0]
            if not es:
                unknown.append(d)
                if (syscfg.get(d.name) or {}).get("enabled") is not True:
                    continue
                es = d.name
            es = merge.get(es, es)          # platforma scalona z inną (panel serwera)
            found.setdefault(es, []).append(d)
    return found, unknown


def scan_all(cfg: dict, progress=None) -> dict:
    """Skan wszystkich włączonych systemów. progress(text, done, total)."""
    roots = config.rom_roots(cfg)
    online = [r for r in roots if Path(r).is_dir()]
    if not online:
        return {"ok": False, "reason": "Żaden folder z grami nie jest dostępny: " + ", ".join(roots)}
    syscfg = cfg.get("systems") or {}
    found, _unknown = collect_systems(cfg)
    targets = sorted(((es, dirs) for es, dirs in found.items()
                      if (syscfg.get(es) or {}).get("enabled", True)),
                     key=lambda t: systems.info(t[0])["display"].lower())
    if any(systems.info(es)["kind"] == "arcade" for es, _d in targets):
        arcade.refresh(cfg, (lambda t: progress(t, 0, len(targets))) if progress else None)
    total = 0
    for i, (es, dirs) in enumerate(targets):
        if progress:
            progress(f"Skanuję {systems.info(es)['display']}…", i, len(targets))
        total += scan_system(es, dirs)
    if len(online) < len(roots):
        # część folderów niedostępna — nie usuwamy ich systemów z biblioteki
        return {"ok": True, "systems": len(targets), "games": total,
                "offline": [r for r in roots if r not in online]}
    with library.db() as c:   # systemy usunięte, wyłączone albo scalone z innym — razem z grami
        keep = [es for es, _d in targets]
        marks = ",".join("?" * len(keep)) or "''"
        gone = [r["id"] for r in c.execute(f"SELECT id FROM games WHERE es NOT IN ({marks})", keep)]
        for j in range(0, len(gone), 500):
            chunk = gone[j:j + 500]
            m2 = ",".join("?" * len(chunk))
            c.execute(f"DELETE FROM cache WHERE game_id IN ({m2})", chunk)
            c.execute(f"DELETE FROM games WHERE id IN ({m2})", chunk)
        c.execute(f"DELETE FROM systems WHERE es NOT IN ({marks})", keep)
    if progress:
        progress("Gotowe", len(targets), len(targets))
    return {"ok": True, "systems": len(targets), "games": total}
