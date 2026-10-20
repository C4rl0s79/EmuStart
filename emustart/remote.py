"""remote — API dla urządzeń (EmuStart na PC i Androidzie) w EmuStart Server
(`EmuStartServer.exe --service`).

Serwer działa na komputerze z grami (NAS) i udostępnia przez HTTP (Tailscale):

    GET /v1/info                         wersja, nazwa serwera
    GET /v1/systems                      systemy z liczbą gier i logo
    GET /v1/systems/<es>/games           gry systemu (tytuł, tagi, rozmiar, gatunek,
                                         rok, gracze, adresy okładki/zrzutu/logo)
    GET /v1/games/<id>                   szczegóły: metadane, opis, pliki
    GET /v1/games/<id>/file/<ścieżka>    plik gry — z obsługą Range (pobieranie
                                         kilkoma strumieniami, wznawianie)
    GET /media/…                         grafiki (jak w UI)

Folder profili (zapisy, ustawienia, blokady — EmuStart na Windows i Androidzie):

    GET    /v1/nas/scan?p=…              pliki pod ścieżką: {ścieżka: [rozmiar, czas]}
    GET    /v1/nas/list?p=…              zawartość folderu: [[nazwa, czy_folder]]
    GET    /v1/nas/stat?p=…              rozmiar i czas pliku
    GET    /v1/nas/file?p=…              plik (zstd, gdy klient obsługuje); X-Mtime
    PUT    /v1/nas/file?p=…&mtime=…&backup=…   zapis atomowy; poprzednia wersja do
                                         kopii zapasowej (przycinanej do ostatnich 10)
    DELETE /v1/nas/file?p=…

Każde zapytanie wymaga nagłówka `Authorization: Bearer <token sesji>` — token z logowania
podpisem kluczem urządzenia (emustart/enroll.py, emustart/devices.py). Wspólny klucz serwera
(config.json → server_token) działa wyłącznie na tym komputerze. Konta: zakres urządzenia
i hasła (emustart/accounts.py).
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import socket
import urllib.parse
from pathlib import Path

from emustart import __version__, config

log = logging.getLogger("emustart.remote")

API = None                    # emustart.api.Api (tryb serwera)
NAS_ROOT: Path | None = None  # folder profili na tym komputerze (None = nie znaleziono)
NAS_ID = ""
DEFAULT_PORT = 8740
TLS: dict = {}                # {"port", "fp"} — port szyfrowany (emustart/tls.py), gdy działa
PORT = DEFAULT_PORT
CHUNK = 1024 * 1024


def ensure_token(cfg: dict) -> str:
    if not cfg.get("server_token"):
        cfg["server_token"] = secrets.token_urlsafe(24)
        config.save(cfg)
    return cfg["server_token"]


def authorized(handler) -> bool:
    """Token sesji urządzenia konta (logowanie podpisem kluczem urządzenia, emustart/enroll.py);
    wspólny klucz serwera — tylko z tego komputera. handler.device = id urządzenia ('' = ten
    komputer)."""
    if API is None:
        return False
    auth = handler.headers.get("Authorization") or ""
    got = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not got:
        return False
    token = API._cfg.get("server_token") or ""
    if token and hmac.compare_digest(got.encode(), token.encode()):
        # wspólny klucz serwera — tylko na tym komputerze (nie z sieci, nie przez tunel)
        if not _local_peer(handler):
            return False
        handler.device = ""
        return True
    from emustart import devices
    did = devices.verify(got)
    if did:
        handler.device = did
        devices.touch(did, handler.client_address[0], handler.headers.get("X-EmuStart-Version") or "",
                      handler.headers.get("X-EmuStart-Hw") or "")
        return True
    return False


def _local_peer(handler) -> bool:
    import ipaddress
    try:
        local = ipaddress.ip_address(handler.client_address[0]).is_loopback
    except ValueError:
        local = False
    return local and not handler.headers.get("CF-Connecting-IP")


def _trust() -> dict:
    """Do odpowiedzi zalogowania i /v1/info: adresy serwera i odcisk certyfikatu (przypinany
    w kliencie)."""
    return {"endpoints": endpoints(), "tls": {"port": TLS["port"], "fp": TLS["fp"]} if TLS else None}


def kind_of(host: str) -> str:
    """lan / tailscale / public — rodzaj adresu (kolejność wyboru w klientach)."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "public" if "." in host else "lan"            # nazwa komputera w sieci lokalnej
    if ip in ipaddress.ip_network("100.64.0.0/10"):
        return "tailscale"
    if ip.is_private or ip.is_link_local:
        return "lan"
    return "public"


def endpoints() -> list:
    """Adresy, pod którymi klienci mogą dosięgnąć serwer (klient wybiera najlepszy) — tylko
    szyfrowane (0.43): sieć lokalna i Tailscale na porcie HTTPS z certyfikatem serwera
    (przypinanym), port publiczny i tunel. Zwykły port obsługuje API tylko z tego komputera."""
    from emustart import srvadmin
    out = []
    if TLS:
        for a in srvadmin._addresses():
            out.append({"url": f"https://{a}:{TLS['port']}", "kind": kind_of(a), "tls": True})
    from emustart import public
    for extra in (public.endpoint(), public.tunnel_endpoint()):
        if extra:
            out.append(extra)
    return out


