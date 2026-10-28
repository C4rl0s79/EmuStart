"""srvadmin — panel serwera EmuStart (`EmuStartServer.exe`, osobny proces z oknem).

Serwer (`EmuStartServer.exe --service`) zwykle działa jako zadanie Harmonogramu na koncie SYSTEM,
bez okna. Panel to osobny proces z małym oknem (pywebview), które otwiera
stronę `server.html` z działającego serwera i woła jego API administracyjne:

    POST /admin/status    wersja, czas pracy, klienci, pobierane gry, skan
    POST /admin/rescan    przeładowanie listy gier (skan folderów)
    POST /admin/restart   ponowne uruchomienie procesu serwera
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
import re
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
        "tls": _tls_state(),
    }


def _tls_state() -> dict:
    from emustart import tls
    return {**tls.STATE, "move_ready": (paths.SERVER / tls.MOVE).is_file()}


# ── przeniesienie serwera na inny komputer (klucz certyfikatu, klucze źródeł grafik) ──
def move_prepare(api, args: dict) -> dict:
    """Plik przeniesienia zaszyfrowany hasłem przeniesienia (folder serwera → nowy komputer)."""
    from emustart import accounts, art_sources, tls
    pw = str(args.get("password") or "")
    why = accounts.policy(pw)
    if why:
        return {"ok": False, "reason": why}
    keys = {k: v for k, v in art_sources.keys(api._cfg).items() if v}
    try:
        out = tls.export_move(paths.SERVER, pw, {"art_keys": keys})
    except (OSError, ValueError) as ex:
        return {"ok": False, "reason": str(ex)}
    log.info("panel: przygotowano przeniesienie serwera (%s)", out.name)
    return {"ok": True, "file": str(out)}


def move_import(api, args: dict) -> dict:
    """Nowy komputer: hasło przeniesienia → klucz certyfikatu i klucze źródeł grafik zaszyfrowane
    tutaj; serwer uruchamia się ponownie (port szyfrowany z tym samym certyfikatem)."""
    from emustart import art_sources, config, secure, tls
    try:
        extra = tls.import_move(paths.SERVER, str(args.get("password") or ""))
    except ValueError as ex:
        return {"ok": False, "reason": str(ex)}
    keys = extra.get("art_keys") or {}
    if keys:
        cur = dict(api._cfg.get("art_keys") or {})
        for name in art_sources.KEY_NAMES:
            if keys.get(name):
                cur[name] = secure.protect(keys[name])
        api._cfg["art_keys"] = cur
        config.save(api._cfg)
    r = restart(api)
    return {"ok": True, "restart": bool(r.get("ok")), "art_keys": len(keys)}


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


def update_apply(_api, args: dict | None = None) -> dict:
    """Skrypt podmiany startuje, ten proces kończy się po chwili (odpowiedź musi dojść).
    `panel_user` — konto Windows okna panelu: skrypt otworzy panel ponownie w jego sesji
    (zapisuje serwer — folder programu jest dla panelu tylko do odczytu)."""
    from emustart import updater
    job = _update["job"]
    if not job:
        return {"ok": False, "reason": "Brak pobranej aktualizacji."}
    who = str((args or {}).get("panel_user") or "")
    if re.fullmatch(r"[\w .$-]{1,64}(\\[\w .$-]{1,64})?", who):
        try:
            updater.update_dir().mkdir(parents=True, exist_ok=True)
            (updater.update_dir() / "panel-user.txt").write_text(who, encoding="utf-8")
        except OSError as ex:
            log.warning("panel: %s", ex)
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


ACTIONS = {"status": status, "rescan": rescan, "restart": restart, "logs": open_logs,
           "update_check": update_check, "update_start": update_start,
           "update_status": update_status, "update_apply": update_apply}


def handle(handler, api) -> None:
    """POST /admin/<akcja> (server.py sprawdził już Host i X-EmuStart)."""
    name = handler.path.split("?")[0].removeprefix("/admin/")
    fn = (ACTIONS.get(name) or ACTIONS_ARGS.get(name)) if ENABLED else None
    n = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(min(n, 1 << 16)) if n else b""
    if not fn:
        return handler.send_error(404)
    try:
        if name in ACTIONS_ARGS:
            try:
                args = json.loads(raw or b"{}")
            except ValueError:
                args = {}
            data = fn(api, args if isinstance(args, dict) else {})
        else:
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


# ── urządzenia i konta (logowanie kontem — emustart/enroll.py) ──
def devices_list(_api) -> dict:
    from emustart import devices
    from emustart import enroll
    return {"ok": True, "devices": devices.listing(), "enroll": enroll.pending(""), "now": time.time()}


def enroll_decide(_api, args: dict) -> dict:
    """Panel: prośba nowej instalacji z internetu — zatwierdź (z grami / bez) albo odrzuć."""
    from emustart import enroll, remote
    return enroll.decide(str(args.get("id", "")), bool(args.get("allow")), bool(args.get("games")), "",
                         remote.create_account)


def accounts_list(_api) -> dict:
    """Konta (profile) i czy mają hasło (emustart/accounts.py) — skrótów panel nie dostaje."""
    from emustart import accounts, devices, remote
    names = {n.lower(): n for n in remote.profile_names()}
    for low in accounts.names_with_password():
        names.setdefault(low, (accounts.record(low) or {}).get("name", low))
    out = []
    for low, name in sorted(names.items()):
        rec = accounts.record(name) or {}
        out.append({"name": name, "password": bool(rec.get("hash")), "set": rec.get("set", 0),
                    "admin": bool(rec.get("admin")), "devices": len(devices.of_account(name)),
                    "google": rec.get("google_email", "")})
    return {"ok": True, "accounts": out, "rules": accounts.RULES}


def account_google_unlink(_api, args: dict) -> dict:
    """Panel: konto przestaje logować się na stronie serwera tym kontem Google."""
    from emustart import accounts
    return {"ok": accounts.unlink_google(str(args.get("name", "")), "panel serwera")}


# ── strona serwera: zaproszenia, logowanie Google, programy (emustart/webportal.py) ──
def invites_list(_api) -> dict:
    from emustart import invites, webportal
    return {"ok": True, "invites": invites.listing(), "google": webportal.google_status()}


def invite_create(_api, args: dict) -> dict:
    """Zaproszenie adresu Google do konta → link do strony serwera (administrator wysyła go sam)."""
    from emustart import invites, webportal
    base = webportal.base_url()
    if not base:
        return {"ok": False, "reason": "Najpierw ustaw adres tunelu (Konfiguracja → Dostęp z internetu)."}
    r = invites.create(str(args.get("email", "")), str(args.get("account", "")))
    if not r.get("ok"):
        return r
    return {"ok": True, "invite": r["invite"], "link": f"{base}/?zaproszenie={r['token']}"}


def invite_delete(_api, args: dict) -> dict:
    from emustart import invites
    return {"ok": invites.delete(str(args.get("id", "")))}


def google_set(_api, args: dict) -> dict:
    from emustart import webportal
    return webportal.set_google(str(args.get("client_id", "")), str(args.get("client_secret", "")))


def clients_status(_api) -> dict:
    from emustart import clientdist
    return clientdist.status()


def clients_refresh(_api) -> dict:
    from emustart import clientdist
    return clientdist.start_refresh()


def account_password(_api, args: dict) -> dict:
    """Panel (tylko ten komputer): nowe hasło konta — np. użytkownik je zapomniał."""
    from emustart import accounts, remote
    name = remote._find_account(str(args.get("name", "")))
    if not name:
        return {"ok": False, "reason": "Nie ma takiego konta."}
    why = accounts.set_password(name, str(args.get("password", "")), by="panel serwera")
    return {"ok": not why, "reason": why, "name": name}


def account_admin(_api, args: dict) -> dict:
    """Panel: konto administratora (wszystkie jego urządzenia zatwierdzają nowe konta)."""
    from emustart import accounts, remote
    name = remote._find_account(str(args.get("name", "")))
    if not name:
        return {"ok": False, "reason": "Nie ma takiego konta."}
    accounts.set_admin(name, bool(args.get("admin")), by="panel serwera")
    return {"ok": True, "name": name}


def device_games(_api, args: dict) -> dict:
    """Panel: czy urządzenie pobiera gry z serwera."""
    from emustart import devices
    return {"ok": devices.set_games(str(args.get("id", "")), bool(args.get("games")))}


def public_status(_api) -> dict:
    from emustart import public
    return {"ok": True, **public.status()}


def public_set(api, args: dict) -> dict:
    """Panel: dostęp z internetu (port publiczny przez UPnP) — włącz / wyłącz."""
    from emustart import config, public
    on = bool(args.get("enabled"))
    api._cfg["server_public"] = on
    config.save(api._cfg)
    if on:
        if public.CTX is None:
            return {"ok": False, "reason": "Port szyfrowany nie działa (brak certyfikatu) — sprawdź logi serwera."}
        st = public.start(api._cfg, public.CTX)
        if not st["enabled"]:
            return {"ok": False, "reason": st.get("error") or "Nie udało się otworzyć portu."}
    else:
        public.stop()
    return {"ok": True, **public.status()}


def tunnel_set(api, args: dict) -> dict:
    """Panel: adres tunelu Cloudflare (np. https://emustart.emucloud.org) — '' wyłącza."""
    from emustart import config, public
    try:
        url = public.set_tunnel(str(args.get("url", "")))
    except ValueError as ex:
        return {"ok": False, "reason": str(ex)}
    api._cfg["server_tunnel_url"] = url
    config.save(api._cfg)
    return {"ok": True, "url": url}


def login_code(_api, args: dict | None = None) -> dict:
    """Kod logowania konta (aplikacja: Zaloguj → kod QR / link / adres + kod): jednorazowy,
    przypisany do konta — zalogowanie nim nie czeka na zatwierdzenie (emustart/enroll.py).
    Konto istniejące albo nowe (`create` — powstanie przy zalogowaniu)."""
    from emustart import accounts, enroll, qr, remote
    args = args or {}
    name = str(args.get("account", "")).strip()
    names = remote.profile_names()
    match = next((a for a in names if a.lower() == name.lower()), "") or (accounts.record(name) or {}).get("name", "")
    if not name:
        return {"ok": False, "reason": "Wybierz konto albo wpisz nazwę nowego."}
    if not match and not args.get("create"):
        return {"ok": False, "reason": f"Nie ma konta „{name}”."}
    try:
        c = enroll.new_login_code(match or name, create=not match)
    except ValueError as ex:
        return {"ok": False, "reason": str(ex)}
    uri = remote.login_link(c["code"], c["account"])
    if not uri:
        return {"ok": False, "reason": "Port szyfrowany serwera nie działa — sprawdź logi serwera."}
    eps = remote.endpoints()
    return {"ok": True, **c, "uri": uri, "svg": qr.svg(uri, scale=4),
            "tunnel": next((e["url"] for e in eps if e.get("kind") == "tunnel"), "")}


def device_revoke(_api, args: dict) -> dict:
    from emustart import devices
    return {"ok": devices.revoke(str(args.get("id", "")))}


def device_rename(_api, args: dict) -> dict:
    from emustart import devices
    return {"ok": devices.rename(str(args.get("id", "")), str(args.get("name", "")))}


# ── przeniesienie profili z udziału NAS do katalogu serwera (2.0, etap 1) ──
_migr: dict = {"phase": "", "done": 0, "total": 0, "files": 0, "error": ""}


def profiles_migrate(api, args: dict) -> dict:
    """Bez confirm — plan (ile plików, ile danych); z confirm — kopiowanie w tle: każdy plik
    sprawdzony (rozmiar + SHA-1), dopiero potem serwer przełącza się na nowy folder. Stary
    folder zostaje nietknięty (z plikiem .emustart-moved) — jako zapas."""
    from emustart import nasfs, remote
    src, dst = remote.NAS_ROOT, paths.SERVER / "profiles"
    if not src:
        return {"ok": False, "reason": "Serwer nie ma folderu profili."}
    if src.resolve() == dst.resolve():
        return {"ok": False, "reason": "Profile są już w katalogu serwera.", "done": True}
    files = {k: v for k, v in nasfs.scan_dir(src).items() if k != nasfs.ROOT_MARK}
    size = sum(v[0] for v in files.values())
    if not args.get("confirm"):
        return {"ok": True, "src": str(src), "dst": str(dst), "files": len(files), "size": size}
    if _migr["phase"] in ("copying", "verifying"):
        return {"ok": False, "reason": "Przenoszenie już trwa."}
    _migr.update(phase="copying", done=0, total=size, files=len(files), error="")
    threading.Thread(target=_migrate_run, args=(api, src, dst, files), daemon=True, name="profile-2.0").start()
    return {"ok": True, "started": True}


def _migrate_run(api, src, dst, files: dict) -> None:
    import hashlib
    import shutil
    from emustart import config, nasfs, remote

    def sha(p):
        h = hashlib.sha1()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        return h.hexdigest()
    try:
        for rel in files:
            d = dst / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src / rel, d)
            _migr["done"] += files[rel][0]
        _migr.update(phase="verifying", done=0)
        for rel, (size, _mt) in files.items():
            a, b = src / rel, dst / rel
            if b.stat().st_size != size or sha(a) != sha(b):
                raise OSError(f"kopia różni się od oryginału: {rel}")
            _migr["done"] += size
        new_id = nasfs.root_id(dst, create=True)
        cfg = api._cfg
        cfg["profiles_nas"] = str(dst)
        config.save(cfg)
        (src / nasfs.MOVED_MARK).write_text(json.dumps({"to": str(dst), "time": time.time()}), encoding="utf-8")
        remote.NAS_ROOT, remote.NAS_ID = dst, new_id
        _migr["phase"] = "done"
        log.info("profile przeniesione: %s → %s (%d plików)", src, dst, len(files))
    except Exception as ex:
        log.exception("przenoszenie profili")
        _migr.update(phase="error", error=str(ex))


