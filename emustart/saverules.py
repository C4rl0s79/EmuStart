"""saverules — reguły lokalizacji zapisów i ustawień gier GOG (serwer EmuStart).

Źródła, w kolejności pierwszeństwa po stronie serwera:
  1. własne poprawki serwera: `<dane serwera>/saverules/overrides.json` — {"<productId>": {"title", "files", "registry"}}
     (format pól jak w ludusavi-manifest); edytuje administrator, aktualizacja manifestu ich nie zmienia,
  2. ludusavi-manifest (https://github.com/mtkennerly/ludusavi-manifest, licencja MIT, © Matthew T. Kennerly;
     dane zebrane z PCGamingWiki) — kopia lokalna `manifest.yaml` + indeks gier GOG `gog-index.json`.
Klient ma jeszcze własne reguły gracza (pierwszeństwo nad wszystkim) — emustart/gogsaves.py.

Dopasowanie wyłącznie po identyfikatorze produktu GOG: wpis z `gog.id` == productId; bez takiego — jedyny wpis
z productId w `id.gogExtra`. Kilka kandydatów → „niejednoznaczne” (klient może wskazać jednego po tytule z listy
kandydatów). Nigdy po samym tytule.

Aktualizacja (w tle, nie przed grą): GET z If-None-Match (ETag) → plik tymczasowy → parsowanie i walidacja →
podmiana; poprzedni indeks zostaje jako `gog-index.prev.json`. Błąd = dalej działa poprzednia kopia.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from emustart import paths

log = logging.getLogger("emustart.saverules")

MANIFEST_URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/refs/heads/data/data/manifest.yaml"
ATTRIBUTION = ("ludusavi-manifest (MIT License, © Matthew T. Kennerly, https://github.com/mtkennerly/ludusavi-manifest); "
               "dane z PCGamingWiki")
MAX_AGE = 7 * 86400                       # automatyczna aktualizacja najwyżej raz na tydzień
MIN_ENTRIES = 1000                        # walidacja: prawdziwy manifest ma dziesiątki tysięcy gier
MAX_DOWNLOAD = 200 << 20
TAGS = ("save", "config")
PID = re.compile(r"^\d{1,20}$")

_lock = threading.Lock()
STATE: dict = {"running": False, "error": "", "checked": 0.0}
_index: dict | None = None


def _dir() -> Path:
    return paths.DATA / "saverules"


def _index_path() -> Path:
    return _dir() / "gog-index.json"


def _atomic(p: Path, data: bytes) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


# ── budowa indeksu z manifestu ──

def _clean_rules(section, kind: str) -> dict:
    """Pola `files` / `registry` wpisu: tylko ścieżki z tekstem, znane tagi, warunki os/store (tekst)."""
    out = {}
    if not isinstance(section, dict):
        return out
    for path, spec in section.items():
        if not isinstance(path, str) or not path.strip() or len(path) > 1000:
            continue
        spec = spec if isinstance(spec, dict) else {}
        tags = [t for t in spec.get("tags") or [] if isinstance(t, str)]
        when = []
        for w in spec.get("when") or []:
            if isinstance(w, dict):
                when.append({k: str(w[k]).lower() for k in (("os", "store") if kind == "files" else ("store",))
                             if isinstance(w.get(k), str)})
        out[path] = {"tags": tags, "when": when}
    return out


def build_index(manifest: dict) -> dict:
    """{productId: [wpis…]} — wpisy z gog.id albo id.gogExtra; wpis = {title, primary, files, registry, installDir}."""
    games: dict = {}
    for title, e in manifest.items():
        if not isinstance(title, str) or not isinstance(e, dict) or e.get("alias"):
            continue
        primary = (e.get("gog") or {}).get("id") if isinstance(e.get("gog"), dict) else None
        extra = (e.get("id") or {}).get("gogExtra") if isinstance(e.get("id"), dict) else None
        ids = ([(str(primary), True)] if isinstance(primary, int) else []) + \
              [(str(x), False) for x in (extra if isinstance(extra, list) else []) if isinstance(x, int)]
        if not ids:
            continue
        entry = {"title": title, "files": _clean_rules(e.get("files"), "files"),
                 "registry": _clean_rules(e.get("registry"), "registry"),
                 "installDir": [k for k in (e.get("installDir") or {}) if isinstance(k, str)][:5]}
        for pid, is_primary in ids:
            games.setdefault(pid, []).append({**entry, "primary": is_primary})
    return games


def parse_manifest(raw: bytes) -> dict:
    import yaml                                          # tylko serwer (aktualizacja reguł)
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    data = yaml.load(raw, Loader=loader)
    if not isinstance(data, dict) or len(data) < MIN_ENTRIES:
        raise ValueError(f"to nie wygląda na ludusavi-manifest ({len(data) if isinstance(data, dict) else 0} wpisów)")
    return data


def install(raw: bytes, etag: str = "", source: str = MANIFEST_URL) -> dict:
    """Nowa wersja manifestu: walidacja, indeks, podmiana (poprzedni indeks zostaje)."""
    games = build_index(parse_manifest(raw))
    if not games:
        raise ValueError("manifest bez gier GOG")
    doc = {"meta": {"etag": etag, "source": source, "installed": time.time(), "games": len(games),
                    "version": time.strftime("%Y-%m-%d", time.gmtime()) + (f" ({etag[:12]})" if etag else ""),
                    "attribution": ATTRIBUTION}, "games": games}
    cur = _index_path()
    if cur.is_file():
        _atomic(_dir() / "gog-index.prev.json", cur.read_bytes())
    _atomic(cur, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    _atomic(_dir() / "manifest.yaml", raw)
    global _index
    with _lock:
        _index = doc
    log.info("reguły zapisów: ludusavi-manifest %s — gier GOG: %d", doc["meta"]["version"], len(games))
    return doc["meta"]


def index() -> dict:
    global _index
    with _lock:
        if _index is not None:
            return _index
    doc = {"meta": {}, "games": {}}
    for p in (_index_path(), _dir() / "gog-index.prev.json"):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            break
        except (OSError, ValueError):
            continue
    with _lock:
        _index = doc
    return doc


def update(force: bool = False, fetch=None) -> dict:
    """Pobranie nowej wersji (ETag). {"ok", "changed", "meta" | "error"}; błąd nie rusza bieżącej kopii."""
    meta = index().get("meta") or {}
    if not force and time.time() - float(meta.get("installed") or 0) < MAX_AGE and meta.get("games"):
        return {"ok": True, "changed": False, "meta": meta}
    with _lock:
        if STATE["running"]:
            return {"ok": False, "error": "aktualizacja reguł już trwa"}
        STATE.update(running=True, error="")
    try:
        st, body, etag = (fetch or _fetch)(MANIFEST_URL, meta.get("etag", ""))
        STATE["checked"] = time.time()
        if st == 304:
            return {"ok": True, "changed": False, "meta": meta}
        if st != 200 or not body:
            raise OSError(f"HTTP {st}")
        return {"ok": True, "changed": True, "meta": install(body, etag)}
    except Exception as ex:                              # sieć, YAML, walidacja — zostaje poprzednia kopia
        STATE["error"] = str(ex)
        log.warning("reguły zapisów: aktualizacja nieudana: %s", ex)
        return {"ok": False, "error": str(ex)}
    finally:
        STATE["running"] = False


def update_async(force: bool = False) -> None:
    threading.Thread(target=update, kwargs={"force": force}, daemon=True, name="saverules").start()


def _fetch(url: str, etag: str) -> tuple:
    req = urllib.request.Request(url, headers={"User-Agent": "EmuStart-Server", **({"If-None-Match": etag} if etag else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read(MAX_DOWNLOAD + 1)
            if len(body) > MAX_DOWNLOAD:
                raise OSError("manifest za duży")
            return r.status, body, r.headers.get("ETag", "")
    except urllib.error.HTTPError as ex:
        if ex.code == 304:
            return 304, b"", etag
        raise


# ── poprawki serwera ──

def overrides() -> dict:
    try:
        d = json.loads((_dir() / "overrides.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in d.items() if PID.match(str(k)) and isinstance(v, dict)} if isinstance(d, dict) else {}


def _public(entry: dict, source: str, meta: dict) -> dict:
    files = [{"path": p, "tags": [t for t in s.get("tags") or [] if t in TAGS], "when": s.get("when") or []}
             for p, s in (entry.get("files") or {}).items()]
    reg = [{"path": p, "tags": [t for t in s.get("tags") or [] if t in TAGS], "when": s.get("when") or []}
           for p, s in (entry.get("registry") or {}).items()]
    return {"source": source, "title": entry.get("title") or "", "files": files, "registry": reg,
            "installDir": entry.get("installDir") or [], "version": meta.get("version") or "",
            "attribution": ATTRIBUTION if source == "ludusavi" else ""}


def rules_for(pid: str, title: str = "") -> dict:
    """Reguły gry dla klienta. source: server (poprawka) | ludusavi | ambiguous | none."""
    pid = str(pid)
    if not PID.match(pid):
        raise ValueError("zły identyfikator gry GOG")
    ov = overrides().get(pid)
    if ov:
        e = {"title": ov.get("title") or "", "files": _clean_rules(ov.get("files"), "files"),
             "registry": _clean_rules(ov.get("registry"), "registry")}
        return _public(e, "server", {"version": "poprawka serwera"})
    doc = index()
    meta, entries = doc.get("meta") or {}, (doc.get("games") or {}).get(pid) or []
    if title:
        pick = [e for e in entries if e["title"] == title]
        if len(pick) == 1:
            return _public(pick[0], "ludusavi", meta)
    primary = [e for e in entries if e.get("primary")]
    chosen = primary if primary else entries
    if len(chosen) == 1:
        return _public(chosen[0], "ludusavi", meta)
    if not entries:
        return {"source": "none", "files": [], "registry": [], "version": meta.get("version") or "",
                "reason": "brak tej gry w bazie reguł" if meta else "serwer nie ma jeszcze bazy reguł zapisów"}
    return {"source": "ambiguous", "files": [], "registry": [], "version": meta.get("version") or "",
            "candidates": sorted(e["title"] for e in entries)}


def status() -> dict:
    meta = index().get("meta") or {}
    return {"version": meta.get("version") or "", "games": meta.get("games") or 0, "running": STATE["running"],
            "error": STATE["error"], "overrides": len(overrides())}