def _body(handler) -> dict:
    n = int(handler.headers.get("Content-Length") or 0)
    try:
        d = json.loads(handler.rfile.read(min(n, 4096)) or b"{}") if n else {}
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def enroll_flow(handler, op: str) -> None:
    """Zalogowanie urządzenia i logowanie podpisem (emustart/enroll.py):
    POST /v1/enroll/request {mode, account, password, name, class, version, pubkey[, login_code]}
    POST /v1/enroll/poll {rid}                                               — bez klucza
    POST /v1/auth/challenge {device}, /v1/auth/token {device, nonce, sig}    — bez klucza
    POST /v1/enroll/pending, /v1/enroll/decide {id, allow, games}            — z kluczem"""
    from emustart import enroll, public
    d = _body(handler)
    ip = public.client_ip(handler)
    if op == "request":
        r = enroll.request(str(d.get("mode", "")), str(d.get("account", "")), str(d.get("name", "")),
                           str(d.get("class", "")), ip, str(d.get("version", "")), str(d.get("pubkey", "")),
                           profile_names(), str(d.get("hw", "")), str(d.get("password", "")),
                           str(d.get("login_code", "")), create_account)
        return _json(handler, r, 200 if r.get("ok") else 400)
    if op == "poll":
        r = enroll.poll(str(d.get("rid", "")))
        if r.get("status") == "approved":
            r.update(server=socket.gethostname(), **_trust())
        return _json(handler, r)
    if op == "challenge":
        r = enroll.challenge(str(d.get("device", "")))
        return _json(handler, r, 200 if r.get("ok") else 403)
    if op == "token":
        r = enroll.token(str(d.get("device", "")), str(d.get("nonce", "")), str(d.get("sig", "")))
        if not r.get("ok"):
            public._fail(ip)
        return _json(handler, r, 200 if r.get("ok") else 403)
    if not authorized(handler):
        return _json(handler, {"ok": False, "reason": "brak lub zły klucz"}, 401)
    viewer = getattr(handler, "device", "")
    if op == "account":                     # nowe konto z zalogowanego urządzenia (inny użytkownik)
        from emustart import public as _pub
        r = enroll.request_account(viewer, str(d.get("account", "")), str(d.get("password", "")),
                                   _pub.client_ip(handler), profile_names(), create_account)
        if r.get("status") == "approved":
            from emustart import accounts
            r["token"] = accounts.token(_secret(), r["account"])
        return _json(handler, r, 200 if r.get("ok") else 400)
    if op == "account_poll":
        r = enroll.poll_account(str(d.get("rid", "")), viewer)
        if r.get("status") == "approved":
            from emustart import accounts
            r["token"] = accounts.token(_secret(), r["account"])     # dostęp tego urządzenia do konta
        return _json(handler, r)
    if op == "logout":                      # urządzenie wylogowuje się samo (unieważnienie)
        from emustart import devices
        return _json(handler, {"ok": bool(viewer) and devices.revoke(viewer)})
    if op == "pending":
        return _json(handler, {"ok": True, "requests": enroll.pending(viewer)})
    if op == "decide":
        return _json(handler, enroll.decide(str(d.get("id", "")), bool(d.get("allow")), bool(d.get("games")),
                                            viewer, create_account))
    return _json(handler, {"ok": False, "reason": "nieznana operacja"}, 404)


def create_account(name: str) -> None:
    """Nowe konto zatwierdzone przez administratora — folder profilu (hasło konta ustawia
    emustart/enroll.py; PIN jest lokalny)."""
    from emustart import nasfs
    if NAS_ROOT is None:
        return
    fs = nasfs.DirFs(NAS_ROOT)
    if not fs.exists(f"{name}/emustart.json"):
        fs.write(f"{name}/emustart.json", b"{}")
    log.info("nowe konto %s", name)


def _seen(handler) -> None:
    """Klient z kluczem — do listy urządzeń w panelu serwera."""
    from emustart import srvadmin
    srvadmin.seen(handler.client_address[0])


GZIP_MIN = 32 * 1024


