"""emulators — wykrywanie emulatorów w `emu_root` i budowa wywołania.

Logika dopasowania pochodzi z PyLinksWeb (`services/emulators.py`), ale kluczem
jest tu nazwa systemu libretro z `systems.py`: rdzeń RetroArcha sam deklaruje
w `info/*.info`, jakie bazy obsługuje, więc to najpewniejsze dopasowanie.

Placeholdery w argumentach:
  %ROM%     pełna ścieżka do gry (w cudzysłowie)
  %ROMDIR%  katalog gry (MAME: -rompath)
  %SET%     nazwa pliku bez rozszerzenia (MAME: nazwa setu)
Brak %ROM% / %SET% → ścieżka gry trafia na koniec.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

# argumenty sprawdzone w tekstach pomocy samych exe (patrz PyLinksWeb)
EMU_ARGS: dict = {
    "retroarch": "-f",
    "duckstation": "-fullscreen -nogui",
    "pcsx2": "-fullscreen -nogui",
    "rpcs3": "--no-gui",
    "dolphin": "-b -e",
    "cemu": "-f -g",
    "ares": "--fullscreen",
    "mgba": "-f",
    "ppsspp": "--fullscreen --escape-exit",
    "azahar": "-f",
    "snes9x": "-fullscreen",
    "ryujinx": "--fullscreen",
    "xemu": "-full-screen -dvd_path %ROM%",
    "shadps4": "--fullscreen true -g %ROM%",
    "mame": '-rompath "%ROMDIR%" -skip_gameinfo %SET%',
    "flycast": "",
    "melonds": "-f",
    "xenia": "--fullscreen",
    "vita3k": "-F",
}

# preferowane emulatory samodzielne (podłańcuch nazwy exe), kolejność = priorytet
STANDALONE_PREF: dict = {
    "PS1": ["duckstation"], "PS2": ["pcsx2"], "PS3": ["rpcs3"], "PSP": ["ppsspp"],
    "PSVITA": ["vita3k"], "GCN": ["dolphin"], "WII": ["dolphin"], "WIIU": ["cemu"],
    "NSW": ["ryujinx", "eden", "citron"], "3DS": ["azahar", "citra"],
    "NDS": ["melonds"], "N64": ["ares"], "SNES": ["snes9x"], "SNESMSU1": ["snes9x"],
    "GBA": ["mgba"], "GB": ["mgba"], "GBC": ["mgba"], "DC": ["flycast"],
    "X360": ["xenia"], "XBOX": ["xemu"], "MAME": ["mame"], "PS4": ["shadps4"],
}

# rdzeń → kody platform; zapas dla rdzeni bez pliku .info
CORE_PLATS: dict = {
    "swanstation": "PS1", "duckstation": "PS1", "mednafen_psx_hw": "PS1",
    "pcsx_rearmed": "PS1", "pcsx2": "PS2", "ppsspp": "PSP",
    "nestopia": "NES", "fceumm": "NES", "mesen": "NES",
    "snes9x": "SNES SNESMSU1", "bsnes": "SNES SNESMSU1",
    "mupen64plus_next": "N64", "parallel_n64": "N64",
    "gambatte": "GB GBC", "sameboy": "GB GBC", "mgba": "GBA GB GBC", "vba_next": "GBA",
    "melonds": "NDS", "desmume": "NDS", "dolphin": "GCN WII",
    "mednafen_saturn": "SATURN", "kronos": "SATURN", "yabasanshiro": "SATURN",
    "flycast": "DC", "genesis_plus_gx": "MD SMS GG SEGACD",
    "picodrive": "MD SMS GG 32X SEGACD", "mednafen_pce": "PCENGINE",
    "mednafen_pce_fast": "PCENGINE", "mednafen_supergrafx": "SUPERGRAFX PCENGINE",
    "fbneo": "FBNEO MAME NEOGEO", "mame": "MAME", "mame2003_plus": "MAME",
    "stella": "ATARI2600", "a5200": "ATARI5200", "atari800": "ATARI5200",
    "prosystem": "ATARI7800", "virtualjaguar": "JAGUAR", "handy": "LYNX",
    "opera": "3DO", "puae": "AMIGA", "vice_x64": "C64", "bluemsx": "MSX MSX2",
    "fmsx": "MSX MSX2", "mednafen_ngp": "NGP", "mednafen_wswan": "WSWAN",
    "o2em": "ODYSSEY2", "fuse": "ZX", "gw": "GW", "quasi88": "PC88", "np2kai": "PC98",
}

def cores_for(plat: str) -> list:
    """Rdzenie RetroArcha dla platformy: najpierw domyślny (installer.CORES), potem
    pozostałe pasujące (CORE_PLATS) — do wyboru w aplikacji na Androida."""
    from emustart import installer
    out = [installer.CORES[plat]] if plat in installer.CORES else []
    for core, plats in CORE_PLATS.items():
        if plat in plats.split() and core not in out:
            out.append(core)
    return out


# emulatory czytające ZIP same — dla nich nie rozpakowujemy
ZIP_NATIVE = ("retroarch", "mame", "snes9x", "mgba", "ares", "melonds",
              "fceux", "mesen", "project64")

# playlisty .m3u (z PyLinksWeb core/discs.py)
_M3U_YES = ("duckstation", "swanstation", "retroarch", "mednafen", "beetle",
            "mgba", "ppsspp", "pcsx_rearmed", "picodrive", "genesis_plus")

_SKIP_DIRS = {"update_staging", "updates", "update", "backup", "old", "temp",
              "tmp", "cache", "downloads", "_old", "uninstall"}
_SKIP_EXE = re.compile(r"(unins|uninstall|updater|setup|crash|tool|chdman|"
                       r"-room$|dsptool|vcredist|dxsetup)", re.I)


def family(exe: str) -> str:
    """Rodzina emulatora z nazwy exe: 'retroarch', 'duckstation', 'pcsx2'…"""
    stem = Path(exe or "").stem.lower()
    for fam in list(EMU_ARGS) + ["fceux", "mesen", "project64", "eden", "citron",
                                 "citra", "redream", "epsxe", "mednafen", "teknoparrot"]:
        if fam in stem:
            return fam
    return stem


def zip_native(exe: str) -> bool:
    return family(exe) in ZIP_NATIVE


def supports_m3u(exe: str) -> bool:
    stem = Path(exe or "").stem.lower()
    return any(k in stem for k in _M3U_YES)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


_OLD_DIR = re.compile(r"(^|[_\-\s.])(old|backup|bak)($|[_\-\s.])", re.I)


def _binaries(root: Path) -> list:
    """Exe emulatorów; przy kilku kopiach tego samego programu (stara wersja,
    kopia dołączona do TeknoParrota) wygrywa najpłytsza."""
    best: dict = {}
    for pat in ("*.exe", "*/*.exe", "*/*/*.exe"):
        try:
            for p in root.glob(pat):
                dirs = p.relative_to(root).parts[:-1]
                if ({x.lower() for x in dirs} & _SKIP_DIRS
                        or any(_OLD_DIR.search(x) for x in dirs)
                        or _SKIP_EXE.search(p.stem)):
                    continue
                key = p.stem.lower()
                if key not in best or len(dirs) < len(best[key].relative_to(root).parts) - 1:
                    best[key] = p
        except OSError:
            pass
    return sorted(best.values())


def _core_info(ra: Path) -> dict:
    """{rdzeń: (nazwa wyświetlana, {znormalizowane bazy})} z info/*.info."""
    out = {}
    for f in (ra.parent / "info").glob("*_libretro.info"):
        try:
            t = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        def field(n):
            m = re.search(rf'^{n}\s*=\s*"([^"]*)"', t, re.M)
            return m.group(1) if m else ""
        out[f.stem[:-len("_libretro")]] = (
            field("corename") or field("display_name"),
            {_norm(x) for x in field("database").split("|") if x})
    return out


_scan_memo: dict = {}


def forget_scan() -> None:
    """Zapomnij zapamiętany skan katalogu emulatorów (np. po instalacji nowego)."""
    _scan_memo.clear()


def _scan_root(root: Path) -> tuple:
    """(exe, {retroarch.exe: info rdzeni}) — skan dysku raz, nie na każdy system."""
    key = str(root).lower()
    if key not in _scan_memo:
        bins = _binaries(root)
        _scan_memo[key] = (bins, {str(b): _core_info(b) for b in bins
                                  if b.stem.lower() == "retroarch"})
    return _scan_memo[key]


def options_for(sysinfo: dict, emu_root: str) -> list:
    """Wszystkie pasujące emulatory dla systemu: [{label, exe, args, type}].
    Kolejność: emulatory samodzielne (wg preferencji), potem rdzenie RetroArch."""
    root = Path(emu_root or "")
    if not root.is_dir():
        return []
    plat = sysinfo.get("plat", "")
    want_db = _norm(sysinfo.get("libretro", ""))
    pref = STANDALONE_PREF.get(plat, [])
    first, cores = [], []
    bins, infos = _scan_root(root)
    for exe in bins:
        stem = exe.stem.lower()
        if stem == "retroarch":
            info = infos.get(str(exe), {})
            for cf in sorted((exe.parent / "cores").glob("*_libretro.dll")):
                core = cf.stem[:-len("_libretro")]
                name, dbs = info.get(core, (core, set()))
                ok = (want_db and want_db in dbs) or plat in CORE_PLATS.get(core, "").split()
                if ok:
                    cores.append({"label": f"RetroArch: {name or core}", "exe": str(exe),
                                  "args": f'-L "{cf}" ' + EMU_ARGS["retroarch"],
                                  "type": "retroarch"})
            continue
        hit = next((p for p in pref if p in stem), None)
        if hit:
            first.append({"label": exe.stem, "exe": str(exe),
                          "args": EMU_ARGS.get(hit, ""), "type": "standalone"})
    # wg listy preferencji; przy kilku exe jednego emulatora (mgba / mgba-sdl)
    # wygrywa ten o najkrótszej nazwie, czyli zwykle główny program
    first.sort(key=lambda o: (next((i for i, p in enumerate(pref)
                                    if p in Path(o["exe"]).stem.lower()), 99),
                              len(Path(o["exe"]).stem)))
    return first + cores


def default_option(sysinfo: dict, emu_root: str) -> dict:
    opts = options_for(sysinfo, emu_root)
    return opts[0] if opts else {}


def build_command(exe: str, args: str, rom: str) -> list:
    """Lista argumentów procesu dla gry `rom`."""
    p = Path(rom)
    filled = (args or "").replace("%ROMDIR%", str(p.parent)).replace("%SET%", p.stem)
    has_rom = "%ROM%" in filled
    try:
        parts = shlex.split(filled, posix=False)
    except ValueError:
        parts = filled.split()
    out = [exe]
    for a in parts:
        if a == "%ROM%":
            out.append(str(p))
        else:
            out.append(a.replace("%ROM%", str(p)).strip('"') if '"' in a else a)
    if not has_rom and "%SET%" not in (args or ""):
        out.append(str(p))
    return out
