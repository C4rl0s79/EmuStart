"""EmuStart — punkt startowy.

    python main.py           pełny ekran (wg ustawień)
    python main.py --window  w oknie
    python main.py --debug   w oknie + DevTools
    python main.py --browser serwer UI na http://127.0.0.1:8765/?dev (bez okna)
    python main.py --server  serwer gier dla telefonu (bez okna)
    python main.py --server-panel  okno serwera: stan, klienci, przeładowanie gier, restart
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from logging.handlers import RotatingFileHandler


def _unblock_self() -> int:
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


_UNBLOCKED = _unblock_self()

import webview  # noqa: E402  (po zdjęciu blokady)

from emustart import api as api_mod, config, paths, server


def _logging(name: str = "emustart.log") -> None:
    paths.ensure_dirs()
    h = RotatingFileHandler(paths.LOGS / name, maxBytes=2_000_000,
                            backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])
    import threading
    threading.excepthook = lambda a: logging.getLogger("emustart").error(
        "wyjątek w wątku %s", a.thread.name if a.thread else "?",
        exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


def main() -> None:
    if "--server-panel" in sys.argv:
        _logging("panel.log")             # osobny log — serwer pisze do emustart.log
        _log_unblocked()
        _use_server_layout(migrate=False)
        return _server_panel()
    _logging()
    _log_unblocked()
    _log_versions()
    debug = "--debug" in sys.argv
    windowed = debug or "--window" in sys.argv
    server_args = any(a in sys.argv for a in ("--server", "--server-key", "--install-server"))
    if server_args or paths.server_layout_active():
        _use_server_layout(migrate=server_args)
    cfg = config.load()
    if server_args:
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


def _use_server_layout(migrate: bool) -> None:
    """Katalog serwera (folder server): migracja z 0.x i przełączenie ścieżek; grafiki serwowane
    z nowego miejsca."""
    moved = paths.use_server_layout(migrate=migrate)
    server.ROUTES["/media/"] = paths.MEDIA
    if moved:
        logging.getLogger("emustart").info("katalog serwera %s: przeniesiono %s", paths.SERVER, ", ".join(moved))


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
    remote.init_nas(api._cfg)             # folder profili (zapisy klientów przez serwer)
    from emustart import srvadmin
    srvadmin.ENABLED = True
    # po restarcie z panelu poprzedni proces zwalnia port po chwili — czekamy na niego
    end = time.monotonic() + (30 if "--restarted" in sys.argv else 0)
    while True:
        try:
            base = server.start(port, dev_api=api, bind="0.0.0.0")
            break
        except OSError:
            if time.monotonic() >= end:
                raise
            time.sleep(0.5)
    remote.PORT = port
    _start_tls(api, port, log)
    log.info("serwer %s: API dla Androida na porcie %d; administracja: %s/index.html?dev",
             __import__("emustart").__version__, port, base.replace("0.0.0.0", "127.0.0.1"))
    while True:
        time.sleep(3600)


def _start_tls(api, port: int, log) -> None:
    """Port szyfrowany (domyślnie zwykły + 1) z własnym certyfikatem serwera (emustart/tls.py)."""
    import time
    from emustart import paths, remote, tls
    try:
        got = tls.ensure(paths.SERVER)
        if not got:
            return
        cert, key, fp = got
        tport = int(api._cfg.get("server_tls_port") or port + 1)
        end = time.monotonic() + (30 if "--restarted" in sys.argv else 0)
        while True:
            try:
                tport = server.start_tls(tport, tls.server_context(cert, key), bind="0.0.0.0")
                break
            except OSError:
                if time.monotonic() >= end:
                    raise
                time.sleep(0.5)
        remote.TLS = {"port": tport, "fp": fp}
        remote.ensure_firewall(tport)
        from emustart import public          # dostęp z internetu (UPnP), gdy włączony w panelu
        public.CTX = tls.server_context(cert, key)
        public.start(api._cfg, public.CTX)
        log.info("port szyfrowany %d (certyfikat %s…)", tport, fp[:16])
    except Exception:
        log.exception("port szyfrowany")


def _log_versions() -> None:
    """Wersje bibliotek okna (pywebview, pythonnet) — różnią się między buildami."""
    from importlib import metadata
    vers = []
    for name in ("pywebview", "pythonnet", "clr_loader"):
        try:
            vers.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            pass
    logging.getLogger("emustart").info("Python %s, %s", sys.version.split()[0], ", ".join(vers) or "brak metadanych")


def _log_unblocked() -> None:
    if _UNBLOCKED:
        logging.getLogger("emustart").info("zdjęto znacznik „z internetu” z %d plików programu",
                                           _UNBLOCKED)


def _server_panel() -> None:
    """Małe okno serwera: stan, urządzenia, pobierane gry, przeładowanie listy gier,
    restart. Serwer zwykle działa bez okna (zadanie Harmonogramu, konto SYSTEM) —
    panel łączy się z nim przez 127.0.0.1; gdy nie odpowiada, uruchamia go."""
    from emustart import remote, srvadmin
    cfg = config.load()
    port = int(cfg.get("server_port") or remote.DEFAULT_PORT)
    if not srvadmin.ping(port):
        if not srvadmin.start_server(port):
            return _message(f"Serwer EmuStart nie odpowiada na porcie {port} i nie udało się go "
                            "uruchomić.\n\nSprawdź logs\\emustart.log obok EmuStart.exe.")
    class PanelApi:
        def open_logs(self):
            # tu, nie w serwerze: serwer na koncie SYSTEM otworzyłby Eksplorator niewidocznie
            os.startfile(str(paths.LOGS))      # type: ignore[attr-defined]
            return {"ok": True}

        def close_for_update(self, version: str = ""):
            """Aktualizacja: okno panelu (ten sam exe) zwalnia pliki programu. Panel wraca:
            1) skrypt aktualizacji uruchamia go po starcie serwera jednorazowym zadaniem
               Harmonogramu w sesji tego użytkownika (update\\panel-user.txt);
            2) zapasowo — osobny proces na tym koncie czeka na nową wersję serwera.
            Przebieg obu — logs\\update.log."""
            from emustart import updater
            exe = sys.executable if getattr(sys, "frozen", False) else ""
            log = logging.getLogger("emustart")
            log.info("panel: zamknięcie na czas aktualizacji do %s", version or "?")
            if exe and os.name == "nt":
                user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
                try:
                    updater.update_dir().mkdir(parents=True, exist_ok=True)
                    (updater.update_dir() / "panel-user.txt").write_text(user, encoding="utf-8")
                except OSError as ex:
                    log.warning("panel: %s", ex)
                ulog = str(paths.LOGS / "update.log").replace("'", "''")
                ps = ("function L($m){ Add-Content -LiteralPath '" + ulog + "' -Value ('{0:yyyy-MM-dd HH:mm:ss} panel: {1}' -f (Get-Date),$m) }; "
                      "L 'czekam na serwer w nowej wersji'; "
                      "$end=(Get-Date).AddMinutes(10); $ok=$false; while((Get-Date) -lt $end){ Start-Sleep 3; try{ "
                      f"$r=Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:{port}/admin/status' "
                      "-Headers @{'X-EmuStart'='1'} -Body '{}' -ContentType 'application/json' -TimeoutSec 3; "
                      f"if('{version}' -eq '' -or $r.version -eq '{version}'){{ $ok=$true; break }} }}catch{{}} }}; "
                      "L ('serwer ' + $(if($ok){'gotowy'}else{'nie potwierdził wersji w 10 min'})); "
                      "Start-Sleep 6; "
                      "$p = Get-CimInstance Win32_Process -Filter \"Name='EmuStart.exe'\" | "
                      "Where-Object { $_.CommandLine -like '*--server-panel*' }; "
                      "if($p){ L 'panel już działa (uruchomiony przez skrypt aktualizacji)' } "
                      f"else {{ Start-Process -FilePath '{exe}' -ArgumentList '--server-panel'; L 'panel uruchomiony' }}")
                flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
                cmd = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]
                try:              # poza ewentualnym zadaniem/zadaniem systemu — przeżyje zamknięcie panelu
                    subprocess.Popen(cmd, close_fds=True,
                                     creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
                except OSError:
                    subprocess.Popen(cmd, close_fds=True, creationflags=flags)
            threading.Timer(0.8, lambda: os._exit(0)).start()
            return {"ok": True}

    url = f"http://127.0.0.1:{port}/server.html"
    try:
        webview.create_window("EmuStart Server", url, js_api=PanelApi(),
                              width=760, height=640, min_size=(560, 480), background_color="#0b0d12")
        webview.start(private_mode=False)
    except Exception:
        # okno (pywebview + .NET + WebView2) nie wystartowało — panel w przeglądarce
        logging.getLogger("emustart").exception("okno panelu nie wystartowało")
        import webbrowser
        webbrowser.open(url)


if __name__ == "__main__":
    main()
