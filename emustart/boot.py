"""boot — wspólny start obu programów: EmuStart (klient, main.py) i EmuStart Server
(server_main.py). Zdjęcie blokady „z internetu”, log, okienko z komunikatem."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from emustart import paths


def unblock_self() -> int:
    """Zdejmuje znacznik „pobrane z internetu” (strumień Zone.Identifier) z plików
    programu. Windows dodaje go plikom wypakowanym z pobranego ZIP-a, a .NET nie
    wczytuje wtedy Python.Runtime.dll (okno: „Failed to resolve
    Python.Runtime.Loader.Initialize”). Musi się stać przed `import webview`."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return 0
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    probe = base / "pythonnet" / "runtime" / "Python.Runtime.dll"
    if not os.path.exists(str(probe) + ":Zone.Identifier"):
        return 0                      # nic nie jest zablokowane (szybka ścieżka)
    n = 0
    for root in {base, Path(sys.executable).parent}:
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    os.remove(os.path.join(dirpath, name) + ":Zone.Identifier")
                    n += 1
                except OSError:
                    pass
    return n


def user_logs() -> Path:
    """Logi procesów na koncie użytkownika (panel serwera, link logowania): folder programu
    serwera jest dla nich tylko do odczytu (--install blokuje zapis zwykłym użytkownikom)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "EmuStart Server" / "logs"


def setup_logging(name: str = "emustart.log", unblocked: int = 0, logs_dir: Path | None = None) -> None:
    if logs_dir is None:
        paths.ensure_dirs()
        logs_dir = paths.LOGS
    else:
        logs_dir.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(logs_dir / name, maxBytes=2_000_000,
                            backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])
    import threading
    threading.excepthook = lambda a: logging.getLogger("emustart").error(
        "wyjątek w wątku %s", a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    if unblocked:
        logging.getLogger("emustart").info("zdjęto znacznik „z internetu” z %d plików programu", unblocked)


def log_versions() -> None:
    """Wersje bibliotek okna (pywebview, pythonnet) — różnią się między buildami."""
    from importlib import metadata
    vers = []
    for name in ("pywebview", "pythonnet", "clr_loader"):
        try:
            vers.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            pass
    logging.getLogger("emustart").info("Python %s, %s", sys.version.split()[0], ", ".join(vers) or "brak metadanych")


def message(text: str, title: str = "EmuStart") -> None:
    """Okienko z informacją (programy nie mają konsoli)."""
    print(text)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        pass
