"""EmuStart — punkt startowy.

    python main.py           pełny ekran (wg ustawień)
    python main.py --window  w oknie
    python main.py --debug   w oknie + DevTools
    python main.py --browser serwer UI na http://127.0.0.1:8765/?dev (bez okna)

Serwer gier to osobny program: server_main.py (EmuStartServer.exe).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from emustart import boot  # noqa: E402

_PROBE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--sdl-probe=")), "")
if _PROBE:
    # numery padów SDL emulatora (emustart/sdlpads.py) — krótki proces pomocniczy, bez okna
    from emustart import sdlpads
    sys.exit(sdlpads.probe_main(_PROBE, next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--sdl-out=")), "")))

_UNBLOCKED = boot.unblock_self()

import webview  # noqa: E402  (po zdjęciu blokady)

from emustart import api as api_mod, config, paths, server


def main() -> None:
    boot.setup_logging("emustart.log", _UNBLOCKED)
    boot.log_versions()
    debug = "--debug" in sys.argv
    windowed = debug or "--window" in sys.argv
    if paths.server_layout_active():
        # folder serwera (EmuStart Server): grę uruchamia się z EmuStart zalogowanego do serwera
        paths.use_server_layout(migrate=False)
        server.ROUTES["/media/"] = paths.MEDIA
    cfg = config.load()
    api = api_mod.Api()
    server.MEDIA_CFG = lambda: api._cfg
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
    logging.getLogger("emustart").info("start %s, obsługa padów w UI: %s",
                                       __import__("emustart").__version__, api.pad_backend())
    try:
        webview.start(debug=debug, private_mode=False)
    except Exception as ex:
        # okno (pywebview + .NET + WebView2) nie wystartowało — interfejs w przeglądarce
        logging.getLogger("emustart").exception("okno EmuStart nie wystartowało")
        return _browser_fallback(api, ex)
    # okno zamknięte: przerywamy zadania w tle i kończymy proces od razu — wątki
    # robocze (np. pobieranie grafik) nie mogą trzymać programu przy życiu
    logging.getLogger("emustart").info("zamknięcie okna — koniec programu")
    try:
        api._shutdown()
    finally:
        logging.shutdown()
        os._exit(0)


def _browser_fallback(api, ex) -> None:
    """EmuStart bez okna: ten sam interfejs w domyślnej przeglądarce (lokalnie)."""
    import time
    import webbrowser
    api.browser_mode = True
    base = server.start(0, dev_api=api)
    url = f"{base}/index.html?dev"
    webbrowser.open(url)
    blocked = "Python.Runtime" in str(ex) or "pythonnet" in str(ex).lower()
    fix = (f"Najczęstsza przyczyna: Windows zablokował pliki rozpakowane z pobranego zipa.\n"
           f"Naprawa (PowerShell):\n"
           f"Get-ChildItem -Recurse '{Path(sys.executable).parent}' | Unblock-File\n\n") if blocked else ""
    boot.message(f"Okno EmuStart nie mogło się uruchomić, więc interfejs otworzył się w przeglądarce:\n{url}\n\n"
             + fix + "Może też brakować .NET Framework 4.8 albo Microsoft Edge WebView2 Runtime.\n\n"
                 + f"Szczegóły: {str(ex)[:300]}", "EmuStart")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
