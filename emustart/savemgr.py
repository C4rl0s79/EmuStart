"""savemgr — menedżer stanów PS2 między komputerem a telefonem.

Zapisy w grze, karty pamięci i stany RetroArcha synchronizują się paczkami z serwerem
(emustart/savesync.py, syncclient.py, Saves.kt) — jedna wersja na wszystkich urządzeniach.
Stany PS2 (savestate) leżą osobno dla każdej platformy (różne wersje formatu):

    <profil>/save/pcsx2/<sstates>/…         PCSX2 na komputerze
    <profil>/states/android/armsx2/…        ARMSX2 na telefonie (też nethersx2)

Menedżer pokazuje stany tej gry z obu platform i kopiuje wybrany na drugą — do jej
folderu na NAS-ie; urządzenie pobiera go przy następnej grze.

Zgodność:
  - PCSX2 ↔ ARMSX2 / AetherSX2: plik .p2s to ZIP z „PCSX2 Savestate Version.id”
    (u32). Ta sama wersja = stan się wczyta (ARMSX2 ma ten sam format co PCSX2 2.x,
    0x9A59…, i czyta też starsze). Wersję drugiej strony bierzemy z jej najnowszego stanu.

Działa na folderze profili przez DirFs (serwer: jego własny dysk; komputer bez
serwera: udział SMB). Komputer z serwerem EmuStart i telefon wołają serwer
(/v1/savemgr/…), który robi to samo u siebie.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import struct
import time
import zipfile
from pathlib import PurePosixPath

log = logging.getLogger("emustart.savemgr")

# (platforma, emulator, opis, folder w profilu, rodzaj)
SOURCES = [
    ("windows", "pcsx2", "PCSX2 na komputerze", "save/pcsx2", "ps2"),
    ("android", "armsx2", "ARMSX2 na telefonie", "states/android/armsx2", "ps2"),
    ("android", "nethersx2", "AetherSX2 na telefonie", "states/android/nethersx2", "ps2"),
]
PLATFORM = {"windows": "Komputer", "android": "Telefon"}

# sloty 00–09, stan wznowienia PCSX2 (.resume) i autozapis ARMSX2 przy wyjściu
# (.autosave.p2s, starsze: .autosave.2–5.p2s)
_P2S = re.compile(r"^(?P<prefix>.+)\.(?P<slot>\d{2}|resume|autosave(?:\.\d)?)\.p2s$", re.I)
P2S_VERSION = "PCSX2 Savestate Version.id"
INDEX = ".emustart-index.json"          # przedrostki stanów PS2 telefonu (Saves.kt)

def norm_stem(s: str) -> str:
    """Porównanie nazw stanów RetroArcha bez różnic „_” / spacja i wielkości liter
    (ZIP „Chrono Trigger (USA) (MSU1)” — ROM w środku „Chrono_Trigger_(USA)_(MSU1)”)."""
    return re.sub(r"[\s_]+", " ", s).strip().lower()


def zip_stems(path) -> list:
    """Nazwy plików gry w archiwum (bez rozszerzeń i plików towarzyszących): RetroArch
    nazywa stan i zapis od pliku w środku archiwum, nie od samego ZIP-a."""
    from emustart.launcher import COMPANION_EXTS
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
    except (OSError, zipfile.BadZipFile):
        return []
    out = []
    for n in names:
        base = PurePosixPath(n.replace("\\", "/")).name
        stem, _, ext = base.rpartition(".")
        if stem and ext.lower() not in COMPANION_EXTS:
            out.append(stem)
    return out


def _src(platform: str, emu: str):
    return next((s for s in SOURCES if s[0] == platform and s[1] == emu), None)


def p2s_version(path) -> int | None:
    """Wersja formatu stanu PCSX2/ARMSX2 (z pliku w ZIP-ie .p2s)."""
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read(P2S_VERSION)
        return struct.unpack("<I", data[:4])[0] if len(data) >= 4 else None
    except (OSError, KeyError, zipfile.BadZipFile, struct.error):
        return None


def fmt_version(v: int | None) -> str:
    return f"{v >> 16:04X}.{v & 0xFFFF:04X}" if v else "?"


def _game_prefixes(fs, nas: str, es: str, name: str, serial: str, learned: list) -> set:
    """Przedrostki stanów PS2 tej gry: numer płyty z metadanych („SLUS-21115 (”),
    poznane przez EmuStart na komputerze i przez telefon (indeks na serwerze)."""
    out = {p for p in learned if p}
    if serial:
        out.add(serial + " (")
    for _p, emu, _l, base, kind in SOURCES:
        if kind != "ps2" or _p != "android":
            continue
        try:
            idx = json.loads((fs.read(f"{nas}/{base}/{INDEX}") or b"{}").decode("utf-8"))
        except (OSError, ValueError):
            continue
        out.update(p + "." for p in idx.get(f"{es}/{name}") or [])
    return out


def catalog(fs, nas: str, es: str, name: str, stems: list, serial: str = "",
            learned: list | None = None) -> dict:
    """Stany PS2 tej gry z obu platform (najnowsze pierwsze). Zapisy w grze i stany
    RetroArcha — paczki z serwerem (stan w opcjach gry: „Zapisy na serwerze”)."""
    plat = _plat(es)
    states, latest_ver = [], {}
    if plat not in ("PS2", ""):
        return {"states": states, "saves": [], "versions": {}}
    prefixes = _game_prefixes(fs, nas, es, name, serial, learned or [])
    for platform, emu, label, base, kind in SOURCES:
        files = fs.scan(f"{nas}/{base}")
        newest = None
        for rel, (size, mtime) in files.items():
            fname = rel.rsplit("/", 1)[-1]
            m = _P2S.match(fname)
            if not m:
                continue
            if newest is None or mtime > newest[1]:
                newest = (rel, mtime)            # wersja emulatora tej platformy
            if not any(fname.startswith(p) for p in prefixes):
                continue
            states.append({"platform": platform, "emu": emu, "label": label, "kind": kind,
                           "rel": f"{base}/{rel}", "name": fname, "size": size, "mtime": mtime,
                           "slot": m.group("slot"), "shot": "", "core": ""})
        if newest:
            latest_ver[(platform, emu)] = _version(fs, f"{nas}/{base}/{newest[0]}")
    for st in states:
        st["version"] = _version(fs, f"{nas}/{st['rel']}")
        st["targets"] = _targets(st, latest_ver)
    states.sort(key=lambda s: -s["mtime"])
    return {"states": states, "saves": [],
            "versions": {f"{p}/{e}": fmt_version(v) for (p, e), v in latest_ver.items()}}

def _version(fs, rel: str) -> int | None:
    path = getattr(fs, "_p", None)
    if path is None:
        return None
    return p2s_version(path(rel))


def _targets(st: dict, latest_ver: dict) -> list:
    """Dokąd można skopiować stan: ta sama rodzina na drugiej platformie, z oceną zgodności."""
    out = []
    for platform, emu, label, _base, kind in SOURCES:
        if kind != st["kind"] or platform == st["platform"]:
            continue
        src, dst = st.get("version"), latest_ver.get((platform, emu))
        if not src:
            ok, note = False, "nie da się odczytać wersji stanu"
        elif not dst:
            ok, note = None, f"brak stanów {label} — wersja nieznana (stan: {fmt_version(src)})"
        elif src == dst:
            ok, note = True, f"ten sam format ({fmt_version(src)})"
        elif emu == "armsx2" and src < dst:
            ok, note = None, f"starszy format {fmt_version(src)} — ARMSX2 czyta starsze, spróbuj"
        else:
            ok, note = False, f"inny format: stan {fmt_version(src)}, {label} {fmt_version(dst)}"
        out.append({"platform": platform, "emu": emu, "label": label, "ok": ok, "note": note})
    return out


def _plat(es: str) -> str:
    """Platforma systemu (PS1, PS2, SNES…); '' = nieznana."""
    from emustart import systems
    try:
        return systems.info(es)["plat"] if es else ""
    except Exception:
        return ""


def copy(fs, nas: str, src_rel: str, platform: str, emu: str, es: str, name: str,
         device: str = "") -> dict:
    """Kopia stanu na drugą platformę (folder na NAS-ie). Istniejący plik o tej
    nazwie trafia najpierw do kopii zapasowej profilu."""
    src_rel = src_rel.strip("/")
    target = _src(platform, emu)
    if not target:
        return {"ok": False, "reason": "Nieznany emulator docelowy."}
    if not (src_rel.startswith("save/") or src_rel.startswith("states/")) or ".." in src_rel:
        return {"ok": False, "reason": "Zła ścieżka stanu."}
    data = fs.read(f"{nas}/{src_rel}")
    if data is None:
        return {"ok": False, "reason": "Nie ma już tego stanu."}
    fname = _equivalent_name(PurePosixPath(src_rel).name, platform)
    _p, _e, label, base, kind = target
    try:
        sub = _dest_dir(fs, nas, base, kind, src_rel)
    except ValueError as ex:
        return {"ok": False, "reason": str(ex)}
    dst = f"{base}/{sub}{fname}" if sub else f"{base}/{fname}"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    old = fs.read(f"{nas}/{dst}")
    if old is not None:
        if old == data:
            return {"ok": True, "rel": dst, "same": True, "label": label}
        fs.write(f"{nas}/_backup/{stamp}-menedzer-zapisow/{dst}", old)
    fs.write(f"{nas}/{dst}", data, time.time())
    if kind == "ps2" and platform == "android":
        _index_add(fs, nas, base, es, name, fname)
    if platform == "windows":
        # znacznik zmian rodziny: komputer porówna folder z NAS-em i pobierze nowy plik
        fs.write(f"{nas}/{base}/.emustart-stamp",
                 f"{time.time():.3f}-{device or socket.gethostname()}-menedzer".encode("utf-8"))
    log.info("menedżer zapisów: %s → %s", src_rel, dst)
    return {"ok": True, "rel": dst, "label": label}


def _equivalent_name(fname: str, platform: str) -> str:
    """Stan „przy wyjściu” ma inną nazwę w każdym emulatorze: PCSX2 — `.resume.p2s`
    (Wznów grę), ARMSX2 — `.autosave.p2s` (wczytywany sam przy starcie). Kopia trafia pod
    nazwę, której emulator docelowy używa; starsze autozapisy (.autosave.2–5) zostają."""
    m = _P2S.match(fname)
    if not m:
        return fname
    slot = m.group("slot").lower()
    if platform == "windows" and slot == "autosave":
        return f"{m.group('prefix')}.resume.p2s"
    if platform == "android" and slot == "resume":
        return f"{m.group('prefix')}.autosave.p2s"
    return fname


def _dest_dir(fs, nas: str, base: str, kind: str, src_rel: str) -> str:
    """Podfolder docelowy: komputer trzyma stany PS2 w podfolderze (sstates), telefon — płasko."""
    if base.startswith("states/android"):
        return ""
    files = fs.scan(f"{nas}/{base}")
    dirs = [r.rsplit("/", 1)[0] for r in files if r.lower().endswith(".p2s") and "/" in r]
    return (max(set(dirs), key=dirs.count) if dirs else "sstates") + "/"


def _index_add(fs, nas: str, base: str, es: str, name: str, fname: str) -> None:
    m = _P2S.match(fname)
    if not m:
        return
    rel = f"{nas}/{base}/{INDEX}"
    try:
        idx = json.loads((fs.read(rel) or b"{}").decode("utf-8"))
    except ValueError:
        idx = {}
    key = f"{es}/{name}"
    have = idx.setdefault(key, [])
    if m.group("prefix") not in have:
        have.append(m.group("prefix"))
        fs.write(rel, json.dumps(idx, ensure_ascii=False).encode("utf-8"))
