"""savemgr — menedżer zapisów i stanów gry między komputerem a telefonem.

Zapisy w grze (save'y RetroArcha, karty pamięci PS1/PS2) są wspólne i synchronizują
się same. Stany gry (savestate) leżą osobno dla każdej platformy:

    <profil>/save/retroarch/<states>/…      RetroArch na komputerze
    <profil>/save/pcsx2/<sstates>/…         PCSX2 na komputerze
    <profil>/states/android/retroarch/…     RetroArch na telefonie
    <profil>/states/android/armsx2/…        ARMSX2 na telefonie (też nethersx2)

Menedżer pokazuje stany tej gry z obu platform i kopiuje wybrany na drugą — do jej
folderu na NAS-ie; urządzenie pobiera go przy następnej grze (komputer: cały folder
stanów, telefon: pliki tej gry).

Zgodność:
  - PCSX2 ↔ ARMSX2 / AetherSX2: plik .p2s to ZIP z „PCSX2 Savestate Version.id”
    (u32). Ta sama wersja = stan się wczyta (ARMSX2 ma ten sam format co PCSX2 2.x,
    0x9A59…, i czyta też starsze). Wersję drugiej strony bierzemy z jej najnowszego stanu.
  - RetroArch: stan należy do rdzenia — ten sam rdzeń (np. SwanStation, Snes9x, mGBA)
    na obu urządzeniach zwykle się wczyta; inny rdzeń — nie.

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
    ("windows", "retroarch", "RetroArch na komputerze", "save/retroarch", "ra"),
    ("windows", "pcsx2", "PCSX2 na komputerze", "save/pcsx2", "ps2"),
    ("android", "retroarch", "RetroArch na telefonie", "states/android/retroarch", "ra"),
    ("android", "armsx2", "ARMSX2 na telefonie", "states/android/armsx2", "ps2"),
    ("android", "nethersx2", "AetherSX2 na telefonie", "states/android/nethersx2", "ps2"),
    # od 0.31.1: stany RetroArcha wspólne dla wszystkich urządzeń (emustart/rastates.py) —
    # każde urządzenie bierze je samo przed grą, tam gdzie jego RetroArch ich szuka
    ("shared", "retroarch", "RetroArch — wspólne (komputer i telefon)", "states/shared/retroarch", "ra"),
]
PLATFORM = {"windows": "Komputer", "android": "Telefon", "shared": "Wszystkie urządzenia"}

_RA_STATE = re.compile(r"^(?P<stem>.+)\.state(?P<slot>\d*)(?P<auto>\.auto)?$", re.I)
# sloty 00–09, stan wznowienia PCSX2 (.resume) i autozapis ARMSX2 przy wyjściu
# (.autosave.p2s, starsze: .autosave.2–5.p2s)
_P2S = re.compile(r"^(?P<prefix>.+)\.(?P<slot>\d{2}|resume|autosave(?:\.\d)?)\.p2s$", re.I)
P2S_VERSION = "PCSX2 Savestate Version.id"
INDEX = ".emustart-index.json"          # przedrostki stanów PS2 telefonu (Saves.kt)

# zapisy w grze (wspólne) — tylko do podglądu
_SAVE_EXT = re.compile(r"\.(srm|sav|rtc|eep|fla|mpk|sra|nv|mcr|bkr|bcr|brm|dsv|ram)$", re.I)


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
    """Stany tej gry z obu platform (najnowsze pierwsze) + zapisy w grze (wspólne)."""
    stems = {norm_stem(s) for s in stems if s}
    plat = _plat(es)
    prefixes = _game_prefixes(fs, nas, es, name, serial, learned or []) if plat in ("PS2", "") else set()
    states, latest_ver = [], {}
    for platform, emu, label, base, kind in SOURCES:
        if kind == "ps2" and plat not in ("PS2", ""):
            continue                       # stany PCSX2/ARMSX2 tylko przy grach PS2
        files = fs.scan(f"{nas}/{base}")
        newest = None
        for rel, (size, mtime) in files.items():
            fname = rel.rsplit("/", 1)[-1]
            if kind == "ra":
                m = _RA_STATE.match(fname)
                if not m or norm_stem(m.group("stem")) not in stems:
                    continue
                slot = "auto" if m.group("auto") else (m.group("slot") or "0")
                shot = rel + ".png" if rel + ".png" in files else ""
            else:
                m = _P2S.match(fname)
                if not m:
                    continue
                if newest is None or mtime > newest[1]:
                    newest = (rel, mtime)            # wersja emulatora tej platformy
                if not any(fname.startswith(p) for p in prefixes):
                    continue
                slot = m.group("slot")
                shot = ""
            states.append({"platform": platform, "emu": emu, "label": label, "kind": kind,
                           "rel": f"{base}/{rel}", "name": fname, "size": size, "mtime": mtime,
                           "slot": slot, "shot": f"{base}/{shot}" if shot else "",
                           "core": rel.split("/")[-2] if kind == "ra" and "/" in rel else ""})
        if kind == "ps2" and newest:
            latest_ver[(platform, emu)] = _version(fs, f"{nas}/{base}/{newest[0]}")
    idx_cache: dict = {}
    for st in states:
        if st["kind"] == "ps2":
            st["version"] = _version(fs, f"{nas}/{st['rel']}")
        elif st["platform"] == "shared" and st["core"]:
            # opis stanu z indeksu serwera (emustart/rastates.py): urządzenie, rodzaj zapisu
            folder = st["rel"].rsplit("/", 1)[0]
            if folder not in idx_cache:
                try:
                    d = json.loads(fs.read(f"{nas}/{folder}/.states.json") or b"{}")
                except (OSError, ValueError):
                    d = {}
                idx_cache[folder] = d if isinstance(d, dict) else {}
            info = idx_cache[folder].get(st["name"]) or {}
            st["device"], st["device_platform"] = info.get("device", ""), info.get("platform", "")
            st["state_kind"] = info.get("kind", "")
        st["targets"] = _targets(st, latest_ver)
    states.sort(key=lambda s: -s["mtime"])
    return {"states": states, "saves": _saves(fs, nas, name, stems, plat),
            "versions": {f"{p}/{e}": fmt_version(v) for (p, e), v in latest_ver.items()}}


def _version(fs, rel: str) -> int | None:
    path = getattr(fs, "_p", None)
    if path is None:
        return None
    return p2s_version(path(rel))


def _targets(st: dict, latest_ver: dict) -> list:
    """Dokąd można skopiować stan: ta sama rodzina na drugiej platformie, z oceną zgodności."""
    out = []
    if st["platform"] == "shared":
        return out                       # wspólne — każde urządzenie bierze je samo
    for platform, emu, label, _base, kind in SOURCES:
        if kind != st["kind"] or platform == st["platform"] or platform == "shared":
            continue
        if kind == "ra":
            ok, note = None, ("ten sam rdzeń na obu urządzeniach — zwykle się wczyta"
                              + (f" (stan z rdzenia {st['core']})" if st["core"] else ""))
        else:
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


def _saves(fs, nas: str, name: str, stems: set, plat: str = "") -> list:
    """Zapisy w grze — wspólne dla komputera i telefonu (synchronizują się same).
    Karty pamięci tylko przy grach swojej platformy: PS1 (DuckStation, SwanStation) i PS2
    (PCSX2, ARMSX2) — gra SNES nie pokazuje kart PlayStation."""
    out = []
    srcs = [("save/retroarch", lambda f: _SAVE_EXT.search(f) and norm_stem(f.rsplit(".", 1)[0]) in stems)]
    if plat == "PS1":
        srcs.append(("save/duckstation/memcards", lambda f: f.lower().endswith(".mcd")
                     and (f.startswith(name + "_") or f.lower().startswith("shared_card"))))
    if plat == "PS2":
        srcs.append(("save/pcsx2/memcards", lambda f: f.lower().endswith(".ps2")))
    for base, test in srcs:
        for rel, (size, mtime) in fs.scan(f"{nas}/{base}").items():
            fname = rel.rsplit("/", 1)[-1]
            if test(fname):
                out.append({"rel": f"{base}/{rel}", "name": fname, "size": size, "mtime": mtime,
                            "shared": base != "save/retroarch" and not fname.startswith(name)})
    out.sort(key=lambda s: -s["mtime"])
    return out


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
    for shot in (src_rel + ".png",):                 # miniatura stanu RetroArcha
        img = fs.read(f"{nas}/{shot}") if kind == "ra" else None
        if img:
            fs.write(f"{nas}/{dst}.png", img, time.time())
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
    """Podfolder docelowy: komputer trzyma stany w podfolderze (states, sstates, czasem
    też wg rdzenia), telefon — płasko."""
    if base.startswith("states/android"):
        return ""
    files = fs.scan(f"{nas}/{base}")
    if kind == "ps2":
        dirs = [r.rsplit("/", 1)[0] for r in files if r.lower().endswith(".p2s") and "/" in r]
        return (max(set(dirs), key=dirs.count) if dirs else "sstates") + "/"
    # RetroArch: stan należy do rdzenia — folder rdzenia stanu źródłowego (telefon od 0.31.1
    # trzyma stany w states/android/retroarch/<rdzeń>/). Bez rdzenia nie zgadujemy (wcześniej
    # „najczęstszy folder” wkładał stan SNES do folderu rdzenia Amigi).
    core = PurePosixPath(src_rel).parent.name
    if core.lower() in ("retroarch", "states", ""):
        # stan bez rdzenia (starsza aplikacja): folder rdzenia, w którym komputer ma stany
        # TEJ gry — tylko gdy jest dokładnie jeden; kilka (np. bsnes i Snes9x) = nie zgadujemy
        m = _RA_STATE.match(PurePosixPath(src_rel).name)
        stem = norm_stem(m.group("stem")) if m else ""
        same = {r.rsplit("/", 1)[0] for r in files if "/" in r
                and (mm := _RA_STATE.match(r.rsplit("/", 1)[-1])) and norm_stem(mm.group("stem")) == stem}
        if len(same) == 1:
            return same.pop() + "/"
        raise ValueError("Stan z telefonu bez informacji o rdzeniu (zapisany starszą wersją aplikacji) — "
                         "zagraj raz na telefonie z wersją 0.31.1 lub nowszą, stan zapisze się w folderze rdzenia.")
    dirs = {r.rsplit("/", 2)[0] for r in files if _RA_STATE.match(r.rsplit("/", 1)[-1]) and r.count("/") >= 2}
    root = "states" if "states" in dirs or not dirs else sorted(dirs)[0]
    for d in (r.rsplit("/", 1)[0] for r in files if "/" in r):
        if d.rsplit("/", 1)[-1].lower() == core.lower():
            return d + "/"                      # istniejący folder rdzenia (wielkość liter jak na PC)
    return f"{root}/{core}/"


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