def _json(handler, data, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    gz = len(body) >= GZIP_MIN and "gzip" in (handler.headers.get("Accept-Encoding") or "")
    if gz:                             # listy gier: kilka MB JSON-a → kilkaset KB (telefon, tunel)
        import gzip
        body = gzip.compress(body, 5)
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    if gz:
        handler.send_header("Content-Encoding", "gzip")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


_games_cache: dict = {}             # es → (czas, wiersze) — strony listy z jednego liczenia
GAMES_TTL = 120


def _games_rows(es: str) -> list:
    """Lista gier systemu dla urządzeń — liczona raz na GAMES_TTL s (metadane systemu, grafiki),
    więc kolejne strony tej samej listy są natychmiast."""
    import time as _t
    hit = _games_cache.get(es)
    if hit and _t.monotonic() - hit[0] < GAMES_TTL:
        return hit[1]
    from emustart import savesync
    # game_id — stały identyfikator gry dla synchronizacji zapisów (ten sam na każdym urządzeniu)
    rows = [dict(_game_row(r), game_id=savesync.game_id(es, r.get("name", ""))) for r in API.list_games(es)]
    _games_cache[es] = (_t.monotonic(), rows)
    return rows


def _game_row(r: dict) -> dict:
    keep = ("id", "name", "title", "tags", "size", "genre", "year", "players", "developer",
            "publisher", "regions", "box", "snap", "logo")
    return {k: r.get(k) for k in keep}


def handle(handler) -> None:
    """GET /v1/… (handler: BaseHTTPRequestHandler serwera)."""
    if not authorized(handler):
        return _json(handler, {"error": "brak lub zły klucz serwera"}, 401)
    _seen(handler)
    path = urllib.parse.unquote(urllib.parse.urlsplit(handler.path).path)
    parts = [p for p in path.split("/") if p][1:]           # bez „v1”
    try:
        if parts == ["info"]:
            feats = ["find", "system", "bios", "media", "library", "emulators"] + (["nas", "savemgr", "profiles"] if NAS_ROOT else [])
            return _json(handler, {"name": socket.gethostname(), "version": __version__, "api": 1,
                                   "features": feats + ["admin"], "nas_id": NAS_ID, "endpoints": endpoints(),
                                   "admin": _admin(handler),
                                   "tls": {"port": TLS["port"], "fp": TLS["fp"]} if TLS else None})
        if parts and parts[0] == "nas":
            return _nas(handler, "GET", parts[1:])
        if parts and parts[0] == "admin":
            return _admin_route(handler, "GET", parts[1:])
        if parts == ["library"]:                       # biblioteka dla komputera bez folderów z grami
            if not _games_ok(handler):
                return _json(handler, {"error": "To konto nie pobiera gier z serwera."}, 403)
            from emustart import libsync
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
            es = (q.get("es") or [""])[0]
            if es:
                try:
                    since = float((q.get("since") or ["0"])[0])
                except ValueError:
                    since = 0.0
                return _json(handler, {"games": libsync.server_games(es, since)})
            return _json(handler, {"systems": libsync.server_systems()})
        if parts == ["sysinfo"]:                       # opis platformy (producent, lata, opis) dla telefonu
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
            return _json(handler, API.system_info((q.get("es") or [""])[0]))
        if parts == ["find"]:
            if not _games_ok(handler):
                return _json(handler, {"error": "To konto nie pobiera gier z serwera."}, 403)
            return _find(handler)
        if parts == ["savemgr", "catalog"]:
            return _savemgr_catalog(handler)
        if parts == ["profiles"]:
            return _profiles_get(handler)
        if parts == ["bios"]:
            return _bios_get(handler)
        if parts and parts[0] == "emulators":
            return _emulators_get(handler, parts[1:])
        if len(parts) == 4 and parts[0] == "savesync" and parts[3] in ("manifest", "status"):
            return savesync_get(handler, parts[1], parts[2], parts[3] == "manifest")
        if parts == ["systems"]:
            from emustart import emucatalog, emulators, systems
            out = []
            for s_ in API.list_systems():
                d = {k: s_.get(k) for k in ("es", "display", "games", "logo", "logo_glow", "kind")}
                plat = systems.info(s_["es"])["plat"]
                d["plat"] = plat
                sc = (API._cfg.get("systems") or {}).get(s_["es"]) or {}
                # rdzeń RetroArcha (Android: *_libretro_android.so) — ten, którego używa komputer, gdy go zgłosił
                d["core"] = sc.get("core_rec") or emucatalog.CORES.get(plat, "")
                d["emu_rec"] = sc.get("emu_rec", "")
                d["cores"] = emulators.cores_for(plat)         # wszystkie pasujące — wybór na telefonie
                out.append(d)
            return _json(handler, out)
        if len(parts) == 3 and parts[0] == "systems" and parts[2] == "games":
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
            rows = _games_rows(parts[1])
            if "offset" not in q and "limit" not in q:
                return _json(handler, rows)                 # cała lista (dawni klienci)
            try:
                off = max(0, int((q.get("offset") or ["0"])[0]))
                lim = max(1, min(5000, int((q.get("limit") or ["1000"])[0])))
            except ValueError:
                return _json(handler, {"error": "zły offset/limit"}, 400)
            return _json(handler, {"total": len(rows), "offset": off, "games": rows[off:off + lim]})
        if len(parts) == 2 and parts[0] == "games" and parts[1].isdigit():
            d = API.game_detail(int(parts[1]), use_variant=False)
            if not d:
                return _json(handler, {"error": "nie ma takiej gry"}, 404)
            if not d.get("meta_online") and hasattr(API, "meta_auto"):
                API.meta_auto(int(parts[1]))           # opis z sieci w tle — przy następnym pytaniu jest
            from emustart import library
            g = library.game(int(parts[1]))
            # ścieżki z „/” — Android traktuje „\\” jako znak w nazwie pliku (EPERM)
            d["files"] = [{"path": rel.replace("\\", "/"), "size": size} for rel, size, _m in g["files"]]
            # arcade: rodzic i BIOS (np. neogeo.zip) obok gry — jak na Windows
            d["files"] += [{"path": rel.replace("\\", "/"), "size": size, "dep": True}
                           for rel, _p, size in _arcade_deps(g)]
            d["file"] = str(d.get("file") or "").replace("\\", "/")
            d["es"] = g["es"]
            from emustart import systems
            info = systems.info(g["es"])
            d["multidisc"] = bool(g.get("multidisc"))          # playlista .m3u na telefonie
            d["kind"] = info["kind"]
            d["exts"] = sorted(systems.ext_set(info))          # ROM w rozpakowanym ZIP-ie
            return _json(handler, d)
        if len(parts) >= 4 and parts[0] == "games" and parts[1].isdigit() and parts[2] == "file" \
                and not _games_ok(handler):
            return _json(handler, {"error": "To konto nie pobiera gier z serwera."}, 403)
        if len(parts) >= 4 and parts[0] == "games" and parts[1].isdigit() and parts[2] == "file":
            return _send_file(handler, int(parts[1]), "/".join(parts[3:]))
        return _json(handler, {"error": "nieznany adres"}, 404)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception:
        log.exception("API zdalne %s", path)
        try:
            _json(handler, {"error": "błąd serwera (szczegóły w logu serwera)"}, 500)
        except OSError:
            pass


def find_nas_root(cfg: dict) -> Path | None:
    r"""Folder profili (kont) serwera: `profiles_nas` z panelu, domyślnie <serwer>\profiles —
    zakładany przy starcie. Bez niego serwer nie zna żadnego konta: nowe konto po zatwierdzeniu
    nie miało folderu, a klient nie dostawał go na liście „Kto gra?”."""
    from emustart import paths
    own = Path(cfg.get("profiles_nas") or (paths.SERVER / "profiles"))
    try:
        own.mkdir(parents=True, exist_ok=True)
        return own
    except OSError as ex:                     # np. dysk z ustawień niepodłączony
        log.error("folder profili %s: %s", own, ex)
        return None


def init_nas(cfg: dict) -> None:
    """Przy starcie serwera: folder profili i jego znacznik (klient sprawdza, że przez
    SMB widzi ten sam folder)."""
    global NAS_ROOT, NAS_ID
    from emustart import nasfs
    NAS_ROOT = find_nas_root(cfg)
    NAS_ID = nasfs.root_id(NAS_ROOT, create=True) if NAS_ROOT else ""
    log.info("folder profili serwera: %s", NAS_ROOT or "niedostępny")
    if NAS_ROOT is not None:
        # konta zatwierdzone, gdy folderu profili nie było (albo po zmianie folderu) — bez
        # swojego folderu nie ma ich na liście kont ani w „Kto gra?” na urządzeniach
        from emustart import accounts, devices, enroll
        have = {n.lower() for n in profile_names()}
        known = devices.accounts_in_use() | {(accounts.record(n) or {}).get("name", n)
                                             for n in accounts.names_with_password()}
        for name in sorted(known):
            if name.lower() not in have and enroll._safe_account(name) == name:
                create_account(name)


def handle_write(handler, method: str) -> None:
    """PUT/DELETE /v1/nas/… (zapis w folderze profili)."""
    if not authorized(handler):
        return _json(handler, {"error": "brak lub zły klucz serwera"}, 401)
    _seen(handler)
    path = urllib.parse.unquote(urllib.parse.urlsplit(handler.path).path)
    parts = [p for p in path.split("/") if p][1:]
    if parts and parts[0] == "admin":
        return _admin_route(handler, method, parts[1:])
    if method == "PUT" and len(parts) == 2 and parts[0] == "profiles":
        return _profiles_put(handler, parts[1])
    if method == "PUT" and parts == ["savemgr", "copy"]:
        return _savemgr_copy(handler)
    if not parts or parts[0] != "nas":
        n = int(handler.headers.get("Content-Length") or 0)
        if n:
            handler.rfile.read(min(n, 1 << 20))
        handler.close_connection = True
        return _json(handler, {"error": "nieznany adres"}, 404)
    # treść odczytana od razu: odpowiedź z błędem przy nieprzeczytanej treści psuje
    # utrzymywane połączenie (Windows zrywa je zamiast zamknąć)
    from emustart import nasfs
    n = int(handler.headers.get("Content-Length") or 0)
    if n > nasfs.MAX_BODY:
        handler.close_connection = True
        return _json(handler, {"error": "za duży plik"}, 413)
    handler.body = handler.rfile.read(n) if n else b""
    try:
        return _nas(handler, method, parts[1:])
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception:
        log.exception("API zdalne %s %s", method, path)
        handler.close_connection = True
        try:
            _json(handler, {"error": "błąd serwera (szczegóły w logu serwera)"}, 500)
        except OSError:
            pass


def _nas(handler, method: str, parts: list) -> None:
    from emustart import nasfs
    import time as _time
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    arg = lambda k: (q.get(k) or [""])[0]      # noqa: E731
    if NAS_ROOT is None:
        return _json(handler, {"error": "serwer nie ma folderu profili"}, 404)
    try:
        rel = nasfs.clean_rel(arg("p"))
    except ValueError as ex:
        return _json(handler, {"error": str(ex)}, 400)
    target = NAS_ROOT / rel if rel else NAS_ROOT
    op = parts[0] if parts else ""
    # dawne skróty PIN-u (.auth.json) nigdy nie wychodzą z serwera; pliki konta z hasłem —
    # urządzeniu tego konta albo z tokenem konta (emustart/accounts.py)
    if _is_auth(rel) or (arg("backup") and _is_auth(arg("backup"))):
        return _json(handler, {"error": "niedostępne"}, 403)
    if not rel and op == "scan":
        return _json(handler, {"error": "cały folder profili jest niedostępny"}, 403)
    for r in (rel, arg("backup")):
        # inne konto — po zalogowaniu jego hasłem („pin”: klient pyta o hasło konta)
        if r and not (_scope_ok(handler, r.replace("\\", "/").split("/")[0])
                      and _profile_ok(handler, r.replace("\\", "/").split("/")[0])):
            return _json(handler, {"error": "konto wymaga hasła", "pin": True}, 403)
    if method == "GET" and op == "scan":
        files = {k: list(v) for k, v in nasfs.scan_dir(target).items() if not _is_auth(k)}
        return _json(handler, {"files": files})
    if method == "GET" and op == "list":
        try:
            ents = [[e.name, e.is_dir()] for e in os.scandir(target) if not _is_auth(e.name)
                    and (rel or _scope_ok(handler, e.name))]
        except OSError:
            return _json(handler, {"error": "nie ma"}, 404)
        return _json(handler, {"entries": ents})
    if method == "GET" and op == "stat":
        try:
            st = target.stat()
        except OSError:
            return _json(handler, {"error": "nie ma"}, 404)
        return _json(handler, {"size": st.st_size, "mtime": st.st_mtime, "dir": target.is_dir()})
    if method == "GET" and op == "file":
        try:
            st = target.stat()
            data = target.read_bytes() if target.is_file() else None
        except OSError:
            data = None
        if data is None:
            return _json(handler, {"error": "nie ma"}, 404)
        enc = ""
        z = nasfs._zstd()
        if z and "zstd" in (handler.headers.get("Accept-Encoding") or "") and len(data) >= nasfs.ZSTD_MIN:
            packed = z.compress(data, 3)
            if len(packed) < len(data):
                data, enc = packed, "zstd"
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("X-Mtime", repr(st.st_mtime))
        if enc:
            handler.send_header("Content-Encoding", enc)
        handler.end_headers()
        handler.wfile.write(data)
        return
    if method == "PUT" and op == "file":
        if not rel or rel == nasfs.ROOT_MARK:
            return _json(handler, {"error": "zła ścieżka"}, 400)
        body = getattr(handler, "body", b"")
        if (handler.headers.get("Content-Encoding") or "") == "zstd":
            body = nasfs._zstd().decompress(body)
        backup = arg("backup")
        mtime = float(arg("mtime") or 0) or _time.time()
        try:
            same = target.is_file() and target.stat().st_size == len(body) and target.read_bytes() == body
        except OSError:
            same = False
        if same:                               # ta sama treść (np. emulator tylko otworzył kartę)
            os.utime(target, (_time.time(), mtime))
            return _json(handler, {"ok": True, "size": len(body), "same": True})
        if backup:
            brel = nasfs.clean_rel(backup)
            nasfs.backup_copy(target, NAS_ROOT / brel)
            broot = nasfs.backup_root(brel)
            if broot:
                nasfs.prune_backups(NAS_ROOT / broot)
        nasfs.write_atomic(target, body, mtime)
        return _json(handler, {"ok": True, "size": len(body)})
    if method == "DELETE" and op == "file":
        if rel and rel != nasfs.ROOT_MARK and target.is_file():
            target.unlink()
        return _json(handler, {"ok": True})
    return _json(handler, {"error": "nieznana operacja"}, 404)


# ── konta: zakres urządzenia, hasło (emustart/accounts.py) ──

def _is_auth(rel: str) -> bool:
    from emustart import pinauth
    return rel.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1].lower() == pinauth.AUTH


