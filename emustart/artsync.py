"""artsync — grafiki gier z komputera na serwer (aplikacja na Androida pokazuje to, co ma serwer).

Logo gier (i część okładek / zrzutów) komputer pobiera sam — na żądanie interfejsu albo
w zadaniu „Grafiki i metadane”. Serwer ich wtedy nie ma, więc telefon nie miał czego pokazać.

- `push_missing(cfg)` — w tle po starcie: dla każdego systemu serwer podaje listę grafik,
  które ma (GET /v1/media/have), komputer wysyła tylko brakujące (PUT /v1/media).
- `push_one(cfg, es, name, kind)` — grafika wybrana ręcznie (opcje gry) zastępuje tę na
  serwerze.

Serwer zapisuje plik w swoim folderze grafik i oznacza grę jako mającą tę grafikę.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import urllib.parse

from emustart import paths

log = logging.getLogger("emustart.artsync")

KINDS = ("logo", "box", "snap")
MAX_FILE = 10 * 1024 * 1024
_running = threading.Lock()


def _local(es: str) -> dict:
    """{nazwa pliku: rozmiar} grafik systemu na tym komputerze."""
    d = paths.MEDIA / es
    out = {}
    if d.is_dir():
        for f in d.iterdir():
            if f.name.endswith(tuple(f".{k}.png" for k in KINDS)):
                try:
                    out[f.name] = f.stat().st_size
                except OSError:
                    pass
    return out


def _put(c, key: str, es: str, fname: str) -> bool:
    from emustart import netsrc
    f = paths.MEDIA / es / fname
    data = f.read_bytes()
    if not data or len(data) > MAX_FILE:
        return False
    q = urllib.parse.urlencode({"es": es, "file": fname})
    c.request("PUT", "/v1/media?" + q, body=data,
              headers={**netsrc.auth_headers(key), "Content-Type": "image/png"})
    r = c.getresponse()
    r.read()
    if r.status != 200:
        log.info("grafika %s/%s na serwer: %s", es, fname, r.status)
    return r.status == 200


def push_missing(cfg: dict) -> int:
    """Grafiki, których serwer nie ma → serwer. Zwraca liczbę wysłanych."""
    from emustart import netsrc
    ep = netsrc.endpoint(cfg)
    if not ep:
        return 0
    info = netsrc.info(cfg)
    if not info.get("ok") or "media" not in info.get("features", []):
        return 0
    if not _running.acquire(blocking=False):
        return 0
    host, port, key = ep
    sent = 0
    try:
        systems = sorted(p.name for p in paths.MEDIA.iterdir() if p.is_dir() and not p.name.startswith("_")) \
            if paths.MEDIA.is_dir() else []
        c = netsrc.conn(host, port, 60)
        for es in systems:
            local = _local(es)
            if not local:
                continue
            c.request("GET", "/v1/media/have?" + urllib.parse.urlencode({"es": es}), headers=netsrc.auth_headers(key))
            r = c.getresponse()
            body = r.read()
            if r.status == 404:
                continue                                 # serwer nie zna tego systemu
            if r.status != 200:
                log.info("grafiki %s: serwer odpowiedział %s", es, r.status)
                continue
            have = set(json.loads(body).get("files") or [])
            missing = sorted(n for n in local if n not in have)
            n = 0
            for fname in missing:
                if _put(c, key, es, fname):
                    n += 1
            if n:
                log.info("grafiki %s → serwer: %d", es, n)
            sent += n
        c.close()
    except (OSError, http.client.HTTPException, ValueError) as ex:
        log.info("grafiki na serwer: %s", ex)
    finally:
        _running.release()
    if sent:
        log.info("grafiki gier wysłane na serwer: %d", sent)
    return sent


def push_one(cfg: dict, es: str, name: str, kind: str) -> bool:
    """Grafika wybrana ręcznie — zastępuje tę na serwerze."""
    from emustart import art, netsrc
    ep = netsrc.endpoint(cfg)
    if not ep or kind not in KINDS:
        return False
    f = art.media_path(es, name, kind)
    if not f.is_file():
        return False
    try:
        c = netsrc.conn(ep[0], ep[1], 60)
        try:
            return _put(c, ep[2], es, f.name)
        finally:
            c.close()
    except (OSError, http.client.HTTPException) as ex:
        log.info("grafika %s na serwer: %s", f.name, ex)
        return False


def push_later(cfg: dict, es: str = "", name: str = "", kind: str = "") -> None:
    if es:
        target = lambda: push_one(cfg, es, name, kind)      # noqa: E731
    else:
        target = lambda: push_missing(cfg)                  # noqa: E731
    threading.Thread(target=target, daemon=True, name="grafiki-serwer").start()


# ── po stronie serwera ──
SERVER_MEDIA = None          # folder grafik serwera (None = paths.MEDIA; testy: osobny)
_names: dict = {}            # es → (liczba gier, {nazwa pliku bez rodzaju: [id gier]})


def _games_by_thumb(es: str) -> dict:
    from emustart import library, systems
    con = library.db()
    rows = con.execute("SELECT id, name FROM games WHERE es=?", (es,)).fetchall()
    hit = _names.get(es)
    if hit and hit[0] == len(rows):
        return hit[1]
    m: dict = {}
    for r in rows:
        m.setdefault(systems.thumb_name(r["name"]), []).append(r["id"])
    _names[es] = (len(rows), m)
    return m


def server_have(es: str) -> list | None:
    """Pliki grafik systemu na serwerze; None = nieznany system."""
    if not _valid_es(es):
        return None
    d = (SERVER_MEDIA or paths.MEDIA) / es
    if not d.is_dir() and not _games_by_thumb(es):
        return None
    return sorted(f.name for f in d.iterdir() if f.name.endswith(tuple(f".{k}.png" for k in KINDS))) \
        if d.is_dir() else []


def _valid_es(es: str) -> bool:
    import re
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,40}", es or ""))


def server_put(es: str, fname: str, data: bytes) -> tuple[int, str]:
    """Zapis grafiki z komputera. (kod HTTP, opis)."""
    import os
    from emustart import art, library
    if not _valid_es(es):
        return 400, "zły system"
    kind = next((k for k in KINDS if fname.endswith(f".{k}.png")), "")
    stem = fname[: -len(f".{kind}.png")] if kind else ""
    if not kind or not stem or "/" in fname or "\\" in fname or fname.startswith("."):
        return 400, "zła nazwa pliku"
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_FILE:
        return 400, "to nie jest obraz PNG"
    ids = _games_by_thumb(es).get(stem)
    if not ids:
        return 404, "serwer nie ma takiej gry"
    dst = (SERVER_MEDIA or paths.MEDIA) / es / fname
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dst)
    for gid in ids:
        library.set_art(gid, kind, art.HAS)
    return 200, "ok"
