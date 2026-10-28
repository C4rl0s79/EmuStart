"""savesync — synchronizacja zapisów gier: serwer jest jedynym źródłem prawdy.

Klucz paczki (bundle): konto + platforma + gra + slot + emulator + rdzeń (RetroArch) + rodzaj
(saves | states | memcards | trash). Slot (1–4) to osobne „przejście” gry: komplet zapisów
i stanów; jeden z nich jest aktywny (`active_slot`, wspólny dla urządzeń konta). Zapytania bez
`slot` (aplikacje sprzed slotów) dotyczą slotu 1 — w manifeście to dotychczasowe `emulators`. Każda paczka ma jedną aktualną wersję (revision) — jak Steam
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
    POST /v1/savesync/<platforma>/<gra>/slot            zmiana aktywnego slotu {slot, at}
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
KINDS = ("saves", "states", "memcards", "trash")     # trash — kosz slotu: stany usunięte przez gracza
SLOTS = (1, 2, 3, 4)
KEEP_REMOVED = 500                  # wpisy „usunięte / przeniesione świadomie” na paczkę stanów
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
    elif kind == "saves":
        # zapisy w grze wspólne dla rdzeni o zgodnym formacie (Snes9x ↔ bsnes: surowy SRAM);
        # klient starszy niż 0.47 podaje swój rdzeń — trafia do tej samej paczki
        from emustart import savebundles
        core = savebundles.save_core(core)
    return emu, core, kind


# ── układ na dysku (wewnętrzny) ──

def _root() -> Path:
    return paths.SERVER / "savesync"


def _account_dir(account: str) -> Path:
    # nazwa konta może mieć spacje i polskie litery — folder z jej skrótu (wewnętrzny)
    return _root() / ("a" + hashlib.sha1(account.lower().encode("utf-8")).hexdigest()[:20])


def slot_no(value) -> int:
    """Numer slotu z zapytania: brak → 1 (aplikacje sprzed slotów)."""
    if value in (None, ""):
        return 1
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise SyncError("nieprawidłowy slot")
    if n not in SLOTS:
        raise SyncError("nieprawidłowy slot")
    return n


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


def _emus(m: dict, slot: int = 1, create: bool = False) -> dict | None:
    """Paczki slotu: slot 1 — dotychczasowe `emulators` (stare aplikacje), 2–4 — `slots`."""
    if slot == 1:
        return m.setdefault("emulators", {})
    if create:
        return m.setdefault("slots", {}).setdefault(str(slot), {}).setdefault("emulators", {})
    return ((m.get("slots") or {}).get(str(slot)) or {}).get("emulators")


def _save(account: str, platform: str, game: str, m: dict) -> None:
    d = _game_dir(account, platform, game)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".manifest.{secrets.token_hex(6)}.tmp"
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "manifest.json")


def _bundle(m: dict, emu: str, core: str, kind: str, create: bool = False, slot: int = 1) -> dict | None:
    emus = _emus(m, slot, create)
    if emus is None:
        return None
    e = emus.setdefault(emu, {}) if create else emus.get(emu)
    if e is None:
        return None
    if emu == "retroarch":
        c = e.setdefault("cores", {}).setdefault(core, {}) if create else (e.get("cores") or {}).get(core)
        if c is None:
            return None
        return c.setdefault("bundles", {}).setdefault(kind, {}) if create else (c.get("bundles") or {}).get(kind)
    return e.setdefault("bundles", {}).setdefault(kind, {}) if create else (e.get("bundles") or {}).get(kind)


def _fold_groups(m: dict) -> bool:
    """_fold_one dla każdego slotu."""
    changed = False
    for n in SLOTS:
        emus = _emus(m, n)
        if emus:
            changed = _fold_one(emus) or changed
    return changed


def _fold_one(emus: dict) -> bool:
    """Paczki zapisów zgodnych rdzeni (sprzed 0.47: osobno Snes9x, bsnes…) → jedna paczka grupy:
    najnowsza zostaje aktualną wersją grupy (ten sam plik i numer wersji — urządzenia, które
    ją mają, nic nie pobierają), pozostałe trafiają do historii grupy. Paczki rdzeni zostają
    (nic nie znika). True — manifest zmieniony."""
    from emustart import savebundles
    cores = (emus.get("retroarch") or {}).get("cores") or {}
    changed = False
    for group, members in savebundles.SAVE_GROUPS.items():
        if ((cores.get(group) or {}).get("bundles") or {}).get("saves"):
            continue
        old = [(c, cores[c]["bundles"]["saves"]) for c in sorted(members)
               if ((cores.get(c) or {}).get("bundles") or {}).get("saves", {}).get("bundle_id")
               and not cores[c]["bundles"]["saves"].get("mirror")]
        if not old:
            continue
        old.sort(key=lambda x: -float(x[1].get("updated_at") or 0))
        top_core, top = old[0]
        hist = []
        for c, b in old[1:]:
            hist.append({**_public(b), "core_id": c, "note": f"zapisy rdzenia {c} sprzed wspólnych zapisów"})
        for c, b in old:
            hist += [dict(h, core_id=h.get("core_id") or c) for h in (b.get("history") or [])]
        hist.sort(key=lambda h: -float(h.get("updated_at") or 0))
        g = cores.setdefault(group, {}).setdefault("bundles", {}).setdefault("saves", {})
        g.update({**_public(top), "from_core": top_core, "history": hist[:KEEP_HISTORY]})
        for _c, b in old:
            b["folded_into"] = group
        changed = True
    return changed


def _load_live(account: str, platform: str, game: str) -> dict:
    """Manifest do pracy (pod blokadą gry): z paczkami zgodnych rdzeni w grupie."""
    m = _load(account, platform, game)
    if _fold_groups(m):
        _save(account, platform, game, m)
        log.info("savesync: zapisy rdzeni → wspólna paczka grupy (%s/%s)", platform, game)
    return m


def _bundles(m: dict):
    """(emulator, rdzeń, rodzaj, węzeł) — wszystkie paczki manifestu (wszystkich slotów)."""
    for _n, emu, core, kind, b in _slot_bundles(m):
        yield emu, core, kind, b


def _slot_bundles(m: dict):
    """(slot, emulator, rdzeń, rodzaj, węzeł) — slot 1 pierwszy."""
    seen = set()
    for n in SLOTS:
        emus = _emus(m, n) if n != 1 else m.get("emulators")
        if not emus or id(emus) in seen:
            continue
        seen.add(id(emus))
        for emu, core, kind, b in _emus_bundles(emus):
            yield n, emu, core, kind, b


def _emus_bundles(emus: dict):
    for emu, e in emus.items():
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
    from emustart import savebundles
    platform, game = _id(platform, "platform_id"), _id(game, "game_id")
    with _lock_for(account, platform, game):
        m = _load_live(account, platform, game)
    out = json.loads(json.dumps(m))
    out["slots"] = {str(n): {"emulators": _emus(out, n) or {}} for n in SLOTS}    # slot 1 = `emulators`
    out["active_slot"] = int(out.get("active_slot") or 1)
    for n in SLOTS:
        cores = ((_emus(out, n) or {}).get("retroarch") or {}).get("cores") or {}
        for group, members in savebundles.SAVE_GROUPS.items():
            gb = ((cores.get(group) or {}).get("bundles") or {}).get("saves")
            if not gb:
                continue
            # klient sprzed 0.47 szuka zapisów pod swoim rdzeniem — ta sama paczka grupy
            for c in members:
                cores.setdefault(c, {}).setdefault("bundles", {})["saves"] = dict(gb, mirror=group)
    for emu, core, kind, b in _bundles(out):
        if b.get("mirror"):
            core = b["mirror"]
        for k in ("history", "file", "folded_into", "from_core"):
            b.pop(k, None)
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
    slot = slot_no(d.get("slot"))
    sha = str(d.get("sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise SyncError("nieprawidłowy sha256")
    size = int(d.get("size") or 0)
    if size <= 0 or size > MAX_BUNDLE:
        raise SyncError("nieprawidłowy rozmiar")
    base = int(d.get("base_revision", -1))
    with _lock_for(account, platform, game):
        b = _bundle(_load_live(account, platform, game), emu, core, kind, slot=slot) or {}
    if base != int(b.get("revision") or 0):
        raise Conflict(_public(b))           # szybciej niż po wysłaniu całej paczki
    uid = "upl_" + secrets.token_urlsafe(16)
    rec = {"upload_id": uid, "acc": account, "dev": device, "p": platform, "g": game, "e": emu, "c": core,
           "k": kind, "s": slot, "base": base, "sha256": sha, "size": size, "exp": time.time() + UPLOAD_TTL}
    _uploads().mkdir(parents=True, exist_ok=True)
    (_uploads() / f"{uid}.json").write_text(json.dumps(rec), encoding="utf-8")
    _prune_uploads()
    url = "/v1/files/u/" + make_token({"op": "put", "acc": account, "dev": device, "u": uid}, UPLOAD_TTL)
    _log("upload/init", account, device, platform, game, emu, core, kind, base, sha=sha, size=size, uid=uid, slot=slot)
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
        m = _load_live(account, p, g)
        slot = int(rec.get("s") or 1)
        b = _bundle(m, rec["e"], rec["c"], rec["k"], create=True, slot=slot)
        cur = int(b.get("revision") or 0)
        if not force and int(d.get("base_revision", -1)) != cur:
            _log("conflict", account, device, p, g, rec["e"], rec["c"], rec["k"], cur, uid=uid, slot=slot)
            raise Conflict(_public(b))
        _apply_removed(b, d, device)
        bid = _install(account, p, g, m, b, data, rec["sha256"], rec["size"], device)
    (_uploads() / f"{uid}.json").unlink(missing_ok=True)
    _log("upload/commit" + (" (wymuszone)" if force else ""), account, device, p, g, rec["e"], rec["c"],
         rec["k"], f"{cur}→{m['revision']}", sha=rec["sha256"], size=rec["size"], uid=uid, slot=slot)
    return {"ok": True, "revision": m["revision"], "bundle_id": bid}


def _apply_removed(b: dict, d: dict, device: str) -> None:
    """Stany świadomie usunięte / przenumerowane / przeniesione z tej paczki: `removed_add`
    [{sha256, name}] — ta treść pod tą nazwą; `removed_drop` — treść wraca pod tą nazwą
    (cofnięte usunięcie, powrót do numeru): [{sha256, name}] albo [sha256] (pod każdą nazwą).
    Najpierw zdjęcie, potem dopisanie. Sumowanie stanów z innego urządzenia (merge_states)
    nie przywraca stanów z listy."""
    add, drop = d.get("removed_add") or [], d.get("removed_drop") or []
    if not isinstance(add, list) or not isinstance(drop, list):
        raise SyncError("nieprawidłowe removed_add / removed_drop")
    drop_any = {str(x).lower() for x in drop if not isinstance(x, dict)}
    drop_pair = {(str(x.get("name") or ""), str(x.get("sha256") or "").lower()) for x in drop if isinstance(x, dict)}
    now = time.time()
    out = [r for r in (b.get("removed") or [])
           if r.get("sha256") not in drop_any and (r.get("name"), r.get("sha256")) not in drop_pair]
    have = {(r.get("name"), r.get("sha256")) for r in out}
    for r in add:
        sha = str(r.get("sha256") or "").lower() if isinstance(r, dict) else ""
        name = str(r.get("name") or "") if isinstance(r, dict) else ""
        if not re.fullmatch(r"[0-9a-f]{64}", sha) or not name or len(name) > 200:
            raise SyncError("nieprawidłowy wpis removed_add")
        if (name, sha) in have:
            continue
        have.add((name, sha))
        out.append({"sha256": sha, "name": name, "at": float(r.get("at") or now), "device": device})
    if out or b.get("removed"):
        b["removed"] = out[-KEEP_REMOVED:]


def removed_pairs(b: dict | None) -> set:
    """{(nazwa w paczce, SHA-256 pliku)} stanów świadomie usuniętych / przeniesionych z paczki."""
    return {(r.get("name"), r.get("sha256")) for r in (b or {}).get("removed") or [] if r.get("sha256")}


_PUBLIC = ("bundle_id", "revision", "sha256", "size", "file_count", "updated_at", "updated_by_device_id", "note")


def _install(account: str, p: str, g: str, m: dict, b: dict, src: Path, sha: str, size: int, device: str,
             move: bool = True, note: str = "", updated_at: float | None = None) -> str:
    """Nowa aktualna wersja paczki `b` z pliku `src` (wołający trzyma blokadę gry); poprzednia
    do historii (ostatnie KEEP_HISTORY). Zwraca bundle_id."""
    gdir = _game_dir(account, p, g)
    (gdir / "bundles").mkdir(parents=True, exist_ok=True)
    bid = "bnd_" + secrets.token_urlsafe(12)
    dst = gdir / "bundles" / f"{bid}.zip"
    if move:
        os.replace(src, dst)
    else:
        import shutil
        shutil.copyfile(src, dst)
    hist = list(b.get("history") or [])
    if b.get("bundle_id"):
        # poprzednia wersja zostaje (kopia zapasowa) — także przegrana przy konflikcie
        hist.insert(0, {k: b.get(k) for k in _PUBLIC if b.get(k) is not None})
    _prune_history(gdir, m, hist, b)
    with zipfile.ZipFile(dst) as z:
        nfiles = len(z.namelist())
    m["revision"] = int(m.get("revision") or 0) + 1
    now = time.time()
    removed = b.get("removed")
    b.clear()
    b.update(bundle_id=bid, revision=m["revision"], sha256=sha, size=size, file_count=nfiles,
             updated_at=updated_at or now, updated_by_device_id=device, history=hist[:KEEP_HISTORY])
    if note:
        b["note"] = note
    if removed:
        b["removed"] = removed
    m["updated_at"], m["updated_by_device_id"] = now, device
    _save(account, p, g, m)
    return bid


def _prune_history(gdir: Path, m: dict, hist: list, b: dict) -> None:
    """Pliki wersji wypadających z historii — tylko gdy żadna inna paczka ich nie używa
    (paczka grupy rdzeni dzieli plik z paczką rdzenia)."""
    used = set()
    for _e, _c, _k, bb in _bundles(m):
        if bb is b:
            continue
        used.add(bb.get("bundle_id"))
        used.update(h.get("bundle_id") for h in (bb.get("history") or []))
    keep = {h.get("bundle_id") for h in hist[:KEEP_HISTORY]}
    for old in hist[KEEP_HISTORY:]:
        if old.get("bundle_id") not in keep and old.get("bundle_id") not in used - {None}:
            (gdir / "bundles" / f"{old['bundle_id']}.zip").unlink(missing_ok=True)


def zip_content(p: Path) -> str:
    """Skrót zawartości paczki — jak savebundles.content_hash na urządzeniach (nazwy w paczce
    i SHA-256 plików), niezależny od czasów i kompresji ZIP-a."""
    with zipfile.ZipFile(p) as z:
        items = [i for i in z.infolist() if not i.is_dir()]
        if not items:
            return ""
        h = hashlib.sha256()
        for i in sorted(items, key=lambda x: x.filename):
            fh = hashlib.sha256()
            with z.open(i) as f:
                for buf in iter(lambda: f.read(CHUNK), b""):
                    fh.update(buf)
            h.update(i.filename.encode("utf-8") + b"\0" + fh.hexdigest().encode() + b"\n")
        return h.hexdigest()


def _content_of(gdir: Path, entry: dict) -> str:
    if entry.get("content"):
        return entry["content"]
    f = gdir / "bundles" / f"{entry.get('bundle_id')}.zip"
    try:
        return zip_content(f) if f.is_file() else ""
    except (OSError, zipfile.BadZipFile):
        return ""


def import_bundle(account: str, platform: str, game: str, emu: str, core: str, kind: str, src: Path,
                  device: str, note: str, updated_at: float) -> str:
    """Paczka spoza urządzeń (migracja zapisów sprzed 0.46): gdy paczki jeszcze nie ma — zostaje
    aktualną wersją; gdy jest — NIE nadpisuje jej, trafia do historii paczki z opisem (do
    przywrócenia w panelu). Ta sama zawartość już jest (bieżąca albo w historii) — nic.
    Zwraca "new" | "history" | "same"."""
    platform, game = _id(platform, "platform_id"), _id(game, "game_id")
    emu, core, kind = _key(emu, core, kind)
    why = check_zip(src)
    if why:
        raise SyncError(why)
    content = zip_content(src)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    with _lock_for(account, platform, game):
        m = _load_live(account, platform, game)
        b = _bundle(m, emu, core, kind, create=True)
        gdir = _game_dir(account, platform, game)
        if kind == "states" and b.get("bundle_id"):
            # stany się sumują: bieżące zostają w swoich slotach, starsze dochodzą jako nowe sloty
            return _merge_into(account, platform, game, m, b, gdir, src, device, note)
        if any(_content_of(gdir, e) == content for e in [b] + list(b.get("history") or []) if e.get("bundle_id")):
            return "same"
        if not b.get("bundle_id"):
            _install(account, platform, game, m, b, src, sha, src.stat().st_size, device, move=False,
                     note=note, updated_at=updated_at)
            b["content"] = content
            _save(account, platform, game, m)
            _log("import (nowa)", account, device, platform, game, emu, core, kind, b["revision"], sha=sha)
            return "new"
        (gdir / "bundles").mkdir(parents=True, exist_ok=True)
        bid = "bnd_" + secrets.token_urlsafe(12)
        import shutil
        shutil.copyfile(src, gdir / "bundles" / f"{bid}.zip")
        with zipfile.ZipFile(src) as z:
            nfiles = len(z.namelist())
        hist = list(b.get("history") or [])
        hist.insert(0, {"bundle_id": bid, "revision": 0, "sha256": sha, "size": src.stat().st_size,
                        "file_count": nfiles, "updated_at": updated_at, "updated_by_device_id": device,
                        "note": note, "content": content, "imported": True})
        _prune_history(gdir, m, hist, b)
        b["history"] = hist[:KEEP_HISTORY]
        _save(account, platform, game, m)
    _log("import (historia)", account, device, platform, game, emu, core, kind, b.get("revision"), sha=sha)
    return "history"


def _merge_into(account: str, platform: str, game: str, m: dict, b: dict, gdir: Path, src: Path,
                device: str, note: str) -> str:
    """Stany z `src` dołączone do bieżącej paczki stanów (savebundles.merge_states; bieżące
    wygrywają sloty, dołączane dostają wolne numery). Wołający trzyma blokadę gry."""
    import tempfile
    from emustart import savebundles
    cur = gdir / "bundles" / f"{b['bundle_id']}.zip"
    with zipfile.ZipFile(cur) as zc, zipfile.ZipFile(src) as zs:
        def index(z):
            return {i.filename: (hashlib.sha256(z.read(i)).hexdigest(), 0.0) for i in z.infolist() if not i.is_dir()}
        mine = {k: (v[0], 1.0) for k, v in index(zc).items()}          # bieżące zawsze wygrywają slot
        merged, _moves = savebundles.merge_states(mine, index(zs), removed_pairs(b))
        if set(merged) == set(mine) and all(merged[a][0] == "mine" for a in merged):
            return "same"
        fd, tmp = tempfile.mkstemp(prefix="emustart-stany-", suffix=".zip")
        os.close(fd)
        tmp = Path(tmp)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
                for arc, (side, name) in sorted(merged.items()):
                    z = zc if side == "mine" else zs
                    info = z.getinfo(name)
                    zi = zipfile.ZipInfo(arc, info.date_time)
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    out.writestr(zi, z.read(name))
            sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
            _install(account, platform, game, m, b, tmp, sha, tmp.stat().st_size, device, move=False,
                     note=note + " — dołączone do bieżących stanów")
        finally:
            tmp.unlink(missing_ok=True)
    _log("import (stany dołączone)", account, device, platform, game, "", "", "states", b.get("revision"))
    return "merged"


def restore(account: str, platform, game, emu: str, core: str, kind: str, bundle_id: str, by: str,
            slot=1) -> dict:
    """Wersja z historii paczki → nowa aktualna wersja (bieżąca do historii). Urządzenia
    pobiorą ją przy następnej grze (bez zmian u siebie) albo zobaczą konflikt."""
    platform, game = _id(platform, "platform_id"), _id(game, "game_id")
    emu, core, kind = _key(emu, core, kind)
    slot = slot_no(slot)
    with _lock_for(account, platform, game):
        m = _load_live(account, platform, game)
        b = _bundle(m, emu, core, kind, slot=slot)
        hit = next((h for h in (b or {}).get("history") or [] if h.get("bundle_id") == bundle_id), None)
        if not hit:
            raise SyncError("nie ma takiej wersji w historii")
        gdir = _game_dir(account, platform, game)
        src = gdir / "bundles" / f"{bundle_id}.zip"
        if not src.is_file():
            raise SyncError("plik tej wersji już nie istnieje")
        note = "przywrócone: " + (hit.get("note") or f"wersja {hit.get('revision')}")
        _install(account, platform, game, m, b, src, hit["sha256"], hit["size"], by, move=False, note=note)
        rev = b["revision"]
    _log("restore", account, by, platform, game, emu, core, kind, rev, slot=slot)
    return {"ok": True, "revision": rev}


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


def set_active(account: str, device: str, platform, game, d: dict) -> dict:
    """Aktywny slot gry (wspólny dla urządzeń konta). Zmiana zrobiona wcześniej (`at`, np. offline)
    niż już zapisana — nie wygrywa; odpowiedź zawsze z bieżącym slotem."""
    platform, game = _id(platform, "platform_id"), _id(game, "game_id")
    slot = slot_no(d.get("slot"))
    try:
        at = float(d.get("at") or time.time())
    except (TypeError, ValueError):
        raise SyncError("nieprawidłowy czas")
    at = min(at, time.time())
    with _lock_for(account, platform, game):
        m = _load_live(account, platform, game)
        if at >= float(m.get("active_at") or 0):
            m.update(active_slot=slot, active_at=at, active_by=device)
            _save(account, platform, game, m)
            _log("slot", account, device, platform, game, "", "", "", m.get("revision"), slot=slot)
        return {"ok": True, "active_slot": int(m.get("active_slot") or 1), "active_at": m.get("active_at") or 0}


def _public(b: dict) -> dict:
    return {k: b.get(k) for k in _PUBLIC if k != "note" or b.get(k)}


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
    _fold_groups(m)
    return [{"emulator_id": e, "core_id": c, "bundle_kind": k, "slot": n, **_public(b),
             "history": b.get("history") or []}
            for n, e, c, k, b in _slot_bundles(m)]


def _log(action: str, account: str, device: str, platform: str, game: str, emu: str, core: str, kind: str,
         rev, sha: str = "", size: int = 0, uid: str = "", slot: int = 1) -> None:
    # bez adresów i tokenów — tylko identyfikatory
    log.info("savesync %s: konto=%s urządzenie=%s %s/%s%s %s/%s/%s rev=%s%s%s%s", action, account, device or "-",
             platform, game, f" slot {slot}" if slot != 1 else "", emu or "-", core or "-", kind or "-", rev,
             f" sha={sha[:12]}" if sha else "",
             f" rozmiar={size}" if size else "", f" {uid}" if uid else "")
