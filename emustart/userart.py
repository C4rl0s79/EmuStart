"""userart — grafiki wgrane przez użytkowników i wybory grafik kont (serwer EmuStart).

Pliki: `<dane serwera>/userart/files/<sha256>.<ext>` — adresowane treścią, ta sama grafika od kilku osób to jeden plik.
Indeks: `<dane serwera>/userart/index.json` — dla każdego konta `uploads` (co wgrało: sha, ext, gra, rodzaj) i `choices`
(wybór grafiki: klucz `<układ>|<gra>|<rodzaj>` → {choice, at}; nowszy wygrywa, choice null = wybór usunięty).

Konto zawsze z tokenu urządzenia (remote._sync_identity), nigdy z treści. Galeria pokazuje wgrane grafiki danej gry
wszystkim kontom serwera: „skopiowanie” cudzej grafiki to zapisanie jej sha jako własnego wyboru. Plik nie znika, gdy
autor usunie swój wpis — inni mogli go już wybrać.

Grafiki są tylko danymi: rozpoznawane po nagłówku (PNG, JPEG, WebP, GIF), bez rozszerzeń i ścieżek od klienta.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

from emustart import paths

MAX_FILE = 40 << 20
MAX_UPLOADS = 5000                     # na konto
MAX_ITEMS = 20000                      # wyborów na konto / w jednym zapytaniu
MAX_ALL = 300                          # galeria całego serwera
# źródła grafik wybieranych kluczami użytkownika i z sieci (klient Windows, Lumen)
WEB_HOSTS = ("https://images.igdb.com/", "https://cdn.thegamesdb.net/", "https://cdn2.steamgriddb.com/",
             "https://images.launchbox-app.com/", "https://thumbnails.libretro.com/")
SHA = re.compile(r"^[0-9a-f]{64}$")
GAME = re.compile(r"^[A-Za-z0-9:_./-]{1,200}$")
KINDS = ("grid", "hero", "logo", "icon")
LAYOUTS = ("desktop", "tv")
CTYPE = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
_lock = threading.Lock()


class ArtError(ValueError):
    pass


def _dir() -> Path:
    return paths.DATA / "userart"


def _load() -> dict:
    try:
        d = json.loads((_dir() / "index.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"accounts": {}}
    except (OSError, ValueError):
        return {"accounts": {}}


def _save(d: dict) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    tmp = _dir() / "index.json.tmp"
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, _dir() / "index.json")


def _acc(d: dict, account: str) -> dict:
    return d.setdefault("accounts", {}).setdefault(account, {"uploads": [], "choices": {}})


def sniff(data: bytes) -> str:
    """Rodzaj obrazu po nagłówku albo '' — nie obraz."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return ""


def _check(game: str, kind: str) -> None:
    if not GAME.match(str(game or "")) or ".." in str(game):
        raise ArtError("zły identyfikator gry")
    if kind not in KINDS:
        raise ArtError("zły rodzaj grafiki")


def upload(account: str, game: str, kind: str, data: bytes) -> dict:
    _check(game, kind)
    if not data or len(data) > MAX_FILE:
        raise ArtError("plik pusty albo za duży (najwyżej 40 MB)")
    ext = sniff(data)
    if not ext:
        raise ArtError("to nie jest obraz PNG, JPEG, WebP ani GIF")
    sha = hashlib.sha256(data).hexdigest()
    f = _dir() / "files" / f"{sha}.{ext}"
    if not f.is_file():
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + f".{threading.get_ident()}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, f)
    with _lock:
        d = _load()
        a = _acc(d, account)
        if not any(u["sha"] == sha and u["game"] == game and u["kind"] == kind for u in a["uploads"]):
            if len(a["uploads"]) >= MAX_UPLOADS:
                raise ArtError("za dużo wgranych grafik na tym koncie")
            a["uploads"].append({"sha": sha, "ext": ext, "game": game, "kind": kind, "size": len(data), "at": time.time()})
            _save(d)
    return {"ok": True, "sha": sha, "ext": ext}