def _secret() -> str:
    cfg = API._cfg
    if not cfg.get("profile_secret"):
        cfg["profile_secret"] = secrets.token_urlsafe(32)
        config.save(cfg)
    return cfg["profile_secret"]


def _scope(handler) -> str:
    """Konto, do którego ogranicza się urządzenie zapytania ('' = wszystkie: administrator,
    urządzenia sprzed 0.37, wspólny klucz serwera)."""
    from emustart import devices
    return devices.account_of(getattr(handler, "device", ""))


def _scope_ok(handler, profile: str) -> bool:
    """Swoje konto; inne konto — gdy na tym urządzeniu zalogował się jego użytkownik (hasło
    → token konta w nagłówku X-Profile-Token)."""
    from emustart import accounts
    sc = _scope(handler)
    if not sc or profile.lower() == sc.lower():
        return True
    return bool(profile) and accounts.token_ok(_secret(), profile, handler.headers.get("X-Profile-Token") or "")


def _admin_route(handler, method: str, p: list) -> None:
    """Grafiki i opisy gier, logo i nazwy systemów — z EmuStart na PC albo telefonie
    administratora (te same funkcje co panel serwera). Zmiana podbija wersję systemu, więc
    pozostałe urządzenia pobiorą ją przy najbliższej synchronizacji.

        GET    admin/games/<id>/art?kind=&q=    propozycje grafik
        PUT    admin/games/<id>/art?kind=&url=  wybór grafiki
        DELETE admin/games/<id>/art?kind=       usunięcie grafiki
        PUT    admin/games/<id>/meta            {"edits": {...}} ręczne poprawki opisu
        PUT    admin/games/<id>/meta/fetch?full=1  opis z sieci (w tle)
        GET    admin/systems/<es>/logos         propozycje logo systemu
        PUT    admin/systems/<es>/logo?id=      wybór logo
        PUT    admin/systems/<es>               {"name", "glow", "enabled", "emu_rec", "core_rec"}"""
    n = int(handler.headers.get("Content-Length") or 0)
    if n > (1 << 20):
        handler.close_connection = True
        return _json(handler, {"error": "za dużo danych"}, 413)
    raw = handler.rfile.read(n) if n else b""
    if not _admin(handler):
        return _json(handler, {"error": "Tylko urządzenie administratora."}, 403)
    if not hasattr(API, "art_choose"):
        return _json(handler, {"error": "nieznany adres"}, 404)
    q = {k: (v or [""])[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query).items()}
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return _json(handler, {"error": "zły JSON"}, 400)
    if not isinstance(body, dict):
        body = {}
    out = None
    if len(p) >= 3 and p[0] == "games" and p[1].isdigit():
        gid = int(p[1])
        if p[2:] == ["art"] and method == "GET":
            out = {"ok": True, "list": API.art_candidates(gid, q.get("kind", ""), q.get("q", ""))}
        elif p[2:] == ["art"] and method == "PUT":
            out = API.art_choose(gid, q.get("kind", ""), q.get("url", ""))
        elif p[2:] == ["art"] and method == "DELETE":
            out = API.art_clear(gid, q.get("kind", ""))
        elif p[2:] == ["meta"] and method == "PUT":
            out = API.meta_save(gid, body.get("edits") if isinstance(body.get("edits"), dict) else {})
        elif p[2:] == ["meta", "fetch"] and method == "PUT":
            out = API.meta_fetch(gid, q.get("full") == "1")
    elif len(p) >= 2 and p[0] == "systems":
        es = p[1]
        if p[2:] == ["logos"] and method == "GET":
            out = {"ok": True, "list": API.system_logo_candidates(es)}
        elif p[2:] == ["logo"] and method == "PUT":
            out = API.system_logo_choose(es, q.get("id", ""))
        elif not p[2:] and method == "PUT":
            out = API.system_set(es, {k: body[k] for k in ("name", "glow", "enabled", "emu_rec", "core_rec") if k in body})
    elif p == ["art_keys"] and method == "PUT":
        # klucze źródeł grafik z komputera administratora (np. z PyLinks, odszyfrowane tam) —
        # tylko połączeniem szyfrowanym (_v1_ok); serwer szyfruje je własnym kluczem
        from emustart import srvadmin
        out = srvadmin.art_keys_set(API, body)
    if out is None:
        return _json(handler, {"error": "nieznany adres"}, 404)
    _games_cache.clear()
    log.info("administrator %s: %s %s", _who(handler), method, "/".join(p))
    return _json(handler, out)


