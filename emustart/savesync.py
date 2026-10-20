"""savesync — synchronizacja zapisów gier: serwer jest jedynym źródłem prawdy.

Klucz paczki (bundle): konto + platforma + gra + emulator + rdzeń (RetroArch) + rodzaj
(saves | states | memcards). Każda paczka ma jedną aktualną wersję (revision) — jak Steam
Cloud: rozbieżne zmiany z dwóch urządzeń (np. gra bez internetu) to konflikt, który
rozstrzyga gracz; przegrana wersja zostaje w kopii zapasowej.

Klient zna wyłącznie identyfikatory. Konto i urządzenie serwer ustala sam z tokenu sesji
(inne konto — tylko z jego tokenem w X-Profile-Token, jak profile). Pliki idą przez
krótkotrwałe, podpisane adresy (HMAC: konto, urządzenie, paczka / wysyłka, czas) — bez
ścieżek serwera; układ na dysku może się zmienić bez zmiany API:

    <serwer>/savesync/<konto>/<platforma>/<gra>/manifest.json
    <serwer>/savesync/<konto>/<platforma>/<gra>/bundles/<bundle_id>.zip   (niezmienne)
    <serwer>/savesync/_uploads/<upload_id>.json|.part|.zip

API (emustart/remote.py, emustart/server.py):
    GET  /v1/savesync/<platforma>/<gra>/manifest        manifest z adresami pobrania
    GET  /v1/savesync/<platforma>/<gra>/status          to samo bez adresów
    POST /v1/savesync/upload/init                       → upload_id, upload_url | 409
    PUT  /v1/files/u/<token>                            treść ZIP (strumieniowo, SHA-256)
    POST /v1/savesync/upload/commit                     → nowa revision | 409
    POST /v1/savesync/<platforma>/<gra>/resolve         decyzja gracza przy konflikcie
    GET  /v1/files/d/<token>                            paczka (Range)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import zipfile
from pathlib import Path

from emustart import paths

log = logging.getLogger("emustart.savesync")

SCHEMA = 1
URL_TTL = 600                       # adresy plików: 10 minut
UPLOAD_TTL = 1800                   # wysyłka (init → commit): 30 minut
MAX_BUNDLE = 1024 * 1024 * 1024     # 1 GB (stany PS2 bywają duże)
MAX_FILES = 5000
KEEP_HISTORY = 10                   # poprzednie wersje paczki na serwerze
KINDS = ("saves", "states", "memcards")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")        # platforma, gra, emulator, rdzeń
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,600}\.[A-Za-z0-9_-]{20,100}$")
CHUNK = 1 << 20

_locks: dict = {}
_locks_guard = threading.Lock()


class SyncError(ValueError):
    """Błędne zapytanie (400)."""


class Denied(PermissionError):
    """Brak dostępu (403)."""


class Conflict(Exception):
    """base_revision nie jest aktualną wersją (409)."""

    def __init__(self, current: dict):
        super().__init__("konflikt wersji")
        self.current = current


# ── identyfikatory ──

def game_id(es: str, name: str) -> str:
    """Stały identyfikator gry, taki sam na każdym urządzeniu: z systemu i nazwy gry na
    serwerze (lokalne numery gier w bazach urządzeń się różnią)."""
    key = f"{(es or '').lower()}|{re.sub(r'[\s_]+', ' ', name or '').strip().lower()}"
    return "g" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def _id(value, what: str, empty_ok: bool = False) -> str:
    v = str(value or "").strip().lower()
    if not v and empty_ok:
        return ""
    if not _ID.match(v):
        raise SyncError(f"nieprawidłowy {what}")
    return v


def _key(emulator_id, core_id, bundle_kind) -> tuple:
    emu = _id(emulator_id, "emulator_id")
    core = _id(core_id, "core_id", empty_ok=True)
    kind = str(bundle_kind or "")
    if kind not in KINDS:
        raise SyncError("nieprawidłowy bundle_kind")
    if emu == "retroarch" and not core:
        raise SyncError("RetroArch: core_id jest wymagany (dane rdzeni są osobne)")
    if emu != "retroarch":
        core = ""
    return emu, core, kind


# ── układ na dysku (wewnętrzny) ──

def _root() -> Path:
    return paths.SERVER / "savesync"


def _account_dir(account: str) -> Path:
    # nazwa konta może mieć spacje i polskie litery — folder z jej skrótu (wewnętrzny)
    return _root() / ("a" + hashlib.sha1(account.lower().encode("utf-8")).hexdigest()[:20])


def _game_dir(account: str, platform: str, game: str) -> Path:
    return _account_dir(account) / platform / game


def _uploads() -> Path:
    return _root() / "_uploads"


def _lock_for(*key) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _load(account: str, platform: str, game: str) -> dict:
    try:
        m = json.loads((_game_dir(account, platform, game) / "manifest.json").read_text(encoding="utf-8"))
        if isinstance(m, dict) and m.get("schema_version") == SCHEMA:
            return m
    except (OSError, ValueError):
        pass
    return {"schema_version": SCHEMA, "platform_id": platform, "game_id": game, "revision": 0,
            "updated_at": 0, "updated_by_device_id": "", "emulators": {}}


def _save(account: str, platform: str, game: str, m: dict) -> None:
    d = _game_dir(account, platform, game)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".manifest.{secrets.token_hex(6)}.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "manifest.json")


def _bundle(m: dict, emu: str, core: str, kind: str, create: bool = False) -> dict | None:
    e = m["emulators"].setdefault(emu, {}) if create else m["emulators"].get(emu)
    if e is None:
        return None
    if emu == "retroarch":
        c = e.setdefault("cores", {}).setdefault(core, {}) if create else (e.get("cores") or {}).get(core)
        if c is None:
            return None
        return c.setdefault("bundles", {}).setdefault(kind, {}) if create else (c.get("bundles") or {}).get(kind)
    return e.setdefault("bundles", {}).setdefault(kind, {}) if create else (e.get("bundles") or {}).get(kind)


def _bundles(m: dict):
    """(emulator, rdzeń, rodzaj, węzeł) — wszystkie paczki manifestu."""
    for emu, e in (m.get("emulators") or {}).items():
        if emu == "retroarch":
            for core, c in (e.get("cores") or {}).items():
                for kind, b in (c.get("bundles") or {}).items():
                    yield emu, core, kind, b
        else:
            for kind, b in (e.get("bundles") or {}).items():
                yield emu, "", kind, b


# ── podpisane adresy ──

def _secret() -> bytes:
    from emustart import remote
    return ("savesync|" + remote._secret()).encode("utf-8")


def make_token(payload: dict, ttl: int = URL_TTL) -> str:
    body = dict(payload, exp=int(time.time()) + ttl)
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(hmac.new(_secret(), raw, hashlib.sha256).digest()).rstrip(b"=")
    return raw.decode() + "." + sig.decode()


def read_token(token: str, op: str) -> dict | None:
    """Treść ważnego tokenu danej operacji albo None (podpis, czas, rodzaj)."""
    if not _TOKEN.match(token or ""):
        return None
    raw, sig = token.split(".", 1)
    good = base64.urlsafe_b64encode(hmac.new(_secret(), raw.encode(), hashlib.sha256).digest()).rstrip(b"=")
    if not hmac.compare_digest(good.decode(), sig):
        return None
    try:
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        return None
    if not isinstance(body, dict) or body.get("op") != op or int(body.get("exp", 0)) < time.time():
        return None
    return body


# ── manifest ──

def manifest(account: str, device: str, platform, game, urls: bool = True) -> dict:
    """Manifest gry dla klienta: wersje paczek i (urls) krótkotrwałe adresy pobrania."""
    platform, game = _id(platform, "platform_id"), _id(game, "game_id")
    m = _load(account, platform, game)
    out = json.loads(json.dumps(m))
    for emu, core, kind, b in _bundles(out):
        b.pop("history", None)
        b.pop("file", None)
        if urls and b.get("bundle_id"):
            b["download_url"] = "/v1/files/d/" + make_token(
                {"op": "get", "acc": account, "dev": device, "p": platform, "g": game,
                 "e": emu, "c": core, "k": kind, "b": b["bundle_id"]})
            b["expires_in"] = URL_TTL
    _log("manifest", account, device, platform, game, "", "", "", out.get("revision"))
    return out


def download_path(token: str) -> tuple[Path, dict] | None:
    """Plik paczki dla ważnego adresu pobrania (zawsze ta wersja, na którą wystawiono adres)."""
    t = read_token(token, "get")
    if not t:
        return None
    try:
        p = _game_dir(t["acc"], _id(t["p"], "p"), _id(t["g"], "g")) / "bundles" / f"{_id(t['b'], 'b')}.zip"
    except SyncError:
        return None
    return (p, t) if p.is_file() else None


# ── wysyłanie ──

def upload_init(account: str, device: str, d: dict) -> dict:
    platform, game = _id(d.get("platform_id"), "platform_id"), _id(d.get("game_id"), "game_id")
    emu, core, kind = _key(d.get("emulator_id"), d.get("core_id"), d.get("bundle_kind"))
    sha = str(d.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SyncError("nieprawidłowy sha256")
    size = int(d.get("size") or 0)
    if size <= 0 or size > MAX_BUNDLE:
        raise SyncError("nieprawidłowy rozmiar")
    base = int(d.get("base_revision", -1))
    with _lock_for(account, platform, game):
        b = _bundle(_load(account, platform, game), emu, core, kind) or {}
    if base != int(b.get("revision") or 0):
        raise Conflict(_public(b))           # szybciej niż po wysłaniu całej paczki
    uid = "upl_" + secrets.token_urlsafe(16)
    rec = {"upload_id": uid, "acc": account, "dev": device, "p": platform, "g": game, "e": emu, "c": core,
           "k": kind, "base": base, "sha256": sha, "size": size, "exp": time.time() + UPLOAD_TTL}
    _uploads().mkdir(parents=True, exist_ok=True)
    (_uploads() / f"{uid}.json").write_text(json.dumps(rec), encoding="utf-8")
    _prune_uploads()
    url = "/v1/files/u/" + make_token({"op": "put", "acc": account, "dev": device, "u": uid}, UPLOAD_TTL)
    _log("upload/init", account, device, platform, game, emu, core, kind, base, sha=sha, size=size, uid=uid)
    return {"ok": True, "upload_id": uid, "upload_url": url, "expires_in": UPLOAD_TTL}


def _upload_rec(uid: str) -> dict | None:
    if not re.fullmatch(r"upl_[A-Za-z0-9_-]{10,40}", uid or ""):
        return None
    try:
        rec = json.loads((_uploads() / f"{uid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if rec.get("exp", 0) >= time.time() else None


def upload_body(token: str, stream, length: int) -> dict:
    """Treść paczki pod adresem wysyłki: strumieniowo do .part, SHA-256 i rozmiar jak w init,
    poprawny ZIP bez ścieżek wychodzących poza paczkę — dopiero wtedy .zip."""
    t = read_token(token, "put")
    rec = _upload_rec(t["u"]) if t else None
    if not rec or rec["acc"] != t["acc"] or rec["dev"] != t["dev"]:
        raise Denied("adres wysyłki nieważny albo wygasł")
    if length != rec["size"]:
        raise SyncError("rozmiar inny niż zgłoszony")
    part = _uploads() / f"{rec['upload_id']}.part"
    h, left = hashlib.sha256(), length
    with open(part, "wb") as f:
        while left > 0:
            buf = stream.read(min(CHUNK, left))
            if not buf:
                break
            h.update(buf)
            f.write(buf)
            left -= len(buf)
    if left or h.hexdigest() != rec["sha256"]:
        part.unlink(missing_ok=True)
        raise SyncError("uszkodzona paczka (rozmiar albo SHA-256)")
    why = check_zip(part)
    if why:
        part.unlink(missing_ok=True)
        raise SyncError(why)
    os.replace(part, _uploads() / f"{rec['upload_id']}.zip")
    return {"ok": True}


def check_zip(p: Path) -> str:
    """'' — poprawna paczka; inaczej powód odrzucenia."""
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            if len(names) > MAX_FILES:
                return "za dużo plików w paczce"
            for n in names:
                if n.startswith(("/", "\\")) or ".." in n.replace("\\", "/").split("/") or ":" in n:
                    return f"niedozwolona ścieżka w paczce: {n}"
            bad = z.testzip()
            if bad:
                return f"uszkodzony plik w paczce: {bad}"
    except (zipfile.BadZipFile, OSError) as ex:
        return f"to nie jest poprawny ZIP ({ex})"
    return ""


def upload_commit(account: str, device: str, d: dict, force: bool = False) -> dict:
    """Nowa wersja paczki — tylko gdy base_revision jest aktualną (inaczej Conflict);
    force (decyzja gracza „zostaw z tego urządzenia”) — mimo nowszej wersji, która trafia
    do historii. Manifest zmienia się atomowo."""
    uid = str(d.get("upload_id") or "")
    rec = _upload_rec(uid)
    if not rec or rec["acc"] != account or rec["dev"] != device:
        raise Denied("nieznana albo wygasła wysyłka")
    if str(d.get("sha256") or "").lower() != rec["sha256"] or int(d.get("size") or 0) != rec["size"]:
        raise SyncError("SHA-256 albo rozmiar inny niż zgłoszony")
    data = _uploads() / f"{uid}.zip"
    if not data.is_file():
        raise SyncError("paczka nie została wysłana")
    p, g = rec["p"], rec["g"]
    with _lock_for(account, p, g):
        m = _load(account, p, g)
        b = _bundle(m, rec["e"], rec["c"], rec["k"], create=True)
        cur = int(b.get("revision") or 0)
        if not force and int(d.get("base_revision", -1)) != cur:
            _log("conflict", account, device, p, g, rec["e"], rec["c"], rec["k"], cur, uid=uid)
            raise Conflict(_public(b))
        gdir = _game_dir(account, p, g)
        (gdir / "bundles").mkdir(parents=True, exist_ok=True)
        bid = "bnd_" + secrets.token_urlsafe(12)
        os.replace(data, gdir / "bundles" / f"{bid}.zip")
        hist = list(b.get("history") or [])
        if b.get("bundle_id"):
            # poprzednia wersja zostaje (kopia zapasowa) — także przegrana przy konflikcie
            hist.insert(0, {k: b.get(k) for k in ("bundle_id", "revision", "sha256", "size", "file_count",
                                                   "updated_at", "updated_by_device_id")})
        for old in hist[KEEP_HISTORY:]:
            (gdir / "bundles" / f"{old['bundle_id']}.zip").unlink(missing_ok=True)
        with zipfile.ZipFile(gdir / "bundles" / f"{bid}.zip") as z:
            nfiles = len(z.namelist())
        m["revision"] = int(m.get("revision") or 0) + 1
        now = time.time()
        b.clear()
        b.update(bundle_id=bid, revision=m["revision"], sha256=rec["sha256"], size=rec["size"],
                 file_count=nfiles, updated_at=now, updated_by_device_id=device, history=hist[:KEEP_HISTORY])
        m["updated_at"], m["updated_by_device_id"] = now, device
        _save(account, p, g, m)
    (_uploads() / f"{uid}.json").unlink(missing_ok=True)
    _log("upload/commit" + (" (wymuszone)" if force else ""), account, device, p, g, rec["e"], rec["c"],
         rec["k"], f"{cur}→{m['revision']}", sha=rec["sha256"], size=rec["size"], uid=uid)
    return {"ok": True, "revision": m["revision"], "bundle_id": bid}


def resolve(account: str, device: str, platform, game, d: dict) -> dict:
    """Decyzja gracza przy konflikcie: local — wersja z tego urządzenia (wysłana wcześniej,
    upload_id) zostaje, serwerowa do historii; remote / skip — nic na serwerze (urządzenie
    pobierze wersję z serwera albo pomija grę)."""
    choice = d.get("choice")
    platform, game = _id(platform, "platform_id"), _id(game, "game_id")
    if choice == "local":
        rec = _upload_rec(str(d.get("upload_id") or ""))
        if not rec or rec["p"] != platform or rec["g"] != game:
            raise SyncError("brak wysłanej wersji z tego urządzenia")
        r = upload_commit(account, device, {**d, "sha256": rec["sha256"], "size": rec["size"]}, force=True)
        _log("resolve local", account, device, platform, game, rec["e"], rec["c"], rec["k"], r["revision"])
        return r
    if choice in ("remote", "skip"):
        _log(f"resolve {choice}", account, device, platform, game, "", "", "", "")
        return {"ok": True, "choice": choice}
    raise SyncError("nieznany wybór")


def _public(b: dict) -> dict:
    return {k: b.get(k) for k in ("bundle_id", "revision", "sha256", "size", "file_count",
                                  "updated_at", "updated_by_device_id")}


def _prune_uploads() -> None:
    now = time.time()
    for f in _uploads().glob("upl_*.json"):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("exp", 0) < now - 60:
                for ext in (".json", ".part", ".zip"):
                    f.with_suffix(ext).unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


def history(account: str, platform, game) -> list:
    """Dla panelu serwera: poprzednie wersje paczek gry (kopie zapasowe)."""
    m = _load(account, _id(platform, "platform_id"), _id(game, "game_id"))
    return [{"emulator_id": e, "core_id": c, "bundle_kind": k, **_public(b), "history": b.get("history") or []}
            for e, c, k, b in _bundles(m)]


def _log(action: str, account: str, device: str, platform: str, game: str, emu: str, core: str, kind: str,
         rev, sha: str = "", size: int = 0, uid: str = "") -> None:
    # bez adresów i tokenów — tylko identyfikatory
    log.info("savesync %s: konto=%s urządzenie=%s %s/%s %s/%s/%s rev=%s%s%s%s", action, account, device or "-",
             platform, game, emu or "-", core or "-", kind or "-", rev, f" sha={sha[:12]}" if sha else "",
             f" rozmiar={size}" if size else "", f" {uid}" if uid else "")
