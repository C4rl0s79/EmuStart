"""bios — BIOS-y dla rdzeni RetroArcha z folderu podanego w ustawieniach.

Plik `info/<rdzeń>_libretro.info` RetroArcha wymienia potrzebne BIOS-y
(`firmwareN_path = "kick34005.A500"`, ścieżki względem folderu `system`).
Przed startem gry brakujące pliki kopiujemy z `cfg["bios_dir"]` (np. folder bios
z RetroBat/Batocery — ten sam układ co `system` RetroArcha). Istniejących nie
nadpisujemy.
"""

from __future__ import annotations

import logging
import time
import re
import shutil
from pathlib import Path

log = logging.getLogger("emustart.bios")

_FW = re.compile(r'^\s*firmware\d+_path\s*=\s*"([^"]+)"', re.M)


def core_name(args: str) -> str:
    """'-L "…\\cores\\puae_libretro.dll" -f' → 'puae'."""
    m = re.search(r"([A-Za-z0-9_+-]+)_libretro\.(dll|so|dylib)", args or "")
    return m.group(1) if m else ""


def firmware(ra_root: Path, core: str) -> list:
    """Ścieżki BIOS-ów rdzenia względem folderu system (z pliku .info)."""
    try:
        text = (ra_root / "info" / f"{core}_libretro.info").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for rel in _FW.findall(text):
        rel = rel.replace("\\", "/").strip("/")
        if rel and ".." not in rel.split("/"):
            out.append(rel)
    return out


# BIOS-y, których serwer nie ma: rdzeń wymienia często kilkanaście opcjonalnych plików — bez
# tego każde uruchomienie gry pytało serwer o każdy z nich, zanim cokolwiek się pokazało
_absent: dict = {}
MISS_TTL = 1800


def sync(cfg: dict, ra_root: Path, core: str, extra: tuple = ()) -> list:
    """Pobiera z serwera EmuStart brakujące BIOS-y rdzenia (i pliki z `extra`) do folderu
    system RetroArcha (BIOS-y ustawia się w panelu serwera). Zwraca listę pobranych."""
    from emustart import installer, libsync
    if not core or not (cfg.get("server_url") and cfg.get("server_key")):
        return []
    dst_root = installer.ra_system_dir(ra_root)
    copied = []
    now = time.monotonic()
    for rel in list(firmware(ra_root, core)) + list(extra):
        dst = dst_root / rel
        if dst.exists() or now - _absent.get(rel, -MISS_TTL) < MISS_TTL:
            continue                     # jest albo serwer niedawno go nie miał (bez pytania przy każdej grze)
        try:
            if libsync.bios_from_server(cfg, rel, dst):
                copied.append(rel)
            else:
                _absent[rel] = now
        except OSError as ex:
            log.warning("BIOS %s: %s", rel, ex)
    if copied:
        log.info("BIOS-y dla %s z serwera: %s", core, ", ".join(copied))
    return copied


# WHDLoad w rdzeniu PUAE: emulowany dysk z WHDLoad leży w <saves>/PUAE/WHDLoad,
# a WHDLoad szuka Kickstartów w devs:kickstarts. Rdzeń wkłada tam tylko pliki
# .RTB — same ROM-y trzeba dołożyć, inaczej gry wymagające kickemu kończą się
# „DOS-Error #205 … devs:kickstarts/kick34005.a500”.
WHDLOAD_KICKSTARTS = ("kick33180.A500", "kick34005.A500", "kick37175.A500", "kick40063.A600",
                      "kick40068.A1200", "kick40068.A4000")


def whdload_kickstarts(cfg: dict, ra_root: Path, saves_dir: Path) -> list:
    from emustart import installer
    target = saves_dir / "PUAE" / "WHDLoad" / "Devs" / "Kickstarts"
    sources = [installer.ra_system_dir(ra_root)]
    copied = []
    for name in WHDLOAD_KICKSTARTS:
        dst = target / name
        if dst.exists():
            continue
        src = next((d / name for d in sources if (d / name).is_file()), None)
        if not src:
            continue
        try:
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied.append(name)
        except OSError as ex:
            log.warning("WHDLoad %s: %s", name, ex)
    if copied:
        log.info("Kickstarty dla WHDLoad (%s): %s", target, ", ".join(copied))
    return copied
