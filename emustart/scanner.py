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

from emustart import arcade, config, library, systems, translations

# „(Disc 1)”, „(Disc 2 of 3)”, „(Disk 1)”, „(CD 2)”, „(Disc A)” (Redump, np. 3DO D's Diner) — ale nie
# „(CD32)” (Amiga CD32) ani „(CDTV)”, „(Disk Writer)” (FDS) — to oznaczenia platformy/wydania, nie płyty
_DISC_RE = re.compile(r"\s*\((?:disc|disk)\s*(?:\d{1,2}|[a-h])\b[^)]*\)|\s*\(cd\s*(?!32\b)\d{1,2}\b[^)]*\)", re.I)


def disc_split(stem: str) -> tuple | None:
    """'C&C (USA) (Disc 1) (GDI)' → ('C&C (USA)', '(Disc 1)', '(GDI)'); bez oznaczenia płyty → None.
    Płyty jednej gry mają ten sam początek nazwy (do oznaczenia płyty); po nim bywa podtytuł płyty
    (Redump: „(Disc 1) (Allies)”, „(Disc 2) (Soviet)”)."""
    m = _DISC_RE.search(stem)
    if not m:
        return None
    return stem[:m.start()].strip(), m.group(0).strip(), stem[m.end():].strip()


def merge_discs(entries: list) -> list:
    """Pliki-płyty bez .m3u → jedna gra na (katalog, nazwa do oznaczenia płyty):
    [{rel, files, multidisc}]. Pliki bez oznaczenia płyty — każdy osobno."""
    games, discs = [], {}
    for f in entries:
        sp = disc_split(Path(f[0]).stem)
        if sp:
            discs.setdefault((os.path.dirname(f[0]).lower(), sp[0].lower()), []).append(f)
        else:
            games.append({"rel": f[0], "files": [f], "multidisc": False})
    for group in discs.values():
        group.sort(key=lambda f: f[0].lower())
        games.append({"rel": group[0][0], "files": group, "multidisc": len(group) > 1})
    return games


def multidisc_name(files: list) -> str:
    """Nazwa gry wielopłytowej. Gdy płyty różnią się tylko numerem — jak dotąd: nazwa bez oznaczenia
    płyty (te same identyfikatory gier i zapisów co w starszych wersjach); gdy mają różne podtytuły —
    nazwa do oznaczenia płyty."""
    stems = [Path(f[0]).stem for f in files]
    tails = {(disc_split(s) or ("", "", ""))[2].lower() for s in stems}
    if len(tails) == 1:
        return _DISC_RE.sub("", stems[0])
    return (disc_split(stems[0]) or (stems[0], "", ""))[0]
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
    return games + merge_discs(loose)


def _folder_games(base: Path, exts: set) -> list:
    """Systemy z grami-katalogami (PS3): każdy podkatalog to gra, pliki obok też."""
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
                loose.append((e.name, st.st_size, st.st_mtime))
        except OSError:
            continue
    # pliki obok katalogów (np. Xbox 360: „Gra (Disc 1).iso”, „Gra (Disc 2).iso”) — płyty jak w _group_files
    return games + merge_discs(loose)


def _scan_dir(rom_dir: Path, exts: set) -> list:
    if not exts:   # nieznany system: wszystko poza oczywistymi śmieciami
        exts = {Path(f[0]).suffix.lower().lstrip(".") for f in _walk(rom_dir, 1)}
        exts -= systems.JUNK_EXTS
        exts.discard("")
    if "folder" in exts:
        return _folder_games(rom_dir, exts - {"folder"})
    return _group_files(rom_dir, _walk(rom_dir), exts)


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
        for g in _scan_dir(d, exts):
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
            name = multidisc_name(g["files"]) if g["multidisc"] else stem
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