def _admin(handler) -> bool:
    from emustart import devices
    return devices.is_admin(getattr(handler, "device", ""))


def _games_ok(handler) -> bool:
    from emustart import devices
    did = getattr(handler, "device", "")
    return not did or devices.games_ok(did)


def _profile_ok(handler, profile: str) -> bool:
    """Konto z hasłem (0.40) — urządzenie tego konta (klucz urządzenia) albo token konta
    (użytkownik podał hasło na tym urządzeniu). Konto bez hasła — tylko urządzenia
    administratora i urządzenia tego konta (do czasu ustawienia hasła). PIN jest lokalny."""
    from emustart import accounts, devices
    sc = _scope(handler)
    # własne konto urządzenia — zawsze, także gdy konto jest administratorem (wtedy zakres
    # jest pusty: „wszystkie konta”) — 0.43–0.44: urządzenia konta administratora dostawały
    # 403 „konto wymaga hasła” na własnych zapisach
    own = devices.account_raw(getattr(handler, "device", ""))
    if profile and own and profile.lower() == own.lower():
        return True
    if sc and profile and profile.lower() == sc.lower():
        return True
    if not profile or profile.startswith((".", "_")) or NAS_ROOT is None:
        return not sc
    if accounts.has_password(profile):
        return accounts.token_ok(_secret(), profile, handler.headers.get("X-Profile-Token") or "")
    return not sc


def profile_names() -> list:
    """Profile w folderze profili (bez przeniesionych po zmianie nazwy)."""
    try:
        ents = [e for e in os.scandir(NAS_ROOT) if e.is_dir() and not e.name.startswith((".", "_"))]
    except OSError:
        return []
    marks = ("save", "settings", "emustart.json", "retroachievements.json", ".auth.json")
    return sorted((e.name for e in ents if not os.path.exists(os.path.join(e.path, "moved.json"))
                   and any(os.path.exists(os.path.join(e.path, m)) for m in marks)), key=str.lower)


def _profiles_get(handler) -> None:
    """GET /v1/profiles — [{name, password}]: konta i czy mają hasło („pin” — to samo, dla
    klientów sprzed 0.40)."""
    from emustart import accounts
    if NAS_ROOT is None:
        return _json(handler, {"error": "serwer nie ma folderu profili"}, 404)
    with_pw = accounts.names_with_password()
    return _json(handler, {"profiles": [{"name": n, "password": n.lower() in with_pw, "pin": n.lower() in with_pw}
                                        for n in profile_names() if _scope_ok(handler, n)]})


def _profiles_put(handler, op: str) -> None:
    """PUT /v1/profiles/login|create|password — JSON {name, password[, old]} w treści
    (hasło nigdy w adresie — trafiłoby do logów). emustart/accounts.py.

    login    — hasło konta → token konta (inny użytkownik na tym urządzeniu);
               konto bez hasła: needs_password
    create   — nowe konto z hasłem (urządzenie administratora); nazwa zajęta → 409
    password — ustawienie hasła (konto bez hasła: urządzenie tego konta albo administrator)
               albo zmiana (z dotychczasowym `old`)
    """
    from emustart import accounts, nasfs, public
    d = _body(handler)
    if NAS_ROOT is None:
        return _json(handler, {"error": "serwer nie ma folderu profili"}, 404)
    fs = nasfs.DirFs(NAS_ROOT)
    wanted, pw = str(d.get("name") or "").strip(), str(d.get("password") or "")
    ip = public.client_ip(handler)
    existing = _find_account(wanted)
    if op == "create":
        if _scope(handler):
            return _json(handler, {"error": "To urządzenie ma dostęp tylko do swojego konta."}, 403)
        if existing:
            return _json(handler, {"error": f"Profil „{existing}” już istnieje.", "exists": True,
                                   "name": existing}, 409)
        name = _safe_profile_name(wanted)
        if not name:
            return _json(handler, {"error": "Zła nazwa profilu."}, 400)
        why = accounts.set_password(name, pw, by=_who(handler))
        if why:
            return _json(handler, {"error": why}, 400)
        fs.write(f"{name}/emustart.json", b"{}")
        log.info("nowe konto %s (z hasłem)", name)
        return _json(handler, {"ok": True, "name": name, "token": accounts.token(_secret(), name)})
    if not existing:
        return _json(handler, {"error": f"Nie ma konta „{wanted}”.", "missing": True}, 404)
    if op == "login":
        ok, why, wait = accounts.verify(existing, pw, ip)
        if why == "brak":
            return _json(handler, {"ok": True, "name": existing, "token": "", "needs_password": True})
        if not ok:
            if getattr(handler.server, "public", False):
                public._fail(ip)                 # z internetu: jak zgadywanie klucza — blokada adresu
            return _json(handler, {"error": why, "retry_after": wait}, 429 if wait else 403)
        return _json(handler, {"ok": True, "name": existing, "token": accounts.token(_secret(), existing)})
    if op == "password":
        why = accounts.policy(pw)
        if why:
            return _json(handler, {"error": why}, 400)
        if accounts.has_password(existing):
            ok, why, wait = accounts.verify(existing, str(d.get("old") or ""), ip)
            if not ok:
                return _json(handler, {"error": "Złe dotychczasowe hasło." if why == "Złe hasło." else why,
                                       "retry_after": wait}, 429 if wait else 403)
        elif not _scope_ok(handler, existing):
            return _json(handler, {"error": "To urządzenie ma dostęp tylko do swojego konta."}, 403)
        accounts.set_password(existing, pw, by=_who(handler))
        return _json(handler, {"ok": True, "name": existing, "token": accounts.token(_secret(), existing)})
    return _json(handler, {"error": "nieznana operacja"}, 404)


