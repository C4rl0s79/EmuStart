"""paths — położenie programu i jego katalogów.

Program jest przenośny: wszystko (config, baza, grafiki, cache) leży obok
`emustart.exe` / `main.py`, dzięki czemu start nie zależy od NAS-a.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """Pliki dołączone do programu (web/, assets/) — w paczce PyInstallera
    leżą w katalogu tymczasowym `_MEIPASS`, nie obok exe."""
    return Path(getattr(sys, "_MEIPASS", app_dir()))


APP = app_dir()
DATA = APP / "data"
MEDIA = DATA / "media"
DB_PATH = DATA / "library.sqlite"
CONFIG_PATH = APP / "config.json"
LOGS = APP / "logs"
DEFAULT_CACHE = APP / "cache"
WEB = bundle_dir() / "web"
ASSETS = bundle_dir() / "assets"

# Rozpakowane gry na czas jednej sesji. %TEMP% + FILE_ATTRIBUTE_TEMPORARY:
# przy dużej ilości wolnego RAM Windows nie zapisuje takich plików na dysk.
RUN_TMP = Path(tempfile.gettempdir()) / "emustart" / "run"


def ensure_dirs() -> None:
    for d in (DATA, MEDIA, LOGS, RUN_TMP):
        d.mkdir(parents=True, exist_ok=True)


IS_WIN = os.name == "nt"
