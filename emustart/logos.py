"""logos — logo systemów do karuzeli.

Kolejność:
  1. wbudowane (assets/systems/<kod>.png, z PyLinks) — 40 systemów,
  2. pobrane z motywu Carbon dla EmulationStation (RetroPie/es-theme-carbon,
     <folder ES>/art/system.svg) — nazwy folderów ES pasują 1:1,
  3. ikona systemu z RetroArcha (assets/xmb/systematic/png/<nazwa libretro>.png),
     kopiowana lokalnie — bez sieci.
Pobrane trafiają do data/media/_systems/. Brak logo zapamiętujemy (plik .none),
żeby nie pytać sieci przy każdym starcie.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from emustart import art_sources, paths, systems

log = logging.getLogger("emustart.logos")

CARBON = "https://raw.githubusercontent.com/RetroPie/es-theme-carbon/master/{es}/art/system.svg"
_ALIAS = {"FBNEO": "ARCADE", "SUPERGRAFX": "PCENGINE", "SNESMSU1": "SNES"}
_busy: set = set()
_lock = threading.Lock()


def _dir() -> Path:
    return paths.MEDIA / "_systems"


def bundled(plat: str) -> str:
    for key in (plat, _ALIAS.get(plat, "")):
        if key and (paths.ASSETS / "systems" / f"{key}.png").is_file():
            return f"/assets/systems/{key}.png"
    return ""


def downloaded(es: str) -> str:
    for ext in ("svg", "png"):
        if (_dir() / f"{es}.{ext}").is_file():
            return f"/media/_systems/{es}.{ext}"
    return ""


def url_for(es: str) -> dict:
    """{'url', 'glow'} — glow: pobrane logo bywają ciemne, UI dodaje im poświatę."""
    u = bundled(systems.info(es)["plat"])
    if u:
        return {"url": u, "glow": False}
    u = downloaded(es)
    return {"url": u, "glow": bool(u)}


def fetch_missing(cfg: dict, es_list) -> None:
    """W tle: logo dla systemów bez wbudowanego i bez pobranego."""
    todo = []
    with _lock:
        for es in es_list:
            if es in _busy or bundled(systems.info(es)["plat"]) or downloaded(es) \
                    or (_dir() / f"{es}.none").exists():
                continue
            _busy.add(es)
            todo.append(es)
    if todo:
        threading.Thread(target=_run, args=(cfg, todo), daemon=True, name="logos").start()


def _run(cfg: dict, todo: list) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    for es in todo:
        try:
            data = art_sources.fetch(CARBON.format(es=es), timeout=15)
            if data and b"<svg" in data[:2000]:
                (d / f"{es}.svg").write_bytes(data)
                continue
            ra = Path(cfg.get("emu_root") or "") / "RetroArch" / "assets" / "xmb" / "systematic" / "png"
            icon = ra / f"{systems.info(es)['libretro']}.png"
            if icon.is_file():
                shutil.copy2(icon, d / f"{es}.png")
            elif data is not None or art_sources.online():
                (d / f"{es}.none").write_text("")       # sprawdzone — logo nie ma
        except Exception:
            log.exception("logo %s", es)
        finally:
            with _lock:
                _busy.discard(es)
