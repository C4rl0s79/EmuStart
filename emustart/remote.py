"""remote — API dla EmuStart na Androidzie (tryb serwera: `EmuStart.exe --server`).

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

Każde zapytanie wymaga klucza serwera: nagłówek `Authorization: Bearer <klucz>`
albo parametr `?k=<klucz>` (dla <img> w WebView). Klucz powstaje przy pierwszym
starcie serwera (config.json → server_token); `--server-key` go wypisuje.
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
    """Klucz urządzenia (sparowanego kodem QR, emustart/devices.py) albo — dla urządzeń
    sprzed parowania — wspólny klucz serwera. handler.device = id urządzenia ('' = wspólny)."""
    if API is None:
        return False
    got = ""
    auth = handler.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        got = auth[7:].strip()
    else:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
        got = (q.get("k") or [""])[0]
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
    if did and devices.is_legacy(did):
        # stary klucz-hasło: wyłącznie przejście na klucz urządzenia (i sprawdzenie serwera)
        path = urllib.parse.urlsplit(handler.path).path
        if path not in ("/v1/auth/upgrade", "/v1/info"):
            return False
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


def pair(handler) -> None:
    """POST /v1/pair {code, name, class, version} → klucz urządzenia (bez klucza — jednorazowy kod)."""
    from emustart import devices
    n = int(handler.headers.get("Content-Length") or 0)
    try:
        d = json.loads(handler.rfile.read(min(n, 4096)) or b"{}") if n else {}
    except ValueError:
        d = {}
    r = devices.pair(str(d.get("code", "")), str(d.get("name", "")), str(d.get("class", "")),
                     handler.client_address[0], str(d.get("version", "")), str(d.get("hw", "")),
                     str(d.get("pubkey", "")))
    if r.get("ok"):
        r.update(server=socket.gethostname(), **_trust())
    return _json(handler, r, 200 if r.get("ok") else 403)


def _trust() -> dict:
    """Do odpowiedzi parowania: adresy serwera i odcisk certyfikatu (przypinany w kliencie)."""
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
    """Adresy, pod którymi klienci mogą dosięgnąć serwer (klient wybiera najlepszy):
    sieć lokalna, Tailscale — zwykły port i szyfrowany. Port publiczny: etap 0.35."""
    from emustart import srvadmin
    out = []
    for a in srvadmin._addresses():
        k = kind_of(a)
        out.append({"url": f"http://{a}:{PORT}", "kind": k, "tls": False})
        if TLS:
            out.append({"url": f"https://{a}:{TLS['port']}", "kind": k, "tls": True})
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


def pair_flow(handler, op: str) -> None:
    """Prośba o dołączenie zatwierdzana na sparowanym urządzeniu (emustart/devices.py):
    POST /v1/pair/request {name, class, version}  — nowe urządzenie, bez klucza
    POST /v1/pair/poll {rid}                       — nowe urządzenie odbiera klucz po zatwierdzeniu
    POST /v1/pair/approve {code, allow}            — sparowane urządzenie (z kluczem);
                                                     allow pominięte = tylko podgląd prośby"""
    from emustart import devices
    d = _body(handler)
    if op == "request":
        r = devices.request(str(d.get("name", "")), str(d.get("class", "")),
                            handler.client_address[0], str(d.get("version", "")),
                            str(d.get("pubkey", "")), str(d.get("hw", "")))
        if r.get("ok"):
            r["server"] = socket.gethostname()
        return _json(handler, r, 200 if r.get("ok") else 429)
    if op == "poll":
        r = devices.poll(str(d.get("rid", "")))
        if r.get("status") == "approved":
            r.update(server=socket.gethostname(), **_trust())
        return _json(handler, r)
    if op == "approve":
        if not authorized(handler):
            return _json(handler, {"ok": False, "reason": "Zatwierdzać może tylko sparowane urządzenie."}, 401)
        code = str(d.get("code", ""))
        if d.get("allow") is None:
            p = devices.pending(code)
            return _json(handler, {"ok": True, "request": p} if p else
                         {"ok": False, "reason": "Nie ma takiej prośby (kod wygasł albo już użyty)."})
        by = devices.name_of(handler.device) if getattr(handler, "device", "") else "urządzenie ze wspólnym kluczem"
        return _json(handler, devices.approve(code, bool(d.get("allow")), by))
    return _json(handler, {"ok": False, "reason": "nieznana operacja"}, 404)


def enroll_flow(handler, op: str) -> None:
    """Nowe instalacje i logowanie podpisem (emustart/enroll.py):
    POST /v1/enroll/request {mode, account, password, name, class, version, pubkey} — bez klucza
    POST /v1/enroll/poll {rid}                                               — bez klucza
    POST /v1/auth/challenge {device}, /v1/auth/token {device, nonce, sig}    — bez klucza
    POST /v1/enroll/pending, /v1/enroll/decide {id, allow, games}            — z kluczem"""
    from emustart import enroll, public
    d = _body(handler)
    ip = public.client_ip(handler)
    if op == "request":
        r = enroll.request(str(d.get("mode", "")), str(d.get("account", "")), str(d.get("name", "")),
                           str(d.get("class", "")), ip, str(d.get("version", "")), str(d.get("pubkey", "")),
                           profile_names(), str(d.get("hw", "")), str(d.get("password", "")))
        return _json(handler, r, 200 if r.get("ok") else 400)
    if op == "poll":
        return _json(handler, enroll.poll(str(d.get("rid", ""))))
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
    if op == "upgrade":                     # stary klucz-hasło → klucz urządzenia (ten sam wpis)
        from emustart import devices
        if not viewer or not devices.is_legacy(viewer):
            return _json(handler, {"ok": False, "reason": "to urządzenie nie ma starego klucza"}, 400)
        return _json(handler, devices.upgrade(viewer, str(d.get("pubkey", "")), str(d.get("hw", ""))))
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
    fs = nasfs.SmbFs(NAS_ROOT)
    if not fs.exists(f"{name}/emustart.json"):
        fs.write(f"{name}/emustart.json", b"{}")
    log.info("nowe konto %s", name)


def _seen(handler) -> None:
    """Klient z kluczem — do listy urządzeń w panelu serwera."""
    from emustart import srvadmin
    srvadmin.seen(handler.client_address[0])


def _json(handler, data, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


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
            feats = ["find", "system", "bios", "media", "library"] + (["nas", "roots", "savemgr", "profiles"] if NAS_ROOT else [])
            return _json(handler, {"name": socket.gethostname(), "version": __version__, "api": 1,
                                   "features": feats, "nas_id": NAS_ID, "endpoints": endpoints(),
                                   "tls": {"port": TLS["port"], "fp": TLS["fp"]} if TLS else None})
        if parts and parts[0] == "nas":
            return _nas(handler, "GET", parts[1:])
        if parts == ["library"]:                       # biblioteka dla komputera bez folderów z grami
            if not _games_ok(handler):
                return _json(handler, {"error": "To konto nie pobiera gier z serwera."}, 403)
            from emustart import libsync
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
            es = (q.get("es") or [""])[0]
            if es:
                return _json(handler, {"games": libsync.server_games(es)})
            return _json(handler, {"systems": libsync.server_systems()})
        if parts == ["sysinfo"]:                       # opis platformy (producent, lata, opis) dla telefonu
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
            return _json(handler, API.system_info((q.get("es") or [""])[0]))
        if parts == ["find"]:
            if not _games_ok(handler):
                return _json(handler, {"error": "To konto nie pobiera gier z serwera."}, 403)
            return _find(handler)
        if parts == ["media", "have"]:                 # grafiki gier, które serwer ma (artsync)
            from emustart import artsync
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
            have = artsync.server_have((q.get("es") or [""])[0])
            return _json(handler, {"files": have} if have is not None else {"error": "nieznany system"},
                         200 if have is not None else 404)
        if parts == ["savemgr", "catalog"]:
            return _savemgr_catalog(handler)
        if parts == ["profiles"]:
            return _profiles_get(handler)
        if parts == ["bios"]:
            return _bios_get(handler)
        if parts == ["systems"]:
            from emustart import emulators, installer, systems
            out = []
            for s_ in API.list_systems():
                d = {k: s_.get(k) for k in ("es", "display", "games", "logo", "logo_glow", "kind")}
                plat = systems.info(s_["es"])["plat"]
                d["plat"] = plat
                sc = (API._cfg.get("systems") or {}).get(s_["es"]) or {}
                # rdzeń RetroArcha (Android: *_libretro_android.so) — ten, którego używa komputer, gdy go zgłosił
                d["core"] = sc.get("core_rec") or installer.CORES.get(plat, "")
                d["emu_rec"] = sc.get("emu_rec", "")
                d["cores"] = emulators.cores_for(plat)         # wszystkie pasujące — wybór na telefonie
                out.append(d)
            return _json(handler, out)
        if len(parts) == 3 and parts[0] == "systems" and parts[2] == "games":
            return _json(handler, [_game_row(r) for r in API.list_games(parts[1])])
        if len(parts) == 2 and parts[0] == "games" and parts[1].isdigit():
            d = API.game_detail(int(parts[1]), use_variant=False)
            if not d:
                return _json(handler, {"error": "nie ma takiej gry"}, 404)
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
    except Exception as ex:
        log.exception("API zdalne %s", path)
        try:
            _json(handler, {"error": str(ex)}, 500)
        except OSError:
            pass


def find_nas_root(cfg: dict) -> Path | None:
    r"""Folder profili na komputerze z serwerem: `profiles_nas` z ustawień, a gdy go tu
    nie ma — `emustart\Profiles` w którymś folderze nadrzędnym folderów z grami
    (klienci widzą go przez udział, np. Z:\emustart\Profiles = <udział>\emustart\Profiles)."""
    own = cfg.get("profiles_nas") or ""
    if own and Path(own).is_dir():
        return Path(own)
    from emustart import paths
    if (paths.SERVER / "profiles" / ".emustart-root").is_file():
        return paths.SERVER / "profiles"          # 2.0: profile w katalogu serwera
    for r in config.rom_roots(cfg):
        for d in [Path(r), *Path(r).parents]:
            cand = d / "emustart" / "Profiles"
            try:
                if cand.is_dir():
                    return cand
            except OSError:
                continue
    return None


def init_nas(cfg: dict) -> None:
    """Przy starcie serwera: folder profili i jego znacznik (klient sprawdza, że przez
    SMB widzi ten sam folder)."""
    global NAS_ROOT, NAS_ID
    from emustart import nasfs
    NAS_ROOT = find_nas_root(cfg)
    NAS_ID = nasfs.root_id(NAS_ROOT, create=True) if NAS_ROOT else ""
    log.info("folder profili serwera: %s", NAS_ROOT or "nie znaleziono (zapisy przez SMB)")


def handle_write(handler, method: str) -> None:
    """PUT/DELETE /v1/nas/… (zapis w folderze profili)."""
    if not authorized(handler):
        return _json(handler, {"error": "brak lub zły klucz serwera"}, 401)
    _seen(handler)
    path = urllib.parse.unquote(urllib.parse.urlsplit(handler.path).path)
    parts = [p for p in path.split("/") if p][1:]
    if method == "PUT" and parts in (["system"], ["roots"], ["media"]) and not _admin(handler):
        n = int(handler.headers.get("Content-Length") or 0)
        if n:
            handler.rfile.read(min(n, 10 << 20))
        return _json(handler, {"error": "Tylko urządzenie administratora."}, 403)
    if method == "PUT" and len(parts) == 2 and parts[0] == "profiles":
        return _profiles_put(handler, parts[1])
    if method == "PUT" and parts == ["system"]:
        return _system_put(handler)
    if method == "PUT" and parts == ["roots"]:
        return _roots_put(handler)
    if method == "PUT" and parts == ["media"]:
        return _media_put(handler)
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
    except Exception as ex:
        log.exception("API zdalne %s %s", method, path)
        handler.close_connection = True
        try:
            _json(handler, {"error": str(ex)}, 500)
        except OSError:
            pass


def _roots_put(handler) -> None:
    """Foldery z grami z EmuStart na Windows (względem udziału na NAS-ie, np. „WHDLoad”):
    brakujące w ustawieniach serwera dopisujemy i skanujemy — telefon widzi to samo co PC.
    Udział = folder nad emustart\\Profiles (ten sam, który klienci mają np. jako Z:)."""
    from emustart import nasfs
    n = int(handler.headers.get("Content-Length") or 0)
    if n > 65536:
        handler.close_connection = True
        return _json(handler, {"error": "za dużo danych"}, 413)
    try:
        rels = json.loads(handler.rfile.read(n) or b"{}").get("roots") or []
    except ValueError:
        return _json(handler, {"error": "zły JSON"}, 400)
    if NAS_ROOT is None:
        return _json(handler, {"error": "serwer nie zna swojego udziału (brak folderu profili)"}, 404)
    share = NAS_ROOT.parent.parent
    cfg = API._cfg
    have = {os.path.normcase(os.path.normpath(r)) for r in config.rom_roots(cfg)}
    added, missing = [], []
    for rel in rels[:64]:
        try:
            r = nasfs.clean_rel(str(rel))
        except ValueError:
            continue
        d = share / r if r else share
        if not d.is_dir():
            missing.append(r)
            continue
        key = os.path.normcase(os.path.normpath(str(d)))
        if key not in have:
            have.add(key)
            added.append(str(d))
    if added:
        cfg["rom_roots"] = config.rom_roots(cfg) + added
        cfg["rom_root"] = cfg["rom_roots"][0]
        config.save(cfg)
        log.info("foldery z grami od klienta: %s — skanowanie", ", ".join(added))
        try:
            API.rescan()
        except Exception:
            log.exception("skanowanie nowych folderów")
    return _json(handler, {"ok": True, "added": added, "missing": missing})


def _media_put(handler) -> None:
    """Grafika gry z EmuStart na Windows (artsync) — logo, okładka, zrzut."""
    from emustart import artsync
    n = int(handler.headers.get("Content-Length") or 0)
    if n > artsync.MAX_FILE:
        handler.close_connection = True
        return _json(handler, {"error": "za duży plik"}, 413)
    body = handler.rfile.read(n) if n else b""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    code, msg = artsync.server_put((q.get("es") or [""])[0], (q.get("file") or [""])[0], body)
    return _json(handler, {"ok": code == 200} if code == 200 else {"error": msg}, code)


def _system_put(handler) -> None:
    """Wygląd systemu z EmuStart na Windows: logo (plik w treści), poświata, nazwa —
    serwer pokazuje je potem aplikacji na Androida tak jak na komputerze."""
    from emustart import logos
    n = int(handler.headers.get("Content-Length") or 0)
    if n > 5 * 1024 * 1024:
        handler.close_connection = True
        return _json(handler, {"error": "za duże logo"}, 413)
    body = handler.rfile.read(n) if n else b""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    arg = lambda k: (q.get(k) or [""])[0]      # noqa: E731
    es, logo, ext = arg("es"), arg("logo"), arg("ext").lower()
    if not re.fullmatch(r"[a-z0-9_]{1,40}", es) or logo not in ("custom", "none", "default", "keep"):
        return _json(handler, {"error": "zły system albo logo"}, 400)
    if logo == "custom" and (ext not in ("svg", "png", "jpg", "webp") or not body):
        return _json(handler, {"error": "zły plik logo"}, 400)
    cfg = API._cfg
    sc = cfg.setdefault("systems", {}).setdefault(es, {})
    if logo == "custom":
        logos._store_custom(es, body, ext)
        sc["logo"] = "custom"
    elif logo == "none":
        sc["logo"] = "none"
    elif logo == "default":
        sc["logo"] = ""
    if arg("glow") in ("0", "1"):
        sc["logo_glow"] = arg("glow") == "1"
    # emulator i rdzeń, których używa komputer — zalecane dla telefonów (stany zgodne)
    if "emu" in q and arg("emu") in ("", "retroarch", "standalone"):
        sc["emu_rec"] = arg("emu")
        core = arg("core")
        sc["core_rec"] = core if re.fullmatch(r"[a-z0-9_+.-]{0,40}", core) else ""
    if "name" in q:
        name = arg("name").strip()[:60]
        if name:
            sc["name"] = name
        else:
            sc.pop("name", None)
    config.save(cfg)
    return _json(handler, {"ok": True})


def _nas(handler, method: str, parts: list) -> None:
    from emustart import nasfs, pinauth
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
            ents = [[e.name, e.is_dir()] for e in os.scandir(target) if e.name != pinauth.AUTH
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
    return rel.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] == pinauth.AUTH


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
    from emustart import accounts
    sc = _scope(handler)
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
    fs = nasfs.SmbFs(NAS_ROOT)
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
        return _json(handler, {"error": f"Nie ma konta „{wanted}”."}, 404)
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
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")[:32]
    return "" if not n or n.startswith(("_", ".")) else n


def _find(handler) -> None:
    """Gra po (system, ścieżka względna) — EmuStart na Windows pobiera ją stąd zamiast
    przez SMB. Odpowiedź: id i pliki (ścieżka, rozmiar); pobieranie jak dla Androida."""
    from emustart import library
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
    es, rel = (q.get("es") or [""])[0], (q.get("rel") or [""])[0].replace("\\", "/")
    if not es or not rel:
        return _json(handler, {"error": "brak es/rel"}, 400)
    rows = library.db().execute("SELECT id, rel, files FROM games WHERE es=?", (es,)).fetchall()
    for r in rows:
        if r["rel"].replace("\\", "/") == rel:
            files = [{"path": f.replace("\\", "/"), "size": size} for f, size, _m in json.loads(r["files"])]
            return _json(handler, {"id": r["id"], "files": files})
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
    out = savemgr.catalog(nasfs.SmbFs(NAS_ROOT), prof, a["es"], a["name"], stems,
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
    out = savemgr.copy(nasfs.SmbFs(NAS_ROOT), prof, src, a.get("platform", ""), a.get("emu", ""),
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
    from emustart import srvadmin
    ip = handler.client_address[0]
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
            srvadmin.sent(ip, gid, g.get("title") or rel, len(buf))


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
    cmds = [
        ["schtasks", "/Create", "/F", "/TN", "EmuStart Server", "/SC", "ONSTART", "/RU", "SYSTEM",
         "/RL", "HIGHEST", "/TR", f'"{exe}" --server'],
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
         f"$s.TargetPath='{exe}';$s.Arguments='--server-panel';$s.IconLocation='{exe},0';$s.Save()"],
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
