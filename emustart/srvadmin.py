"""srvadmin — panel serwera EmuStart (`EmuStart.exe --server-panel`).

Serwer (`--server`) zwykle działa jako zadanie Harmonogramu na koncie SYSTEM,
bez okna. Panel to osobny proces z małym oknem (pywebview), które otwiera
stronę `server.html` z działającego serwera i woła jego API administracyjne:

    POST /admin/status    wersja, czas pracy, klienci, pobierane gry, skan
    POST /admin/rescan    przeładowanie listy gier (skan folderów)
    POST /admin/restart   ponowne uruchomienie procesu serwera
    POST /admin/key       klucz serwera (do wklejenia na telefonie)
    POST /admin/logs      folder logów w Eksploratorze
    POST /admin/update_check   najnowsze wydanie na GitHubie (emustart/updater.py)
    POST /admin/update_start   pobranie i rozpakowanie aktualizacji w tle
    POST /admin/update_status  postęp pobierania
    POST /admin/update_apply   podmiana plików po zamknięciu serwera i jego ponowny start

Tylko z tego komputera: nagłówek Host 127.0.0.1/localhost i nagłówek
X-EmuStart (jak API interfejsu) — z sieci (Tailscale) panel jest niedostępny.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request

from emustart import __version__, paths

log = logging.getLogger("emustart.srvadmin")

ENABLED = False               # True tylko w trybie serwera
STARTED = time.time()
CLIENT_WINDOW = 15 * 60       # klient „aktywny”, gdy odezwał się w tylu ostatnich s

_lock = threading.Lock()
_clients: dict = {}           # ip → {"last", "requests", "sent"}
_transfers: dict = {}         # (ip, gid) → {"title", "sent", "last"}
_sent_total = 0


# ── statystyki (wołane przez remote.py) ──
def seen(ip: str) -> None:
    with _lock:
        c = _clients.setdefault(ip, {"last": 0.0, "requests": 0, "sent": 0, "first": time.time()})
        c["last"] = time.time()
        c["requests"] += 1


def sent(ip: str, gid: int, title: str, n: int) -> None:
    global _sent_total
    now = time.time()
    with _lock:
        _sent_total += n
        c = _clients.setdefault(ip, {"last": now, "requests": 0, "sent": 0, "first": now})
        c["sent"] += n
        c["last"] = now
        t = _transfers.setdefault((ip, gid), {"title": title, "sent": 0, "first": now, "last": now})
        t["sent"] += n
        t["last"] = now


def _host_name(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0].split(".")[0]
    except OSError:
        return ""


_names: dict = {}


def _name(ip: str) -> str:
    if ip not in _names:
        _names[ip] = ""
        # odwrotny DNS (MagicDNS Tailscale zna nazwy urządzeń) bez blokowania panelu
        threading.Thread(target=lambda: _names.__setitem__(ip, _host_name(ip)),
                         daemon=True, name="dns").start()
    return _names[ip]


# ── API panelu ──
def status(api) -> dict:
    from emustart import config, library, remote
    now = time.time()
    with _lock:
        clients = [{"ip": ip, "name": _name(ip), "last": c["last"], "requests": c["requests"],
                    "sent": c["sent"], "active": now - c["last"] < CLIENT_WINDOW}
                   for ip, c in _clients.items()]
        transfers = [{"ip": ip, "name": _name(ip), "title": t["title"], "sent": t["sent"],
                      "first": t["first"], "last": t["last"], "active": now - t["last"] < 10}
                     for (ip, _gid), t in _transfers.items() if now - t["last"] < 3600]
        total = _sent_total
    clients.sort(key=lambda c: -c["last"])
    transfers.sort(key=lambda t: -t["last"])
    try:
        games = library.db().execute("SELECT COUNT(*) n FROM games").fetchone()["n"]
        systems = library.db().execute("SELECT COUNT(DISTINCT es) n FROM games").fetchone()["n"]
    except Exception:
        games = systems = 0
    scan = dict(api._scan) if api else {}
    return {
        "version": __version__, "host": socket.gethostname(), "pid": os.getpid(),
        "started": STARTED, "uptime": now - STARTED,
        "port": int((api._cfg if api else {}).get("server_port") or remote.DEFAULT_PORT),
        "addresses": _addresses(),
        "games": games, "systems": systems,
        "roots": [str(r) for r in config.rom_roots(api._cfg)] if api else [],
        "nas_root": str(remote.NAS_ROOT) if remote.NAS_ROOT else "",
        "clients": clients[:20], "transfers": transfers[:20], "sent_total": total,
        "scan": scan, "last_scan": _last_scan,
        "frozen": bool(getattr(sys, "frozen", False)),
    }


def _addresses() -> list:
    """Adresy IPv4 komputera — Tailscale (100.x) na początku: ten wpisuje się w telefonie."""
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except OSError:
        pass
    out.discard("127.0.0.1")
    return sorted(out, key=lambda a: (not a.startswith("100."), a))


_last_scan = 0.0


def rescan(api) -> dict:
    global _last_scan
    r = api.rescan()
    if not r.get("ok"):
        return {"ok": False, "reason": "Skanowanie już trwa."}
    _last_scan = time.time()
    return {"ok": True}


def key(api) -> dict:
    from emustart import remote
    return {"key": remote.ensure_token(api._cfg)}


def open_logs(_api) -> dict:
    try:
        os.startfile(str(paths.LOGS))      # type: ignore[attr-defined]
        return {"ok": True}
    except Exception as ex:
        return {"ok": False, "reason": str(ex)}


def restart(_api) -> dict:
    """Nowy proces serwera z tymi samymi parametrami, ten kończy się po chwili
    (odpowiedź musi jeszcze dojść do panelu). Nowy czeka, aż port się zwolni."""
    cmd = _self_cmd() + [a for a in sys.argv[1:] if a != "--restarted"] + ["--restarted"]
    flags = 0
    if os.name == "nt":
        flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                 | subprocess.CREATE_NO_WINDOW)
    try:
        try:      # poza zadaniem Harmonogramu (job object) — inaczej zginąłby razem z nami
            subprocess.Popen(cmd, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0),
                             close_fds=True, cwd=str(paths.APP))
        except OSError:
            subprocess.Popen(cmd, creationflags=flags, close_fds=True, cwd=str(paths.APP))
    except Exception as ex:
        log.exception("restart serwera")
        return {"ok": False, "reason": f"Nie udało się uruchomić nowego procesu: {ex}"}
    log.info("restart serwera na żądanie panelu")

    def bye():
        time.sleep(0.7)
        logging.shutdown()
        os._exit(0)
    threading.Thread(target=bye, daemon=True, name="restart").start()
    return {"ok": True}


def _self_cmd() -> list:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, os.path.abspath(sys.argv[0])]


# ── aktualizacja (emustart/updater.py) ──
_update: dict = {"info": None, "job": None}


def update_check(_api) -> dict:
    from emustart import updater
    try:
        info = updater.latest()
    except Exception as ex:
        log.warning("sprawdzenie aktualizacji: %s", ex)
        return {"ok": False, "reason": f"Nie udało się sprawdzić wydań na GitHubie: {ex}"}
    _update["info"] = info
    return {"ok": True, **{k: info[k] for k in ("version", "current", "newer", "size", "notes",
                                                "published", "page")},
            "available": bool(info["url"]), "frozen": bool(getattr(sys, "frozen", False))}


def update_start(_api) -> dict:
    from emustart import updater
    job = _update["job"]
    if job and job.phase in ("downloading", "extracting", "applying"):
        return {"ok": True, **job.status()}
    info = _update["info"]
    if not info or not info.get("url"):
        return {"ok": False, "reason": "Najpierw sprawdź aktualizacje."}
    if not info["newer"]:
        return {"ok": False, "reason": f"Serwer ma już najnowszą wersję ({info['current']})."}
    job = updater.Job(info)
    _update["job"] = job
    job.start()
    return {"ok": True, **job.status()}


def update_status(_api) -> dict:
    job = _update["job"]
    return {"ok": True, **job.status()} if job else {"ok": True, "phase": "idle"}


def update_apply(_api) -> dict:
    """Skrypt podmiany startuje, ten proces kończy się po chwili (odpowiedź musi dojść)."""
    job = _update["job"]
    if not job:
        return {"ok": False, "reason": "Brak pobranej aktualizacji."}
    r = job.apply(sys.argv[1:])
    if not r.get("ok"):
        return r
    log.info("koniec serwera — aktualizacja do %s", job.info.get("version"))

    def bye():
        time.sleep(0.7)
        logging.shutdown()
        os._exit(0)
    threading.Thread(target=bye, daemon=True, name="update-exit").start()
    return {"ok": True, "version": job.info.get("version")}


ACTIONS = {"status": status, "rescan": rescan, "restart": restart, "key": key, "logs": open_logs,
           "update_check": update_check, "update_start": update_start,
           "update_status": update_status, "update_apply": update_apply}


def handle(handler, api) -> None:
    """POST /admin/<akcja> (server.py sprawdził już Host i X-EmuStart)."""
    name = handler.path.split("?")[0].removeprefix("/admin/")
    fn = ACTIONS.get(name) if ENABLED else None
    n = int(handler.headers.get("Content-Length") or 0)
    if n:
        handler.rfile.read(min(n, 1 << 16))
    if not fn:
        return handler.send_error(404)
    try:
        data = fn(api)
    except Exception as ex:
        log.exception("panel %s", name)
        data = {"ok": False, "reason": str(ex)}
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


# ── po stronie panelu (osobny proces) ──
def ping(port: int, timeout: float = 1.5) -> dict | None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/admin/status", data=b"",
                                 headers={"X-EmuStart": "1"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def start_server(port: int) -> bool:
    """Serwer nie odpowiada: najpierw zadanie Harmonogramu (autostart, konto SYSTEM),
    a gdy go nie ma — proces serwera na koncie użytkownika."""
    if os.name == "nt":
        r = subprocess.run(["schtasks", "/Run", "/TN", "EmuStart Server"], capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0 and _wait(port, 15):
            return True
        subprocess.Popen(_self_cmd() + ["--server"], close_fds=True, cwd=str(paths.APP),
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen(_self_cmd() + ["--server"], close_fds=True, cwd=str(paths.APP),
                         start_new_session=True)
    return _wait(port, 30)


def _wait(port: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ping(port, 1.0):
            return True
        time.sleep(0.5)
    return False
