"""devices — urządzenia sparowane z serwerem EmuStart (etap 1 wersji 2.0).

Każdy komputer i telefon ma **własny klucz** (zamiast jednego klucza serwera dla wszystkich):
widać go w panelu serwera z nazwą, klasą (pc / phone), wersją EmuStart i czasem ostatniego
kontaktu, a unieważnienie odcina tylko to jedno urządzenie.

Parowanie: panel serwera → „Dodaj urządzenie” → jednorazowy kod (ważny 10 minut) pokazany
jako tekst i kod QR (`emustart://pair?u=<adres>&c=<kod>`). Urządzenie wysyła kod do
`POST /v1/pair` i dostaje swój klucz. Serwer trzyma tylko skrót klucza (SHA-256), więc
plik `devices.json` nie zdradza kluczy.

Rejestr: `<katalog serwera>\\devices.json` (paths.SERVER). Stary wspólny klucz serwera
(`server_token`) działa dalej — dla urządzeń sprzed parowania.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time

from emustart import paths

log = logging.getLogger("emustart.devices")

PAIR_TTL = 600                   # kod parowania ważny tyle s
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # bez 0/O, 1/I — do przepisania
MAX_FAILS = 10                   # tyle błędnych kodów z jednego adresu → przerwa
FAIL_WINDOW = 600
SEEN_SAVE = 60                   # ostatni kontakt zapisywany na dysk najwyżej co tyle s
CLASSES = ("pc", "phone")

_lock = threading.Lock()
_codes: dict = {}                # kod → czas wygaśnięcia
_fails: dict = {}                # ip → [czasy błędnych prób]
_cache: dict | None = None
_dirty_at = 0.0


def _path():
    return paths.SERVER / "devices.json"


def _h(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _save() -> None:
    global _dirty_at
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(_load(), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    _dirty_at = 0.0


def reset_cache() -> None:
    """Dla testów i po zmianie katalogu serwera."""
    global _cache
    with _lock:
        _cache = None
        _codes.clear()
        _fails.clear()


def new_code() -> dict:
    """Jednorazowy kod parowania (panel serwera)."""
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
    with _lock:
        now = time.time()
        for c, exp in list(_codes.items()):
            if exp < now:
                _codes.pop(c, None)
        _codes[code] = now + PAIR_TTL
    return {"code": code, "pretty": f"{code[:4]}-{code[4:]}", "expires": now + PAIR_TTL}


def _norm_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch in PAIR_ALPHABET)


def pair(code: str, name: str, cls: str, ip: str = "", version: str = "") -> dict:
    """Wymiana jednorazowego kodu na klucz urządzenia. {"ok", "id", "key"} albo {"ok": False, "reason"}."""
    now = time.time()
    with _lock:
        fails = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW]
        _fails[ip] = fails
        if len(fails) >= MAX_FAILS:
            return {"ok": False, "reason": "Za dużo błędnych kodów — spróbuj za kilka minut."}
        c = _norm_code(code)
        exp = _codes.get(c)
        if not exp or exp < now:
            fails.append(now)
            return {"ok": False, "reason": "Nieprawidłowy albo wygasły kod — wygeneruj nowy w panelu serwera."}
        _codes.pop(c, None)                       # kod jednorazowy
        key = secrets.token_urlsafe(32)
        did = secrets.token_hex(6)
        devs = _load()
        devs[did] = {"name": (str(name).strip() or "urządzenie")[:40],
                     "class": cls if cls in CLASSES else "pc",
                     "key": _h(key), "created": now, "last_seen": now, "ip": ip, "version": version}
        _save()
    log.info("sparowano urządzenie %s (%s, %s)", devs[did]["name"], devs[did]["class"], ip)
    return {"ok": True, "id": did, "key": key}


def verify(key: str) -> str:
    """Id urządzenia dla klucza ('' = nieznany / unieważniony)."""
    if not key:
        return ""
    h = _h(key)
    with _lock:
        for did, d in _load().items():
            if not d.get("revoked") and hmac.compare_digest(d.get("key", ""), h):
                return did
    return ""


def touch(did: str, ip: str = "", version: str = "") -> None:
    """Ostatni kontakt urządzenia (na dysk najwyżej co SEEN_SAVE s)."""
    global _dirty_at
    with _lock:
        d = _load().get(did)
        if not d:
            return
        now = time.time()
        d["last_seen"] = now
        if ip:
            d["ip"] = ip
        if version:
            d["version"] = version
        if not _dirty_at:
            _dirty_at = now
        if now - _dirty_at >= SEEN_SAVE or d.get("_new"):
            d.pop("_new", None)
            _save()


def flush() -> None:
    with _lock:
        if _dirty_at:
            _save()


def listing() -> list:
    with _lock:
        out = [{"id": did, **{k: v for k, v in d.items() if k != "key"}} for did, d in _load().items()]
    out.sort(key=lambda d: -d.get("last_seen", 0))
    return out


def revoke(did: str) -> bool:
    with _lock:
        d = _load().get(did)
        if not d:
            return False
        d["revoked"] = time.time()
        _save()
    log.info("unieważniono urządzenie %s", d.get("name"))
    return True


def rename(did: str, name: str) -> bool:
    with _lock:
        d = _load().get(did)
        if not d or not str(name).strip():
            return False
        d["name"] = str(name).strip()[:40]
        _save()
    return True


def pair_uri(base_url: str, code: str) -> str:
    """Zawartość kodu QR: adres serwera i kod jednorazowy."""
    from urllib.parse import urlencode
    return "emustart://pair?" + urlencode({"u": base_url, "c": code})
