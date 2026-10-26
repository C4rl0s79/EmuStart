"""clientdist — programy do pobrania ze strony serwera (0.49): EmuStart (Windows, Android), Lumen.

Serwer co kilka godzin (i na żądanie z panelu) sprawdza najnowsze wydania na GitHubie,
pobiera plik programu i trzyma go u siebie — strona serwera (emustart/webportal.py) daje go
zalogowanym bez sięgania do GitHuba:

    <dane serwera>\\clients\\<program>\\<plik>
    <dane serwera>\\clients\\index.json        wersje, rozmiary, skróty, błędy

Plik trafia do pobrania dopiero po sprawdzeniu skrótu SHA-256 (z API GitHuba) i **podpisu
wydania** (`<plik>.sig`, Ed25519 — emustart/signing.py: klucz EmuStart, dla Lumen — klucz
Lumen). Bez poprawnego podpisu zostaje poprzednia wersja.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.request
from pathlib import Path

from emustart import __version__, paths, signing

log = logging.getLogger("emustart.clientdist")

UA = f"EmuStart-Server/{__version__}"
EVERY = 6 * 3600
# program → repozytorium, plik wydania, klucz i przedrostek podpisu
APPS = {
    "emustart-windows": {"repo": "C4rl0s79/EmuStart", "asset": re.compile(r"EmuStart-[\d.]+-win64\.zip"),
                         "key": signing.RELEASE_PUBKEY, "prefix": signing.PREFIX},
    "emustart-android": {"repo": "C4rl0s79/EmuStart", "asset": re.compile(r"EmuStart-[\d.]+-android\.apk"),
                         "key": signing.RELEASE_PUBKEY, "prefix": signing.PREFIX},
    "lumen-windows": {"repo": "C4rl0s79/Launcher", "asset": re.compile(r"Lumen-[\d.]+-setup\.exe"),
                      "key": signing.LUMEN_PUBKEY, "prefix": signing.LUMEN_PREFIX},
}
_lock = threading.Lock()
_job = {"running": False, "log": [], "finished": 0.0}


def _dir() -> Path:
    return paths.SERVER / "clients"


def load_index() -> dict:
    try:
        d = json.loads((_dir() / "index.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(d: dict) -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    tmp = _dir() / "index.json.tmp"
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _dir() / "index.json")


def _get(url: str, timeout: float = 30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA,
                                                                      "Accept": "application/vnd.github+json"}),
                                  timeout=timeout)


def _latest(app: str) -> dict:
    spec = APPS[app]
    with _get(f"https://api.github.com/repos/{spec['repo']}/releases/latest", 20) as r:
        rel = json.loads(r.read())
    assets = rel.get("assets") or []
    a = next((x for x in assets if spec["asset"].fullmatch(x.get("name") or "")), None)
    if not a:
        raise ValueError(f"wydanie {rel.get('tag_name')} nie ma pliku programu")
    sig = next((x for x in assets if x.get("name") == a["name"] + ".sig"), None)
    if not sig:
        raise ValueError(f"wydanie {rel.get('tag_name')}: brak podpisu {a['name']}.sig")
    return {"version": (rel.get("tag_name") or "").lstrip("v"), "name": a["name"], "url": a["browser_download_url"],
            "size": int(a.get("size") or 0), "sha256": (a.get("digest") or "").removeprefix("sha256:").lower(),
            "sig_url": sig["browser_download_url"], "published": rel.get("published_at") or ""}


def _fetch(app: str, rel: dict) -> dict:
    """Pobranie, skrót, podpis → wpis indeksu. Wyjątek = poprzednia wersja zostaje."""
    spec = APPS[app]
    d = _dir() / app
    d.mkdir(parents=True, exist_ok=True)
    part = d / (rel["name"] + ".part")
    with _get(rel["sig_url"]) as r:
        sig = r.read(4096).decode("ascii", "replace")
    with _get(rel["url"], 60) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    sha = signing.file_sha256(part)
    size = part.stat().st_size
    if (rel["sha256"] and sha != rel["sha256"]) or (rel["size"] and size != rel["size"]):
        part.unlink(missing_ok=True)
        raise ValueError("pobrany plik różni się od wydania (skrót / rozmiar)")
    if not signing.verify(rel["name"], sha, sig, spec["key"], spec["prefix"]):
        part.unlink(missing_ok=True)
        raise ValueError(f"zły podpis {rel['name']} — plik odrzucony")
    os.replace(part, d / rel["name"])
    for old in d.iterdir():
        if old.name != rel["name"]:
            try:
                old.unlink(missing_ok=True)
            except OSError:                   # właśnie pobierany ze strony (Windows) — zniknie następnym razem
                log.info("programy do pobrania: %s zostaje na razie (w użyciu)", old.name)
    return {"version": rel["version"], "file": rel["name"], "size": size, "sha256": sha,
            "published": rel["published"], "updated": time.time()}


def refresh(apps: list | None = None) -> dict:
    """Sprawdza i pobiera nowe wydania (w tym wątku)."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "reason": "Sprawdzanie programów już trwa."}
    try:
        _job.update(running=True, log=[])
        idx = load_index()
        for app in apps or list(APPS):
            cur = idx.get(app) or {}
            try:
                rel = _latest(app)
                if cur.get("version") == rel["version"] and path(app, idx):
                    cur.pop("error", None)
                    idx[app] = cur
                    continue
                idx[app] = _fetch(app, rel)
                _job["log"].append(f"{app}: {cur.get('version') or '—'} → {rel['version']}")
                log.info("programy do pobrania: %s %s → %s", app, cur.get("version") or "—", rel["version"])
            except Exception as ex:
                idx[app] = {**cur, "error": str(ex)[:300], "checked": time.time()}
                _job["log"].append(f"{app}: błąd — {ex}")
                log.warning("programy do pobrania: %s: %s", app, ex)
            _save_index(idx)
        return {"ok": True}
    finally:
        _job.update(running=False, finished=time.time())
        _lock.release()


def start_refresh() -> dict:
    if _job["running"]:
        return {"ok": False, "reason": "Sprawdzanie programów już trwa."}
    threading.Thread(target=refresh, daemon=True, name="programy").start()
    return {"ok": True}


def path(app: str, idx: dict | None = None) -> Path | None:
    """Plik programu do pobrania (None — serwer go jeszcze nie ma)."""
    e = (idx if idx is not None else load_index()).get(app) or {}
    if app not in APPS or not e.get("file"):
        return None
    p = _dir() / app / e["file"]
    return p if p.is_file() else None


def status() -> dict:
    idx = load_index()
    return {"ok": True, "apps": {a: {k: (idx.get(a) or {}).get(k) for k in ("version", "file", "size", "updated", "error")}
                                 | {"ready": bool(path(a, idx))} for a in APPS},
            "job": {"running": _job["running"], "log": list(_job["log"]), "finished": _job["finished"]}}


def scheduler() -> None:
    """Wątek serwera: pierwsze sprawdzenie po 2 minutach, potem co EVERY."""
    def loop():
        time.sleep(120)
        while True:
            try:
                refresh()
            except Exception:
                log.exception("programy do pobrania")
            time.sleep(EVERY)
    threading.Thread(target=loop, daemon=True, name="programy-harmonogram").start()