def _find_account(name: str) -> str:
    """Istniejące konto (folder profilu albo hasło na serwerze) bez względu na wielkość liter."""
    from emustart import accounts, pinauth
    hit = pinauth.find(profile_names(), name)
    if hit:
        return hit
    rec = accounts.record(name)
    return rec.get("name", "") if rec else ""


def _who(handler) -> str:
    from emustart import devices
    did = getattr(handler, "device", "")
    return devices.name_of(did) if did else "serwer"


def _safe_profile_name(name: str) -> str:
    from emustart import nasfs
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")[:32].strip(" .")
    try:
        ok = bool(n) and nasfs.clean_rel(n) == n and not n.startswith(("_", "."))
    except ValueError:
        ok = False
    return n if ok else ""


def _find(handler) -> None:
    """Gra po (system, ścieżka względna) — EmuStart na PC pobiera ją stąd. Odpowiedź: id, pliki
    (ścieżka, rozmiar) i — arcade — zależności (gra nadrzędna, BIOS: obok gry, jak na telefonie)."""
    from emustart import library
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    es, rel = (q.get("es") or [""])[0], (q.get("rel") or [""])[0].replace("\\", "/")
    if not es or not rel:
        return _json(handler, {"error": "brak es/rel"}, 400)
    rows = library.db().execute("SELECT id, rel, files FROM games WHERE es=?", (es,)).fetchall()
    for r in rows:
        if r["rel"].replace("\\", "/") == rel:
            files = [{"path": f.replace("\\", "/"), "size": size} for f, size, _m in json.loads(r["files"])]
            deps = [{"path": p.replace("\\", "/"), "size": size} for p, _f, size in _arcade_deps(library.game(r["id"]))]
            return _json(handler, {"id": r["id"], "files": files, "deps": deps})
    return _json(handler, {"error": "nie ma takiej gry"}, 404)


def _savemgr_args(handler) -> dict:
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    return {k: (v or [""])[0] for k, v in q.items()}


def _savemgr_game(es: str, name: str) -> tuple[list, str]:
    """Nazwy plików i numer płyty gry z biblioteki serwera (gdy klient ich nie podał)."""
    from emustart import ingame, library, metadata, savemgr
    r = library.db().execute("SELECT id, rel FROM games WHERE es=? AND name=?", (es, name)).fetchone()
    if not r:
        return [name], ""
    try:
        serial = ingame._serial(metadata.get(r["id"]))
    except Exception:
        serial = ""
    stems = [Path(r["rel"].replace("\\", "/")).stem, name]
    if r["rel"].lower().endswith(".zip"):
        g = library.game(r["id"]) or {}
        stems += savemgr.zip_stems(Path(g.get("src") or "") / r["rel"])   # ROM w środku ZIP-a
    return stems, serial


def _savemgr_catalog(handler) -> None:
    """GET /v1/savemgr/catalog?profile=&es=&name=[&stems=a|b][&serial=][&learned=x|y]
    — stany tej gry z komputera i telefonu, ze zgodnością (emustart/savemgr.py)."""
    from emustart import nasfs, savemgr
    a = _savemgr_args(handler)
    if NAS_ROOT is None:
        return _json(handler, {"error": "serwer nie ma folderu profili"}, 404)
    try:
        prof = nasfs.clean_rel(a.get("profile", ""))
    except ValueError as ex:
        return _json(handler, {"error": str(ex)}, 400)
    if not prof or "/" in prof or not a.get("es") or not a.get("name"):
        return _json(handler, {"error": "brak profile/es/name"}, 400)
    if not (_scope_ok(handler, prof) and _profile_ok(handler, prof)):
        return _json(handler, {"error": "konto wymaga hasła", "pin": True}, 403)
    stems, serial = _savemgr_game(a["es"], a["name"])
    stems += [s for s in a.get("stems", "").split("|") if s]
    out = savemgr.catalog(nasfs.DirFs(NAS_ROOT), prof, a["es"], a["name"], stems,
                          a.get("serial") or serial, [p for p in a.get("learned", "").split("|") if p])
    return _json(handler, out)


def _savemgr_copy(handler) -> None:
    """PUT /v1/savemgr/copy?profile=&src=&platform=&emu=&es=&name=&device= — kopia stanu
    na drugą platformę (w folderze profili; urządzenie pobierze go przy następnej grze)."""
    from emustart import nasfs, savemgr
    n = int(handler.headers.get("Content-Length") or 0)
    if n:
        handler.rfile.read(min(n, 1 << 16))
    a = _savemgr_args(handler)
    if NAS_ROOT is None:
        return _json(handler, {"error": "serwer nie ma folderu profili"}, 404)
    try:
        prof = nasfs.clean_rel(a.get("profile", ""))
        src = nasfs.clean_rel(a.get("src", ""))
    except ValueError as ex:
        return _json(handler, {"error": str(ex)}, 400)
    if not prof or "/" in prof:
        return _json(handler, {"error": "brak profile"}, 400)
    if not (_scope_ok(handler, prof) and _profile_ok(handler, prof)):
        return _json(handler, {"error": "konto wymaga hasła", "pin": True}, 403)
    out = savemgr.copy(nasfs.DirFs(NAS_ROOT), prof, src, a.get("platform", ""), a.get("emu", ""),
                       a.get("es", ""), a.get("name", ""), a.get("device", ""))
    return _json(handler, out)


def _arcade_deps(g: dict) -> list:
    """Zależności gry arcade: [(nazwa pliku, ścieżka na serwerze, rozmiar)] — rodzic i BIOS
    z folderów systemu (launcher.py robi to samo na Windows)."""
    from emustart import arcade, library, systems
    if systems.info(g["es"])["kind"] != "arcade":
        return []
    out = []
    dirs = [library.game_dir(g)] + [d for d in library.system_dirs(g["es"]) if d != library.game_dir(g)]
    sub = Path(g["rel"].replace("\\", "/")).parent.as_posix()
    for dep in arcade.dependencies(Path(g["rel"]).stem):
        rel = f"{dep}.zip" if sub in ("", ".") else f"{sub}/{dep}.zip"     # obok gry
        for d in dirs:
            f = Path(d) / f"{dep}.zip"
            try:
                out.append((rel, f, f.stat().st_size))
                break
            except OSError:
                continue
    return out


_BIOS_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ ()+-]{0,79}$")


def _ra_roots(cfg: dict) -> list:
    from emustart import emulators
    out = []
    for sc in (cfg.get("systems") or {}).values():
        exe = (sc or {}).get("exe") or ""
        if exe and emulators.family(exe) == "retroarch" and Path(exe).parent not in out:
            out.append(Path(exe).parent)
    return out


