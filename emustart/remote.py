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
            return _json(handler, {"name": socket.gethostname(), "version": __version__, "api": 1})
        if parts == ["systems"]:
            from emustart import installer, systems
            out = []
            for s_ in API.list_systems():
                d = {k: s_.get(k) for k in ("es", "display", "games", "logo", "kind")}
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
            if not buf:
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
