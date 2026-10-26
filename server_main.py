"""EmuStart Server — serwer gier i jego panel (osobny program od EmuStart na PC).

    EmuStartServer.exe              panel serwera (okno): stan, konfiguracja, urządzenia,
                                    konta, aktualizacja; uruchamia serwer, gdy nie działa
    EmuStartServer.exe --panel      to samo (skrypty aktualizacji, skrót w menu Start)
    EmuStartServer.exe --service    serwer bez okna (zadanie Harmonogramu „EmuStart Server”)
    EmuStartServer.exe --install    autostart z Windows, zapora, skrót (jako administrator)
    EmuStartServer.exe --login-link link logowania urządzenia do schowka (bez aparatu)

Dane serwera: folder `server` obok exe (config.json, biblioteka, grafiki, konta).
"""

from __future__ import annotations

import logging
import os
import sys
import threading

from emustart import boot

_UNBLOCKED = boot.unblock_self()

import webview  # noqa: E402  (po zdjęciu blokady)

from emustart import config, paths, server, srvapi  # noqa: E402

_message = lambda text, title="EmuStart Server": boot.message(text, title)  # noqa: E731


def main() -> None:
    service = any(a in sys.argv for a in ("--service", "--install"))
    if not service:
        # panel i link logowania — na koncie użytkownika: log w jego folderze, nic w folderze programu
        boot.setup_logging("panel.log", _UNBLOCKED, boot.user_logs())
        _use_server_layout(migrate=False)
        if "--login-link" in sys.argv:
            return _login_link()
        return _server_panel()
    boot.setup_logging("emustart.log", _UNBLOCKED)
    boot.log_versions()
    _use_server_layout(migrate=True)
    config.load()
    return _server_mode()


def _login_link_from_server(port: int) -> str:
    """Link logowania z działającego serwera (kody żyją w jego pamięci) — przez panel na tym
    komputerze. To samo co kod QR z panelu (Dodaj urządzenie)."""
    import json
    import urllib.request
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/admin/login_code", data=b"{}", method="POST",
                                     headers={"X-EmuStart": "1"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read()).get("uri", "")
    except Exception:
        return ""


