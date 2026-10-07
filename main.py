"""EmuStart — punkt startowy.

    python main.py           pełny ekran (wg ustawień)
    python main.py --window  w oknie
    python main.py --debug   w oknie + DevTools
    python main.py --browser serwer UI na http://127.0.0.1:8765/?dev (bez okna)
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import webview

from emustart import api as api_mod, config, paths, server


def _logging() -> None:
    paths.ensure_dirs()
    h = RotatingFileHandler(paths.LOGS / "emustart.log", maxBytes=2_000_000,
                            backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])
    import threading
    threading.excepthook = lambda a: logging.getLogger("emustart").error(
        "wyjątek w wątku %s", a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main() -> None:
    _logging()
    debug = "--debug" in sys.argv
    windowed = debug or "--window" in sys.argv
    cfg = config.load()
    api = api_mod.Api()
    if "--browser" in sys.argv:
        print(server.start(8765, dev_api=api) + "/index.html?dev")
        import time
        while True:
            time.sleep(3600)
    base = server.start()
    window = webview.create_window(
        "EmuStart", f"{base}/index.html", js_api=api,
        fullscreen=bool(cfg.get("fullscreen", True)) and not windowed,
        width=1600, height=900, min_size=(960, 540), background_color="#0b0d12")
    api.attach(window)
    webview.start(debug=debug, private_mode=False)
    # okno zamknięte: przerywamy zadania w tle i kończymy proces od razu — wątki
    # robocze (np. pobieranie grafik) nie mogą trzymać programu przy życiu
    logging.getLogger("emustart").info("zamknięcie okna — koniec programu")
    try:
        api._shutdown()
    finally:
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    main()