def remove(account: str, game: str, kind: str, sha: str) -> dict:
    """Usuwa wpis z galerii autora; plik zostaje (ktoś mógł go wybrać)."""
    with _lock:
        d = _load()
        a = _acc(d, account)
        before = len(a["uploads"])
        a["uploads"] = [u for u in a["uploads"] if not (u["sha"] == sha and u["game"] == game and u["kind"] == kind)]
        if len(a["uploads"]) != before:
            _save(d)
    return {"ok": True}


def gallery(account: str, game: str, kind: str) -> dict:
    """Grafiki danego rodzaju wgrane przez wszystkie konta, dla tej gry; bez `game` — cały serwer (najnowsze, najwyżej
    MAX_ALL). Własne pierwsze."""
    _check(game or "x", kind)
    d = _load()
    seen, out = set(), []
    for name, a in sorted((d.get("accounts") or {}).items(), key=lambda x: x[0].lower() != account.lower()):
        for u in a.get("uploads") or []:
            if (not game or u["game"] == game) and u["kind"] == kind and u["sha"] not in seen:
                seen.add(u["sha"])
                out.append({"sha": u["sha"], "ext": u["ext"], "size": u["size"], "at": u["at"], "by": name,
                            "mine": name.lower() == account.lower(), "game": u["game"]})
    if not game:
        out.sort(key=lambda x: (not x["mine"], -float(x["at"])))
        out = out[:MAX_ALL]
    return {"ok": True, "items": out}


def file_path(sha: str) -> tuple:
    """(plik, typ) albo (None, '')."""
    if SHA.match(str(sha or "")):
        for ext, ctype in CTYPE.items():
            f = _dir() / "files" / f"{sha}.{ext}"
            if f.is_file():
                return f, ctype
    return None, ""


def _valid_choice(c) -> dict | None:
    """Tylko znane rodzaje i pola; ścieżki lokalne (`file`) nie wychodzą z urządzenia."""
    if c is None:
        return None
    if not isinstance(c, dict):
        raise ArtError("zły wybór")
    t = c.get("type")
    https = lambda v: isinstance(v, str) and v.startswith("https://") and len(v) < 1000   # noqa: E731
    if t in ("steam", "none"):
        return {"type": t}
    if t == "launchbox" and https(c.get("url")):
        return {"type": t, "url": c["url"]}
    if t == "sgdb" and https(c.get("url")) and isinstance(c.get("imageId"), int):
        return {"type": t, "url": c["url"], "imageId": c["imageId"], **({"thumb": c["thumb"]} if https(c.get("thumb")) else {})}
    if t == "web" and https(c.get("url")) and c["url"].startswith(WEB_HOSTS):
        return {"type": t, "url": c["url"]}
    if t == "user" and file_path(c.get("sha"))[0] is not None and c.get("ext") in CTYPE:
        return {"type": t, "sha": c["sha"], "ext": c["ext"]}
    raise ArtError("zły wybór")


def sync(account: str, items: list) -> dict:
    """Wybory z urządzenia → serwer (nowszy `at` wygrywa, błędny wpis jest pomijany); wynik: wszystkie wybory konta."""
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise ArtError("zła lista wyborów")
    with _lock:
        d = _load()
        a = _acc(d, account)
        changed = False
        for it in items:
            try:
                key = str(it["key"])
                lay, game, kind = key.split("|")
                _check(game, kind)
                if lay not in LAYOUTS:
                    continue
                at = float(it.get("at") or 0)
                if at > time.time() + 86400:           # zegar urządzenia z przyszłości nie zablokuje późniejszych zmian
                    at = time.time()
                cur = a["choices"].get(key)
                if cur is None or at > float(cur.get("at") or 0):
                    a["choices"][key] = {"choice": _valid_choice(it.get("choice")), "at": at}
                    changed = True
            except (KeyError, TypeError, ValueError):
                continue
        if changed:
            _save(d)
        return {"ok": True, "choices": dict(a["choices"])}
