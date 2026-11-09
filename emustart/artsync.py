"""artsync — grafiki użytkowników i wybory grafik: klient silnika (serwer: emustart/userart.py). Lumen woła `Api.art_user_*`."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
from pathlib import Path

from emustart import paths, syncclient as sc

MAX_FILE = 40 << 20


def _q(**kw) -> str:
    return urllib.parse.urlencode(kw)


def _cache() -> Path:
    return paths.DATA / "userart-cache"


def upload(cfg: dict, pid: int, path: str, game: str, kind: str) -> dict:
    p = Path(path)
    if not p.is_file() or p.stat().st_size > MAX_FILE:
        return {"ok": False, "reason": "Plik nie istnieje albo ma ponad 40 MB."}
    data = p.read_bytes()
    st, body = sc._Client(cfg, pid).call("POST", f"/v1/userart/upload?{_q(game=game, kind=kind)}", data,
                                         {"Content-Type": "application/octet-stream", "Content-Length": str(len(data))}, timeout=120)
    try:
        out = json.loads(body or b"{}")
    except ValueError:
        out = {}
    if st != 200:
        return {"ok": False, "reason": out.get("reason") or f"serwer odpowiedział {st}"}
    target = _cache() / f"{out['sha']}.{out['ext']}"             # od razu w kopii lokalnej
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.is_file():
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    return out


def sync(cfg: dict, pid: int, items: list) -> dict:
    return sc._Client(cfg, pid).json("POST", "/v1/userart/choices", {"items": items})


def gallery(cfg: dict, pid: int, game: str, kind: str) -> dict:
    return sc._Client(cfg, pid).json("GET", f"/v1/userart/gallery?{_q(game=game, kind=kind)}")


def remove(cfg: dict, pid: int, game: str, kind: str, sha: str) -> dict:
    return sc._Client(cfg, pid).json("POST", "/v1/userart/remove", {"game": game, "kind": kind, "sha": sha})


def file(cfg: dict, pid: int, sha: str, ext: str) -> dict:
    """Plik grafiki w kopii lokalnej (także bez serwera, jeśli był już pobrany)."""
    if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha) or ext not in ("png", "jpg", "webp", "gif"):
        return {"path": ""}
    target = _cache() / f"{sha}.{ext}"
    if target.is_file():
        return {"path": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    try:
        with open(tmp, "wb") as f:
            st, _ = sc._Client(cfg, pid).call("GET", f"/v1/userart/file/{sha}", timeout=120, sink=f)
        if st != 200 or hashlib.sha256(tmp.read_bytes()).hexdigest() != sha:
            tmp.unlink(missing_ok=True)
            return {"path": ""}
        os.replace(tmp, target)
        return {"path": str(target)}
    except (sc.Offline, OSError):
        tmp.unlink(missing_ok=True)
        return {"path": ""}
