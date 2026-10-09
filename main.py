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
from pathlib import Path
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
    if any(a in sys.argv for a in ("--server", "--server-key", "--install-server")):
        return _server_mode()
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
    _message(f"Okno EmuStart nie mogło się uruchomić, więc interfejs otworzył się w przeglądarce:\n{url}\n\n"
             + fix + "Może też brakować .NET Framework 4.8 albo Microsoft Edge WebView2 Runtime.\n\n"
             + f"Szczegóły: {str(ex)[:300]}", "EmuStart")
    while True:
        time.sleep(3600)


def _message(text: str, title: str = "EmuStart Server") -> None:
    """Okienko z informacją (EmuStart.exe nie ma konsoli)."""
    print(text)
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        pass


def _server_mode() -> None:
    """EmuStart jako serwer gier dla aplikacji na Androidzie (na komputerze z grami)."""
    import socket
    import time
    from emustart import remote
    log = logging.getLogger("emustart")
    api = api_mod.Api()
    remote.API = api
    token = remote.ensure_token(api._cfg)
    port = int(api._cfg.get("server_port") or remote.DEFAULT_PORT)
    if "--server-key" in sys.argv:
        copied = False
        try:                              # klucz od razu w schowku — do wklejenia na telefonie
            import subprocess
            subprocess.run(["clip"], input=token.encode("utf-16-le"), check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            copied = True
        except Exception:
            pass
        return _message(f"Adres: http://{socket.gethostname()}:{port}\n"
                        "(w aplikacji na telefonie wpisz adres Tailscale tego komputera)\n\n"
                        f"Klucz serwera:\n\n{token}"
                        + ("\n\nKlucz jest w schowku (Ctrl+V)." if copied else ""))
    if "--install-server" in sys.argv:
        if not getattr(sys, "frozen", False):
            return _message("Autostart serwera instaluje się z EmuStart.exe (wersja zbudowana).")
        res = remote.install_autostart(sys.executable, port)
        ok = all(code == 0 for _c, code, _o in res)
        lines = [f"{c}: {'OK' if code == 0 else 'błąd'} {o[:120]}" for c, code, o in res]
        return _message(("Serwer będzie startował razem z Windows." if ok else
                         "Nie wszystko się udało — uruchom jako administrator.") + "\n\n" + "\n".join(lines))
    base = server.start(port, dev_api=api, bind="0.0.0.0")
    log.info("serwer %s: API dla Androida na porcie %d; administracja: %s/index.html?dev",
             __import__("emustart").__version__, port, base.replace("0.0.0.0", "127.0.0.1"))
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