def bios_dirs(cfg: dict) -> list:
    """Gdzie serwer ma BIOS-y: folder BIOS-ów z ustawień i foldery `system` RetroArchów
    wybranych dla systemów (tam EmuStart na komputerze kopiuje BIOS-y, Kickstarty itp.)."""
    from emustart import installer
    out = [Path(cfg["bios_dir"])] if cfg.get("bios_dir") else []
    for root in _ra_roots(cfg):
        d = installer.ra_system_dir(root)
        if d not in out:
            out.append(d)
    return out


def _bios_rel(rel: str) -> str:
    rel = rel.replace("\\", "/").strip("/")
    parts = rel.split("/")
    return rel if parts and all(_BIOS_PART.match(p) and p != ".." for p in parts) and len(parts) <= 4 else ""


def _bios_find(cfg: dict, rel: str):
    for d in bios_dirs(cfg):
        f = d / rel
        try:
            if f.is_file() and f.stat().st_size <= 64 * 1024 * 1024:
                return f
        except OSError:
            continue
    return None


def _bios_get(handler) -> None:
    """GET /v1/bios?core=swanstation — BIOS-y rdzenia (z pliku .info RetroArcha), które
    serwer ma: {"files": [ścieżka względem system]}; GET /v1/bios?name=<ścieżka> — plik.
    Telefon dokłada je do RetroArcha przed grą, jak EmuStart na Windows (bios.sync)."""
    from emustart import bios
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    folder = _bios_rel((q.get("dir") or [""])[0])
    if folder:
        # GET /v1/bios?dir=pcsx2/bios — pliki w folderze (emulatory samodzielne: PCSX2)
        files: list = []
        for d in bios_dirs(API._cfg):
            try:
                files += [f"{folder}/{f.name}" for f in sorted((d / folder).iterdir())
                          if f.is_file() and _bios_rel(f.name) and f.stat().st_size <= 64 * 1024 * 1024]
            except OSError:
                continue
        return _json(handler, {"files": list(dict.fromkeys(files))})
    core = (q.get("core") or [""])[0]
    if core:
        names = []
        for c in dict.fromkeys([core, re.sub(r"_(gles\d*|android)$", "", core)]):
            for root in _ra_roots(API._cfg):
                names += bios.firmware(root, c)
        files = [r for r in dict.fromkeys(names) if _bios_rel(r) and _bios_find(API._cfg, r)]
        return _json(handler, {"files": files})
    rel = _bios_rel((q.get("name") or [""])[0])
    f = _bios_find(API._cfg, rel) if rel else None
    if not f:
        return _json(handler, {"error": "nie ma takiego BIOS-u na serwerze"}, 404 if rel else 400)
    data = f.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


# ── synchronizacja zapisów gier (emustart/savesync.py) ──
def _sync_identity(handler, d: dict | None = None) -> tuple[str, str]:
    """(konto, urządzenie) zapytania — z tokenu sesji, nie z treści. Inne konto niż konto
    urządzenia tylko z tokenem tego konta (X-Profile-Token, gracz zalogowany hasłem)."""
    from emustart import accounts, devices, savesync
    did = getattr(handler, "device", "")
    want = str((d or {}).get("account")
               or urllib.parse.unquote(handler.headers.get("X-EmuStart-Account") or "")).strip()   # nazwa z polskimi znakami
    if not did:
        # wspólny klucz serwera (EmuStart na komputerze z serwerem) — konto podane wprost
        if not want:
            raise savesync.Denied("brak konta")
        return want, "serwer"
    own = devices.account_raw(did)
    if not want or want.lower() == own.lower():
        if not own:
            raise savesync.Denied("urządzenie nie jest przypisane do konta")
        return own, did
    if accounts.token_ok(_secret(), want, handler.headers.get("X-Profile-Token") or ""):
        return want, did
    raise savesync.Denied("brak dostępu do zapisów tego konta")


def _sync_errors(handler, fn) -> None:
    from emustart import savesync
    try:
        out = fn()
    except savesync.Conflict as ex:
        return _json(handler, {"ok": False, "conflict": True, "current": ex.current}, 409)
    except savesync.Denied as ex:
        return _json(handler, {"ok": False, "reason": str(ex)}, 403)
    except (savesync.SyncError, ValueError, TypeError) as ex:
        return _json(handler, {"ok": False, "reason": str(ex)}, 400)
    if out is not None:
        return _json(handler, out)


def savesync_get(handler, platform: str, game: str, urls: bool) -> None:
    from emustart import savesync

    def run():
        from emustart import devices
        acc, dev = _sync_identity(handler)
        out = savesync.manifest(acc, dev, platform, game, urls=urls)
        for _e, _c, _k, b in savesync._bundles(out):    # nazwa urządzenia do okna konfliktu
            did = b.get("updated_by_device_id") or ""
            b["updated_by_device_name"] = (devices.name_of(did) if did and did != "serwer" else did) or did
        return out
    return _sync_errors(handler, run)


def savesync_post(handler) -> None:
    """POST /v1/savesync/upload/init | upload/commit | <platforma>/<gra>/resolve."""
    from emustart import savesync
    if not authorized(handler):
        return _json(handler, {"error": "brak lub zły klucz serwera"}, 401)
    _seen(handler)
    n = int(handler.headers.get("Content-Length") or 0)
    try:
        d = json.loads(handler.rfile.read(min(n, 65536)) or b"{}") if n else {}
    except ValueError:
        d = {}
    d = d if isinstance(d, dict) else {}
    parts = [p for p in urllib.parse.urlsplit(handler.path).path.split("/") if p][2:]

    def run():
        acc, dev = _sync_identity(handler, d)
        if parts == ["upload", "init"]:
            return savesync.upload_init(acc, dev, d)
        if parts == ["upload", "commit"]:
            return savesync.upload_commit(acc, dev, d)
        if len(parts) == 3 and parts[2] == "resolve":
            return savesync.resolve(acc, dev, parts[0], parts[1], d)
        raise savesync.SyncError("nieznany adres")
    return _sync_errors(handler, run)


def files_put(handler) -> None:
    """PUT /v1/files/u/<token> — treść paczki (adres z upload/init)."""
    from emustart import savesync
    token = urllib.parse.urlsplit(handler.path).path.rsplit("/", 1)[-1]
    n = int(handler.headers.get("Content-Length") or 0)
    return _sync_errors(handler, lambda: savesync.upload_body(token, handler.rfile, n))


def files_get(handler) -> None:
    """GET /v1/files/d/<token> — paczka (adres z manifestu), z Range."""
    from emustart import savesync
    token = urllib.parse.urlsplit(handler.path).path.rsplit("/", 1)[-1]
    hit = savesync.download_path(token)
    if not hit:
        return _json(handler, {"ok": False, "reason": "adres pobrania nieważny albo wygasł"}, 403)
    return _send_path(handler, hit[0])


