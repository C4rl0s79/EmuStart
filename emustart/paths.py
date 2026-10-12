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


# ── katalog serwera (EmuStart 2.0, etap 1) ──
# Serwer trzyma wszystko poza grami w jednym podkatalogu programu — skopiowanie katalogu
# programu przenosi serwer na inny komputer. config.json i folder data z wersji 0.x trafiają tam
# przy pierwszym starcie serwera.
SERVER = APP / "server"


def server_layout_active() -> bool:
    """Ten katalog programu jest serwerem (po migracji): także zwykły EmuStart uruchomiony
    na komputerze z serwerem pracuje na danych serwera, nie na pustym folderze data."""
    return (SERVER / "config.json").is_file() and not (APP / "config.json").is_file()


def use_server_layout(migrate: bool = False) -> list:
    """Przełącza ścieżki na katalog serwera; migrate — przenosi config.json i folder data z 0.x.
    Zwraca opis przeniesionych elementów (do logu)."""
    global DATA, MEDIA, DB_PATH, CONFIG_PATH
    moved = []
    if migrate:
        SERVER.mkdir(parents=True, exist_ok=True)
        old_cfg, old_data = APP / "config.json", APP / "data"
        if old_cfg.is_file() and not (SERVER / "config.json").exists():
            os.replace(old_cfg, SERVER / "config.json")
            moved.append("config.json")
        if old_data.is_dir():
            for item in list(old_data.iterdir()):
                dst = SERVER / item.name
                if not dst.exists():
                    os.replace(item, dst)
                    moved.append("data/" + item.name)
            try:
                old_data.rmdir()
            except OSError:
                pass
    DATA = SERVER
    MEDIA = SERVER / "media"
    DB_PATH = SERVER / "library.sqlite"
    CONFIG_PATH = SERVER / "config.json"
    return moved


def ensure_dirs() -> None:
    for d in (DATA, MEDIA, LOGS, RUN_TMP):
        d.mkdir(parents=True, exist_ok=True)


IS_WIN = os.name == "nt"
