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
CHUNK = 1024 * 1024


def ensure_token(cfg: dict) -> str:
    if not cfg.get("server_token"):
        cfg["server_token"] = secrets.token_urlsafe(24)
        config.save(cfg)
    return cfg["server_token"]


def authorized(handler) -> bool:
    if API is None:
        return False
    token = API._cfg.get("server_token") or ""
    if not token:
        return False
    got = ""
    auth = handler.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        got = auth[7:].strip()
    else:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
        got = (q.get("k") or [""])[0]
    return bool(got) and hmac.compare_digest(got.encode(), token.encode())


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
    path = urllib.parse.unquote(urllib.parse.urlsplit(handler.path).path)
    parts = [p for p in path.split("/") if p][1:]           # bez „v1”
    try:
        if parts == ["info"]:
            feats = ["find", "system"] + (["nas"] if NAS_ROOT else [])
            return _json(handler, {"name": socket.gethostname(), "version": __version__, "api": 1,
                                   "features": feats, "nas_id": NAS_ID})
        if parts and parts[0] == "nas":
            return _nas(handler, "GET", parts[1:])
        if parts == ["find"]:
            return _find(handler)
        if parts == ["systems"]:
            from emustart import installer, systems
            out = []
            for s_ in API.list_systems():
                d = {k: s_.get(k) for k in ("es", "display", "games", "logo", "logo_glow", "kind")}
                plat = systems.info(s_["es"])["plat"]
                d["plat"] = plat
                d["core"] = installer.CORES.get(plat, "")     # rdzeń RetroArcha (Android: *_libretro_android.so)
                out.append(d)
            return _json(handler, out)
        if len(parts) == 3 and parts[0] == "systems" and parts[2] == "games":
            return _json(handler, [_game_row(r) for r in API.list_games(parts[1])])
        if len(parts) == 2 and parts[0] == "games" and parts[1].isdigit():
            d = API.game_detail(int(parts[1]))
            if not d:
                return _json(handler, {"error": "nie ma takiej gry"}, 404)
            from emustart import library
            g = library.game(int(parts[1]))
            d["files"] = [{"path": rel, "size": size} for rel, size, _m in g["files"]]
            d["es"] = g["es"]
            return _json(handler, d)
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
    path = urllib.parse.unquote(urllib.parse.urlsplit(handler.path).path)
    parts = [p for p in path.split("/") if p][1:]
    if method == "PUT" and parts == ["system"]:
        return _system_put(handler)
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
    if "name" in q:
        name = arg("name").strip()[:60]
        if name:
            sc["name"] = name
        else:
            sc.pop("name", None)
    config.save(cfg)
    return _json(handler, {"ok": True})


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
    if method == "GET" and op == "scan":
        return _json(handler, {"files": {k: list(v) for k, v in nasfs.scan_dir(target).items()}})
    if method == "GET" and op == "list":
        try:
            ents = [[e.name, e.is_dir()] for e in os.scandir(target)]
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
        if backup:
            brel = nasfs.clean_rel(backup)
            nasfs.backup_copy(target, NAS_ROOT / brel)
            broot = nasfs.backup_root(brel)
            if broot:
                nasfs.prune_backups(NAS_ROOT / broot)
        mtime = float(arg("mtime") or 0) or _time.time()
        nasfs.write_atomic(target, body, mtime)
        return _json(handler, {"ok": True, "size": len(body)})
    if method == "DELETE" and op == "file":
        if rel and rel != nasfs.ROOT_MARK and target.is_file():
            target.unlink()
        return _json(handler, {"ok": True})
    return _json(handler, {"error": "nieznana operacja"}, 404)


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
            files = [{"path": f, "size": size} for f, size, _m in json.loads(r["files"])]
            return _json(handler, {"id": r["id"], "files": files})
    return _json(handler, {"error": "nie ma takiej gry"}, 404)


def _send_file(handler, gid: int, rel: str) -> None:
    from emustart import library
    g = library.game(gid)
    if not g:
        return _json(handler, {"error": "nie ma takiej gry"}, 404)
    files = {r.replace("\\", "/"): size for r, size, _m in g["files"]}
    rel = rel.replace("\\", "/")
    if rel not in files:                      # tylko pliki tej gry — bez dowolnych ścieżek
        return _json(handler, {"error": "nie ma takiego pliku"}, 404)
    base = Path(g.get("src") or "")
    path = base / g["rel"] if rel == g["rel"].replace("\\", "/") and not g.get("is_dir") else base / rel
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


def install_autostart(exe: str, port: int) -> list:
    """Zadanie Harmonogramu (przy starcie systemu) i reguła zapory — uruchamiane
    przez użytkownika na komputerze z grami (wymaga uprawnień administratora)."""
    import subprocess
    cmds = [
        ["schtasks", "/Create", "/F", "/TN", "EmuStart Server", "/SC", "ONSTART", "/RU", "SYSTEM",
         "/RL", "HIGHEST", "/TR", f'"{exe}" --server'],
        ["netsh", "advfirewall", "firewall", "add", "rule", "name=EmuStart Server", "dir=in",
         "action=allow", "protocol=TCP", f"localport={port}"],
    ]
    out = []
    for c in cmds:
        r = subprocess.run(c, capture_output=True, text=True)
        out.append((" ".join(c[:2]), r.returncode, (r.stdout + r.stderr).strip()))
    return out