def _emulators_get(handler, parts: list) -> None:
    """GET /v1/emulators — wersje emulatorów w magazynie serwera i co potrzebuje który system;
    GET /v1/emulators/manifest?item=&version= — pliki wersji z rozmiarem i sumą SHA-256;
    GET /v1/emulators/archive?item=&version= — cała wersja w jednym ZIP-ie (Range: wznawianie)."""
    from emustart import emucatalog, emustore
    if not parts:
        return _json(handler, emustore.public_index(API._cfg))
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    item, version = (q.get("item") or [""])[0], (q.get("version") or [""])[0]
    if parts == ["manifest"] and emucatalog.valid_item(item):
        m = emustore.manifest(API._cfg, item, version)
        if m:
            return _json(handler, m)
    if parts == ["archive"] and emucatalog.valid_item(item):
        p = emustore.archive_path(API._cfg, item, version)
        if p:
            return _send_path(handler, p)
    return _json(handler, {"error": "nie ma takiej wersji emulatora"}, 404)


def _send_file(handler, gid: int, rel: str) -> None:
    from emustart import library
    g = library.game(gid)
    if not g:
        return _json(handler, {"error": "nie ma takiej gry"}, 404)
    files = {r.replace("\\", "/"): size for r, size, _m in g["files"]}
    rel = rel.replace("\\", "/")
    deps = {r: p for r, p, _s in _arcade_deps(g)}
    if rel not in files and rel not in deps:  # tylko pliki tej gry (i jej zależności arcade)
        return _json(handler, {"error": "nie ma takiego pliku"}, 404)
    base = Path(g.get("src") or "")
    if rel in deps:
        path = deps[rel]
    else:
        path = base / rel                       # „/” działa i na Windows
    from emustart import srvadmin
    ip = handler.client_address[0]
    _send_path(handler, path, lambda n: srvadmin.sent(ip, gid, g.get("title") or rel, n))


def _send_path(handler, path: Path, on_chunk=None) -> None:
    """Plik z obsługą Range (wznawianie pobierania): gry, archiwa emulatorów."""
    try:
        size = path.stat().st_size
    except OSError:
        return _json(handler, {"error": "plik niedostępny"}, 404)
    start, end = 0, size - 1
    rng = handler.headers.get("Range") or ""
    m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
    partial = bool(m)
    if m:
        if m.group(1):
            start = int(m.group(1))
            if m.group(2):
                end = min(int(m.group(2)), size - 1)
        elif m.group(2):                      # ostatnie N bajtów
            start = max(0, size - int(m.group(2)))
        if start > end or start >= size:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{size}")
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
    length = end - start + 1
    handler.send_response(206 if partial else 200)
    handler.send_header("Content-Type", "application/octet-stream")
    handler.send_header("Content-Length", str(length))
    handler.send_header("Accept-Ranges", "bytes")
    if partial:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with open(path, "rb") as f:
        f.seek(start)
        left = length
        while left > 0:
            buf = f.read(min(CHUNK, left))
            if not buf:                       # plik się skrócił — odpowiedź niepełna, zamknij
                handler.close_connection = True
                break
            handler.wfile.write(buf)
            left -= len(buf)
            if on_chunk:
                on_chunk(len(buf))


FW_TLS = "EmuStart Server (szyfrowany)"


def ensure_firewall(port: int, name: str = "") -> None:
    """Reguła zapory dla portu szyfrowanego — serwer działa jako SYSTEM (zadanie Harmonogramu),
    więc dopisuje ją sam przy starcie; bez uprawnień nic się nie dzieje (wpis w logu)."""
    if os.name != "nt":
        return
    import subprocess
    name = name or FW_TLS
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={name}"],
                           capture_output=True, text=True, creationflags=flags, timeout=20)
        if r.returncode == 0 and f"{port}" in r.stdout:
            return
        if r.returncode == 0:                          # reguła na inny port — od nowa
            subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={name}"],
                           capture_output=True, creationflags=flags, timeout=20)
        r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={name}", "dir=in",
                            "action=allow", "protocol=TCP", f"localport={port}"],
                           capture_output=True, text=True, creationflags=flags, timeout=20)
        if r.returncode == 0:
            log.info("zapora: dopuszczony port %d (%s)", port, name)
        else:
            log.warning("zapora: nie udało się dopuścić portu %d (%s) — uruchom serwer jako administrator "
                        "albo dodaj regułę ręcznie", port, (r.stdout + r.stderr).strip()[:200])
    except (OSError, subprocess.SubprocessError) as ex:
        log.warning("zapora: %s", ex)


def install_autostart(exe: str, port: int) -> list:
    """Zadanie Harmonogramu (przy starcie systemu) i reguła zapory — uruchamiane
    przez użytkownika na komputerze z grami (wymaga uprawnień administratora)."""
    import subprocess
    app = str(Path(exe).parent)
    cmds = [
        # folder programu: zapis tylko SYSTEM (serwer) i administratorzy, zwykli użytkownicy —
        # odczyt. Bez tego każdy zalogowany użytkownik mógł podmienić DLL w _internal, a serwer
        # (konto SYSTEM) wykonałby ją przy starcie; także dane serwera (konta, profile, klucze).
        # SID-y zamiast nazw grup (nazwy zależą od języka Windows).
        ["icacls", app, "/inheritance:r", "/grant:r", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
         "*S-1-5-32-545:(OI)(CI)RX"],
        ["schtasks", "/Create", "/F", "/TN", "EmuStart Server", "/SC", "ONSTART", "/RU", "SYSTEM",
         "/RL", "HIGHEST", "/TR", f'"{exe}" --service'],
        # schtasks zakłada limit 72 h pracy zadania — serwer działa bez końca;
        # po awarii Harmonogram uruchamia go ponownie (3 razy co minutę)
        ["powershell", "-NoProfile", "-Command",
         "Set-ScheduledTask -TaskName 'EmuStart Server' -Settings (New-ScheduledTaskSettingsSet "
         "-ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) "
         "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries)"],
        # skrót do panelu serwera w menu Start (wszyscy użytkownicy)
        ["powershell", "-NoProfile", "-Command",
         "$s=(New-Object -ComObject WScript.Shell).CreateShortcut("
         "\"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\EmuStart Server.lnk\");"
         f"$s.TargetPath='{exe}';$s.Arguments='--panel';$s.IconLocation='{exe},0';$s.Save()"],
        ["netsh", "advfirewall", "firewall", "add", "rule", "name=EmuStart Server", "dir=in",
         "action=allow", "protocol=TCP", f"localport={port}"],
        ["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_TLS}", "dir=in",
         "action=allow", "protocol=TCP", f"localport={port + 1}"],
    ]
    out = []
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        out.append((" ".join(c[:2]), r.returncode, (r.stdout + r.stderr).strip()))
    return out