def profiles_migrate_status(_api) -> dict:
    from emustart import remote
    return {**_migr, "root": str(remote.NAS_ROOT or "")}


ACTIONS_ARGS = {"device_revoke": device_revoke, "device_rename": device_rename, "account_password": account_password,
                "profiles_migrate": profiles_migrate, 
                "public_set": public_set, "tunnel_set": tunnel_set,
                "enroll_decide": enroll_decide, "device_games": device_games, "account_admin": account_admin}
def config_get(api) -> dict:
    from emustart import srvconfig
    return srvconfig.get(api._cfg)


def config_set(api, args: dict) -> dict:
    from emustart import srvconfig
    return srvconfig.set_value(api, str(args.get("key", "")), args.get("value"))


# ── grafiki i opisy (pobiera serwer; urządzenia biorą je od niego) ──
def art_overview(api) -> dict:
    return {"ok": True, **api.art_overview()}


def art_start(api, args: dict) -> dict:
    mode = str(args.get("mode") or "all")
    if mode not in ("art", "meta", "meta_wiki", "all", "shrink"):
        return {"ok": False, "reason": "Nieznany rodzaj pobierania."}
    return api.art_start(str(args.get("es") or ""), mode)


def art_cancel(api) -> dict:
    api.art_cancel()
    return {"ok": True}