def _login_link() -> None:
    """Logowanie urządzenia bez aparatu (komputer): link z kodem jednorazowym wklejony w
    „Zaloguj” — to samo co kod QR z panelu (Dodaj urządzenie), bez czekania na zatwierdzenie."""
    from emustart import remote
    port = int(config.load().get("server_port") or remote.DEFAULT_PORT)
    link = _login_link_from_server(port)
    if link:
        try:
            import subprocess
            subprocess.run(["clip"], input=link.encode("utf-16-le"), check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:
            pass
    return _message(("Link logowania (jednorazowy, ważny 10 minut) jest w schowku —\n"
                     "wklej go w EmuStart: Ustawienia → Zaloguj.\n\n" + link) if link else
                    "Serwer nie działa — uruchom go i spróbuj ponownie (albo panel serwera → Dodaj urządzenie).")


def _use_server_layout(migrate: bool) -> None:
    """Katalog serwera (folder server): migracja z 0.x i przełączenie ścieżek; grafiki serwowane
    z nowego miejsca."""
    moved = paths.use_server_layout(migrate=migrate)
    server.ROUTES["/media/"] = paths.MEDIA
    if moved:
        logging.getLogger("emustart").info("katalog serwera %s: przeniesiono %s", paths.SERVER, ", ".join(moved))


def _server_mode() -> None:
    """Serwer gier (bez okna): biblioteka, grafiki, konta i urządzenia dla EmuStart na PC i Androidzie."""
    import time
    from emustart import remote
    log = logging.getLogger("emustart")
    api = srvapi.ServerApi()
    remote.API = api
    remote.ensure_token(api._cfg)           # wspólny klucz serwera (tylko ten komputer)
    port = int(api._cfg.get("server_port") or remote.DEFAULT_PORT)
    if "--install" in sys.argv:
        if not getattr(sys, "frozen", False):
            return _message("Autostart serwera instaluje się z EmuStartServer.exe (wersja zbudowana).")
        res = remote.install_autostart(sys.executable, port)
        ok = all(code == 0 for _c, code, _o in res)
        lines = [f"{c}: {'OK' if code == 0 else 'błąd'} {o[:120]}" for c, code, o in res]
        return _message(("Serwer będzie startował razem z Windows." if ok else
                         "Nie wszystko się udało — uruchom jako administrator.") + "\n\n" + "\n".join(lines))
    remote.init_nas(api._cfg)             # folder profili (zapisy klientów przez serwer)
    from emustart import acctbackup
    acctbackup.start_auto(lambda: api._cfg)   # kopie kont raz dziennie
    from emustart import emustore
    emustore.scheduler(lambda: api._cfg)      # nowe wersje emulatorów raz dziennie
    from emustart import clientdist
    clientdist.scheduler()                    # programy do pobrania ze strony serwera
    from emustart import srvadmin
    srvadmin.ENABLED = True
    srvadmin.import_keys_file(api)          # server\art_keys.json — klucze źródeł grafik (plik usuwany)
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
        if api._cfg.get("server_tunnel_url"):
            public.set_tunnel(api._cfg["server_tunnel_url"])
        log.info("port szyfrowany %d (certyfikat %s…)", tport, fp[:16])
    except Exception:
        log.exception("port szyfrowany")


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
        def pick(self, kind: str = "dir", start: str = ""):
            """Wybór folderu (albo pliku) w oknie panelu; dysk sieciowy → ścieżka UNC (serwer
            na koncie systemowym nie zna liter dysków sieciowych użytkownika)."""
            from emustart import srvconfig
            win = webview.windows[0] if webview.windows else None
            if not win:
                return {"ok": False}
            mode = webview.OPEN_DIALOG if kind == "file" else webview.FOLDER_DIALOG
            got = win.create_file_dialog(mode, directory=start if start and os.path.isdir(start) else "",
                                         file_types=("Program (*.exe)",) if kind == "file" else ())
            if not got:
                return {"ok": False}
            path = got[0] if isinstance(got, (list, tuple)) else got
            return {"ok": True, "path": srvconfig.unc(str(path))}

        def open_logs(self):
            # tu, nie w serwerze: serwer na koncie SYSTEM otworzyłby Eksplorator niewidocznie
            os.startfile(str(paths.LOGS))      # type: ignore[attr-defined]
            return {"ok": True}

        def whoami(self):
            """Konto Windows tego panelu — serwer (aktualizacja) otworzy panel ponownie w tej sesji."""
            return f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")

        def close_for_update(self, version: str = ""):
            """Aktualizacja: okno panelu (ten sam exe) zwalnia pliki programu. Panel wraca:
            1) skrypt aktualizacji uruchamia go po starcie serwera jednorazowym zadaniem
               Harmonogramu w sesji tego użytkownika (konto podane serwerowi w update_apply);
            2) zapasowo — osobny proces na tym koncie czeka na nową wersję serwera
               (log w folderze użytkownika — folder programu jest tylko do odczytu)."""
            from emustart import updater
            exe = sys.executable if getattr(sys, "frozen", False) else ""
            log = logging.getLogger("emustart")
            log.info("panel: zamknięcie na czas aktualizacji do %s", version or "?")
            if exe and os.name == "nt":
                ulog = str(boot.user_logs() / "update.log").replace("'", "''")
                ps = ("function L($m){ Add-Content -LiteralPath '" + ulog + "' -Value ('{0:yyyy-MM-dd HH:mm:ss} panel: {1}' -f (Get-Date),$m) }; "
                      "L 'czekam na serwer w nowej wersji'; "
                      "$end=(Get-Date).AddMinutes(10); $ok=$false; while((Get-Date) -lt $end){ Start-Sleep 3; try{ "
                      f"$r=Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:{port}/admin/status' "
                      "-Headers @{'X-EmuStart'='1'} -Body '{}' -ContentType 'application/json' -TimeoutSec 3; "
                      f"if('{version}' -eq '' -or $r.version -eq '{version}'){{ $ok=$true; break }} }}catch{{}} }}; "
                      "L ('serwer ' + $(if($ok){'gotowy'}else{'nie potwierdził wersji w 10 min'})); "
                      "Start-Sleep 6; "
                      "$p = Get-CimInstance Win32_Process -Filter \"Name='EmuStartServer.exe'\" | "
                      "Where-Object { $_.CommandLine -like '*--panel*' }; "
                      "if($p){ L 'panel już działa (uruchomiony przez skrypt aktualizacji)' } "
                      f"else {{ Start-Process -FilePath '{exe}' -ArgumentList '--panel'; L 'panel uruchomiony' }}")
                # przeżyje zamknięcie panelu; bez DETACHED_PROCESS (PowerShell bez konsoli nic nie robi)
                updater.spawn_powershell(["-NoProfile", "-WindowStyle", "Hidden", "-Command", ps])
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
