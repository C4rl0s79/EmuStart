"""ownart — własne grafiki gier użytkownika w kliencie Windows (Lumen ma swoje: prefs.art).

Grafiki domyślne przychodzą z serwera i nie są tu ruszane. Wybór gracza leży osobno: `<dane>/ownart.json`
({profil: {"emu:<sid>|<grid/hero/logo>": {choice, at, file}}}), pliki w `media/_moje`, nakładany na listę gier.
Klucz i rodzaj jak w Lumenie, więc wybory synchronizują się z kontem jako wybory trybu TV (`tv|emu:<sid>|grid`).
Źródła: grafiki wgrane na serwer (userart) i sieć kluczami użytkownika z tego urządzenia, nie przez serwer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from emustart import art_sources, artsync, paths, savesync, syncclient, userart

log = logging.getLogger("emustart.ownart")

KINDS = {"box": "grid", "snap": "hero", "logo": "logo"}
BACK = {v: k for k, v in KINDS.items()}
MAX_THUMBS = 40
_lock = threading.Lock()


def _path() -> Path:
    return paths.DATA / "ownart.json"


def _dir() -> Path:
    return paths.MEDIA / "_moje"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _put(pid: int, key: str, entry: dict) -> None:
    with _lock:
        d = _load()
        d.setdefault(str(pid), {})[key] = entry
        _path().parent.mkdir(parents=True, exist_ok=True)
        tmp = _path().with_name("ownart.json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, _path())


def game_key(es: str, name: str) -> str:
    return f"emu:{savesync.game_id(es, name)}"


def _url(fname: str) -> str:
    f = _dir() / fname
    v = int(f.stat().st_mtime) if f.is_file() else 0
    return "/media/_moje/" + urllib.parse.quote(fname) + f"?v={v}"


def apply(rows: list, es: str, pid: int) -> None:
    """Nakłada wybory gracza na wiersze gier (pola box / snap / logo)."""
    mine = _load().get(str(pid))
    if not mine:
        return
    for r in rows:
        gk = game_key(es, r["name"])
        for k, lk in KINDS.items():
            f = (mine.get(f"{gk}|{lk}") or {}).get("file")
            if f:
                r[k] = _url(f)


def _write(data: bytes, ext: str) -> str:
    fname = f"{hashlib.sha256(data).hexdigest()}.{ext}"
    _dir().mkdir(parents=True, exist_ok=True)
    (_dir() / fname).write_bytes(data)
    return fname


def _from_url(url: str) -> str:
    if not str(url).startswith(userart.WEB_HOSTS):
        raise ValueError("Nieoczekiwany adres grafiki.")
    data = art_sources.fetch(url, timeout=30)
    ext = userart.sniff(data or b"")
    if not ext:
        raise ValueError("Nie udało się pobrać grafiki.")
    return _write(data, ext)


def _from_server(cfg: dict, pid: int, sha: str, ext: str) -> str:
    p = artsync.file(cfg, pid, sha, ext).get("path")
    if not p:
        raise ValueError("Nie udało się pobrać grafiki z serwera.")
    _dir().mkdir(parents=True, exist_ok=True)
    fname = f"{sha}.{ext}"
    if not (_dir() / fname).is_file():
        shutil.copyfile(p, _dir() / fname)
    return fname


def candidates(cfg: dict, pid: int, es: str, name: str, kind: str, query: str = "") -> list:
    """[{url, sha, ext, thumb, source, label}]: najpierw grafiki z serwera, potem sieć kluczami z tego urządzenia."""
    out = []
    try:
        items = artsync.gallery(cfg, pid, game_key(es, name), KINDS[kind]).get("items") or []
        with ThreadPoolExecutor(6) as ex:
            files = list(ex.map(lambda i: _try_server(cfg, pid, i), items[:MAX_THUMBS]))
        for i, f in zip(items, files):
            if f:
                out.append({"url": "", "sha": i["sha"], "ext": i["ext"], "thumb": _url(f), "source": "Serwer",
                            "label": "moja" if i.get("mine") else i.get("by", "")})
    except (syncclient.Offline, syncclient.ConflictError, OSError, ValueError) as ex:
        log.info("galeria grafik: %s", ex)
    try:
        out += [dict(c, sha="", ext="") for c in art_sources.candidates(cfg, es, name, kind, query)
                if str(c["url"]).startswith(userart.WEB_HOSTS)]
    except (OSError, ValueError, KeyError, TypeError) as ex:
        log.warning("propozycje grafik: %s", ex)
    return out


def _try_server(cfg: dict, pid: int, item: dict) -> str:
    try:
        return _from_server(cfg, pid, item["sha"], item["ext"])
    except (syncclient.Offline, syncclient.ConflictError, OSError, ValueError):
        return ""


def choose(cfg: dict, pid: int, es: str, name: str, kind: str, url: str, sha: str = "", ext: str = "") -> str:
    """Zapisuje wybór; zwraca adres grafiki."""
    if sha:
        fname, choice = _from_server(cfg, pid, sha, ext), {"type": "user", "sha": sha, "ext": ext}
    else:
        fname, choice = _from_url(url), {"type": "web", "url": url}
    _put(pid, f"{game_key(es, name)}|{KINDS[kind]}", {"choice": choice, "at": time.time(), "file": fname})
    return _url(fname)


def clear(pid: int, es: str, name: str, kind: str) -> None:
    _put(pid, f"{game_key(es, name)}|{KINDS[kind]}", {"choice": None, "at": time.time(), "file": ""})


def _file_of(cfg: dict, pid: int, c: dict | None) -> str:
    """Plik wyboru z innego urządzenia; '' — grafika domyślna."""
    t = (c or {}).get("type")
    if t == "user":
        return _from_server(cfg, pid, c["sha"], c["ext"])
    if t in ("sgdb", "launchbox", "web"):
        return _from_url(c["url"])
    return ""


def sync(cfg: dict, pid: int) -> int:
    """Wybory tego urządzenia ↔ konto (nowszy wygrywa). Liczba zmian przyjętych z serwera."""
    mine = dict(_load().get(str(pid)) or {})
    items = [{"key": f"tv|{k}", "choice": e.get("choice"), "at": e.get("at", 0)} for k, e in mine.items()]
    changed = 0
    for key, v in (artsync.sync(cfg, pid, items).get("choices") or {}).items():
        lay, _, rest = key.partition("|")
        if lay != "tv" or not rest.startswith("emu:") or rest.rpartition("|")[2] not in BACK:
            continue
        if float(v.get("at") or 0) <= float((mine.get(rest) or {}).get("at") or 0):
            continue
        try:
            _put(pid, rest, {"choice": v.get("choice"), "at": v.get("at"), "file": _file_of(cfg, pid, v.get("choice"))})
            changed += 1
        except (syncclient.Offline, syncclient.ConflictError, OSError, ValueError) as ex:
            log.info("wybór grafiki %s: %s", rest, ex)                  # spróbuje przy następnej synchronizacji
    return changed