def launchbox_update(api) -> dict:
    return api.launchbox_update()


def art_keys_set(api, args: dict) -> dict:
    """Klucze źródeł grafik (SteamGridDB, IGDB, TheGamesDB) — zaszyfrowane na serwerze;
    puste pole zostawia zapisany klucz, „-” go usuwa."""
    from emustart import art_sources, config, secure
    keys = dict(api._cfg.get("art_keys") or {})
    for name in art_sources.KEY_NAMES:
        v = str(args.get(name) or "").strip()
        if v == "-":
            keys.pop(name, None)
        elif v:
            keys[name] = secure.protect(v)
    keys["source"] = "panel serwera"
    api._cfg["art_keys"] = keys
    config.save(api._cfg)
    return {"ok": True, "sources": art_sources.Sources(api._cfg).enabled()}


KEYS_FILE = "art_keys.json"


def import_keys_file(api) -> int:
    """Przy starcie serwera: klucze źródeł grafik z pliku <serwer>/art_keys.json
    ({"sgdb_key", "igdb_client_id", "igdb_client_secret", "tgdb_key"} jawnie) — zapisane
    zaszyfrowane jak z panelu, plik usuwany. Zwraca liczbę kluczy."""
    import json as _json
    f = paths.SERVER / KEYS_FILE
    if not f.is_file():
        return 0
    try:
        data = _json.loads(f.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as ex:
        log.warning("klucze grafik z %s: %s", f, ex)
        return 0
    from emustart import art_sources
    got = {k: str(data.get(k) or "").strip() for k in art_sources.KEY_NAMES} if isinstance(data, dict) else {}
    got = {k: v for k, v in got.items() if v}
    if got:
        art_keys_set(api, got)
    try:
        f.unlink()                       # jawne klucze nie zostają na dysku
    except OSError as ex:
        log.warning("nie mogę usunąć %s: %s", f, ex)
    log.info("klucze źródeł grafik z pliku %s: %s", KEYS_FILE, ", ".join(sorted(got)) or "brak")
    return len(got)


# ── konta: pliki i kopie zapasowe (emustart/acctbackup.py) ──
def _acct(fn):
    def run(api, args: dict) -> dict:
        try:
            return fn(api, args)
        except ValueError as ex:
            return {"ok": False, "reason": str(ex)}
    return run


@_acct
def account_files(api, args: dict) -> dict:
    from emustart import acctbackup
    name = str(args.get("name", ""))
    return {**acctbackup.browse(name), "backups": acctbackup.backups(api._cfg, name),
            "auto": api._cfg.get("backup_auto") is not False, "keep": int(api._cfg.get("backup_keep") or 14)}


@_acct
def account_backup(api, args: dict) -> dict:
    from emustart import acctbackup
    return acctbackup.backup(api._cfg, str(args.get("name", "")))


@_acct
def account_restore(api, args: dict) -> dict:
    from emustart import acctbackup
    return acctbackup.restore(api._cfg, str(args.get("name", "")), str(args.get("file", "")))


def backup_settings(api, args: dict) -> dict:
    from emustart import config
    if "auto" in args:
        api._cfg["backup_auto"] = bool(args["auto"])
    if "keep" in args:
        try:
            api._cfg["backup_keep"] = max(1, min(365, int(args["keep"])))
        except (TypeError, ValueError):
            return {"ok": False, "reason": "Podaj liczbę kopii (1–365)."}
    config.save(api._cfg)
    return {"ok": True, "auto": api._cfg.get("backup_auto") is not False, "keep": api._cfg["backup_keep"]}


# ── jedna gra: grafiki i opis; wygląd systemów ──
def _gid(args: dict) -> int:
    try:
        return int(args.get("id"))
    except (TypeError, ValueError):
        return 0


GAME_ACTIONS = {
    "games_search": lambda api, a: {"ok": True, "games": api.games_search(str(a.get("q", "")), str(a.get("es", "")))},
    "game_edit": lambda api, a: api.game_edit(_gid(a)),
    "game_meta_fetch": lambda api, a: api.meta_fetch(_gid(a), bool(a.get("full"))),
    "game_meta_save": lambda api, a: api.meta_save(_gid(a), a.get("edits") if isinstance(a.get("edits"), dict) else {}),
    "game_art_candidates": lambda api, a: {"ok": True, "list": api.art_candidates(_gid(a), str(a.get("kind", "")),
                                                                                  str(a.get("q", "")))},
    "game_art_choose": lambda api, a: api.art_choose(_gid(a), str(a.get("kind", "")), str(a.get("url", ""))),
    "game_art_clear": lambda api, a: api.art_clear(_gid(a), str(a.get("kind", ""))),
    "systems_look": lambda api, a: {"ok": True, "systems": api.systems_look()},
    "system_set": lambda api, a: api.system_set(str(a.get("es", "")), {k: a[k] for k in ("name", "glow", "enabled",
                                                                                         "emu_rec", "core_rec") if k in a}),
    "system_logo_candidates": lambda api, a: {"ok": True, "list": api.system_logo_candidates(str(a.get("es", "")))},
    "system_logo_choose": lambda api, a: api.system_logo_choose(str(a.get("es", "")), str(a.get("id", ""))),
    "system_merge": lambda api, a: api.system_merge(str(a.get("es", "")), str(a.get("into", ""))),
}
ACTIONS_ARGS.update(GAME_ACTIONS)
ACTIONS_ARGS.update({"move_prepare": move_prepare, "move_import": move_import, "update_apply": update_apply})
ACTIONS.pop("update_apply", None)
ACTIONS_ARGS.update({"config_set": config_set, "art_start": art_start, "art_keys_set": art_keys_set,
                     "account_files": account_files, "account_backup": account_backup,
                     "account_restore": account_restore, "backup_settings": backup_settings})
ACTIONS.update({"art": art_overview, "art_cancel": art_cancel, "launchbox_update": launchbox_update})


# ── emulatory: magazyn serwera (emustart/emustore.py) ──
def emu_status(api) -> dict:
    from emustart import emustore
    return emustore.status(api._cfg)


def emu_update(api, args: dict) -> dict:
    """Panel: sprawdź i pobierz nowe wersje teraz (wszystkie albo wybrane, `force` — ponownie)."""
    from emustart import emucatalog, emustore
    items = args.get("items")
    if items is not None:
        items = [str(i) for i in items if emucatalog.valid_item(str(i))]
    allow = [str(i) for i in args.get("allow_held") or [] if emucatalog.valid_item(str(i))]
    if allow:
        log.info("panel: zatwierdzona wstrzymana aktualizacja %s (stany PS2)", ", ".join(allow))
    return emustore.start_update(api._cfg, items, bool(args.get("force")), allow)


def emu_policy_get(api) -> dict:
    """Panel: wymagane ustawienia emulatorów (emustart/emupolicy.py) — tekst do edycji."""
    from emustart import emupolicy
    eff = emupolicy.effective(api._cfg)
    own = api._cfg.get("emu_policy") or {}
    return {"ok": True, "families": [{"family": f, "label": label, "text": emupolicy.format_values(eff[f]),
                                      "default": f not in own} for f, label in emupolicy.FAMILIES.items()]}


def emu_policy_set(api, args: dict) -> dict:
    """Panel: zapis wymaganych ustawień jednej rodziny; `reset` — wartości domyślne EmuStart."""
    from emustart import config, emupolicy
    fam = str(args.get("family", ""))
    if fam not in emupolicy.FAMILIES:
        return {"ok": False, "reason": "Nieznany emulator."}
    pol = dict(api._cfg.get("emu_policy") or {})
    if args.get("reset"):
        pol.pop(fam, None)
    else:
        values, why = emupolicy.parse(fam, str(args.get("text", "")))
        if why:
            return {"ok": False, "reason": why}
        pol[fam] = values
    api._cfg["emu_policy"] = pol
    config.save(api._cfg)
    log.info("panel: wymagane ustawienia %s: %s", fam, "domyślne" if args.get("reset") else pol[fam])
    return {"ok": True, **emu_policy_get(api)}


ACTIONS["emu_status"] = emu_status
ACTIONS_ARGS["emu_update"] = emu_update
ACTIONS["emu_policy"] = emu_policy_get
ACTIONS_ARGS["emu_policy_set"] = emu_policy_set
ACTIONS.update({"config": config_get, "public_status": public_status, "devices": devices_list, "profiles_migrate_status": profiles_migrate_status,
                "accounts": accounts_list, "invites": invites_list, "clients_status": clients_status,
                "clients_refresh": clients_refresh})
ACTIONS_ARGS.update({"login_code": login_code, "account_google_unlink": account_google_unlink,
                     "invite_create": invite_create, "invite_delete": invite_delete, "google_set": google_set})


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
        subprocess.Popen(_self_cmd() + ["--service"], close_fds=True, cwd=str(paths.APP),
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.Popen(_self_cmd() + ["--service"], close_fds=True, cwd=str(paths.APP),
                         start_new_session=True)
    return _wait(port, 30)


def _wait(port: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ping(port, 1.0):
            return True
        time.sleep(0.5)
    return False


# ── zapisy sprzed 0.46 → paczki synchronizacji (emustart/savemigrate.py) ──
_smig: dict = {}


def savesync_migrate(_api, args: dict) -> dict:
    """Panel: próba (dry — sam raport) albo migracja zapisów sprzed 0.46 do paczek (w tle)."""
    import threading
    from emustart import remote, savemigrate
    if not remote.NAS_ROOT:
        return {"ok": False, "reason": "Serwer nie ma folderu profili."}
    if _smig.get("phase") == "running":
        return {"ok": False, "reason": "Migracja już trwa."}
    dry = not args.get("confirm")
    _smig.clear()
    _smig.update(phase="running", dry=dry)

    def run():
        try:
            rep = savemigrate.run(remote.NAS_ROOT, dry=dry)
            _smig.update(phase="done", report=rep)
        except Exception as ex:
            log.exception("migracja zapisów")
            _smig.update(phase="error", error=str(ex))
    threading.Thread(target=run, daemon=True, name="migracja-zapisow").start()
    return {"ok": True, "dry": dry}


def savesync_migrate_status(_api) -> dict:
    from emustart import savemigrate
    rep = _smig.get("report") or savemigrate.last_report()
    out = {k: v for k, v in _smig.items() if k != "report"}
    if rep:
        out["report"] = {**rep, "items": rep.get("items", [])[:300]}
    return {"ok": True, **out}


def savesync_legacy(_api, args: dict) -> dict:
    """Wersje sprzed 0.46 w historii paczek konta (paczka już była — do przywrócenia)."""
    from emustart import savemigrate
    return {"ok": True, "items": savemigrate.legacy_versions(str(args.get("account", "")))}


def savesync_restore(_api, args: dict) -> dict:
    from emustart import savesync
    return savesync.restore(str(args.get("account", "")), str(args.get("es", "")).lower(), str(args.get("game_id", "")),
                            str(args.get("emulator", "")), str(args.get("core", "")), str(args.get("kind", "")),
                            str(args.get("bundle_id", "")), "panel", args.get("slot") or 1)


ACTIONS["savesync_migrate_status"] = savesync_migrate_status
ACTIONS_ARGS.update({"savesync_migrate": savesync_migrate, "savesync_legacy": savesync_legacy,
                     "savesync_restore": savesync_restore})
