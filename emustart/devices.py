"""devices — urządzenia sparowane z serwerem EmuStart (etap 1 wersji 2.0).

Każdy komputer i telefon ma **własny klucz** (zamiast jednego klucza serwera dla wszystkich):
widać go w panelu serwera z nazwą, klasą (pc / phone), wersją EmuStart i czasem ostatniego
kontaktu, a unieważnienie odcina tylko to jedno urządzenie.

Parowanie: panel serwera → „Dodaj urządzenie” → jednorazowy kod (ważny 10 minut) pokazany
jako tekst i kod QR (`emustart://pair?u=<adres>&c=<kod>`). Urządzenie wysyła kod do
`POST /v1/pair` i dostaje swój klucz. Serwer trzyma tylko skrót klucza (SHA-256), więc
plik `devices.json` nie zdradza kluczy.

Rejestr: `<katalog serwera>\\devices.json` (paths.SERVER). Od 0.39 urządzenia łączą się
tylko kluczem urządzenia (klucz publiczny w rejestrze, logowanie podpisem — emustart/enroll.py);
wspólny klucz serwera (`server_token`) działa wyłącznie na komputerze serwera.
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
        _reqs.clear()


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


def _pubkey_ok(pubkey: str) -> bool:
    from emustart import enroll
    try:
        enroll.verify_code(pubkey)
        return True
    except Exception:
        return False


def pair(code: str, name: str, cls: str, ip: str = "", version: str = "", hw: str = "",
         pubkey: str = "") -> dict:
    """Jednorazowy kod z panelu + klucz publiczny urządzenia → urządzenie z kluczem (logowanie
    podpisem). {"ok", "id"} albo {"ok": False, "reason"}. Kluczy-haseł serwer już nie wydaje."""
    if not _pubkey_ok(pubkey):
        return {"ok": False, "reason": "Zaktualizuj EmuStart — parowanie wymaga klucza urządzenia (wersja 0.39 lub nowsza)."}
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
        did = _add_keyed_locked(name, cls, ip, version, pubkey, now, hw=hw)
    return {"ok": True, "id": did}


def _add_keyed_locked(name: str, cls: str, ip: str, version: str, pubkey: str, now: float,
                      by: str = "", hw: str = "") -> str:
    """Urządzenie z kluczem i pełnym dostępem (kod z panelu / zatwierdzenie na urządzeniu
    administratora — jak dawniej). Wołający trzyma _lock."""
    did = secrets.token_hex(6)
    _load()[did] = {"name": (str(name).strip() or "urządzenie")[:40], "class": cls if cls in CLASSES else "pc",
                    "pubkey": pubkey, "role": "admin", "created": now, "last_seen": now, "ip": ip,
                    "version": version}
    if by:
        _load()[did]["approved_by"] = by
    if _valid_hw(hw):
        _load()[did]["hw"] = _valid_hw(hw)
        replace_same_hw(did, hw)
    _save()
    log.info("sparowano urządzenie %s (%s, %s, klucz urządzenia)%s", name, cls, ip, f" — zatwierdził {by}" if by else "")
    return did


def upgrade(did: str, pubkey: str, hw: str = "") -> dict:
    """Urządzenie ze starym kluczem-hasłem przechodzi na klucz urządzenia: ten sam wpis (konto,
    rola, nazwa), stary klucz przestaje działać. Dawne urządzenia miały pełny dostęp — rola admin."""
    if not _pubkey_ok(pubkey):
        return {"ok": False, "reason": "zły klucz urządzenia"}
    with _lock:
        d = _load().get(did)
        if not d or d.get("revoked"):
            return {"ok": False, "reason": "nieznane urządzenie"}
        if d.get("pubkey"):
            return {"ok": False, "reason": "urządzenie ma już klucz"}
        d["pubkey"] = pubkey
        d.pop("key", None)
        d.setdefault("role", "admin")
        if _valid_hw(hw):
            d["hw"] = _valid_hw(hw)
            replace_same_hw(did, hw)
        d["upgraded"] = time.time()
        _save()
    log.info("urządzenie %s przeszło na klucz urządzenia", d.get("name"))
    return {"ok": True, "id": did}


def is_legacy(did: str) -> bool:
    """Wpis ze starym kluczem-hasłem (bez klucza urządzenia)."""
    with _lock:
        d = _load().get(did) or {}
    return bool(d) and not d.get("pubkey")


# ── prośba o dołączenie, zatwierdzana na sparowanym urządzeniu (jak logowanie telewizora) ──
# Nowe urządzenie: POST /v1/pair/request → kod + QR na jego ekranie; telefon (albo panel
# serwera) zatwierdza kod; nowe urządzenie odpytuje /v1/pair/poll i odbiera swój klucz.
REQ_TTL = 600
MAX_PENDING_IP = 5
_reqs: dict = {}                 # kod → prośba


def _gc(now: float) -> None:
    for c, r in list(_reqs.items()):
        if r["expires"] < now:
            _reqs.pop(c, None)


def request(name: str, cls: str, ip: str = "", version: str = "", pubkey: str = "", hw: str = "") -> dict:
    """Nowe urządzenie prosi o dołączenie → {"ok", "rid" (tajny, do odpytywania), "code", "pretty"}."""
    if not _pubkey_ok(pubkey):
        return {"ok": False, "reason": "Zaktualizuj EmuStart — parowanie wymaga klucza urządzenia (wersja 0.39 lub nowsza)."}
    now = time.time()
    with _lock:
        _gc(now)
        if sum(1 for r in _reqs.values() if r["ip"] == ip and r["status"] == "pending") >= MAX_PENDING_IP:
            return {"ok": False, "reason": "Za dużo oczekujących próśb z tego adresu — spróbuj za kilka minut."}
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
        rid = secrets.token_urlsafe(24)
        _reqs[code] = {"rid": rid, "name": (str(name).strip() or "urządzenie")[:40],
                       "class": cls if cls in CLASSES else "pc", "ip": ip, "version": version,
                       "created": now, "expires": now + REQ_TTL, "status": "pending", "key": "", "id": "",
                       "pubkey": pubkey, "hw": _valid_hw(hw)}
    log.info("prośba o dołączenie: %s (%s, %s)", name, cls, ip)
    return {"ok": True, "rid": rid, "code": code, "pretty": f"{code[:4]}-{code[4:]}", "expires": now + REQ_TTL}


def poll(rid: str) -> dict:
    """Stan prośby: pending / approved (+ klucz, oddawany raz) / denied / expired."""
    now = time.time()
    with _lock:
        _gc(now)
        for c, r in _reqs.items():
            if rid and hmac.compare_digest(r["rid"], str(rid)):
                if r["status"] == "pending":
                    return {"ok": True, "status": "pending"}
                _reqs.pop(c, None)                # klucz i odmowa — tylko raz
                if r["status"] == "approved":
                    return {"ok": True, "status": "approved", "id": r["id"]}
                return {"ok": True, "status": "denied"}
    return {"ok": True, "status": "expired"}


def pending(code: str) -> dict | None:
    """Prośba czekająca na zatwierdzenie (do pokazania: nazwa, klasa, adres) — bez sekretów."""
    with _lock:
        _gc(time.time())
        r = _reqs.get(_norm_code(code))
        if not r or r["status"] != "pending":
            return None
        return {k: r[k] for k in ("name", "class", "ip", "version", "created", "expires")}


def pending_list() -> list:
    with _lock:
        _gc(time.time())
        return [{"code": c, "pretty": f"{c[:4]}-{c[4:]}", **{k: r[k] for k in ("name", "class", "ip", "created")}}
                for c, r in _reqs.items() if r["status"] == "pending"]


def approve(code: str, allow: bool, by: str = "") -> dict:
    """Zatwierdzenie (albo odrzucenie) prośby kodem — na sparowanym urządzeniu lub w panelu."""
    now = time.time()
    with _lock:
        _gc(now)
        c = _norm_code(code)
        r = _reqs.get(c)
        if not r or r["status"] != "pending":
            return {"ok": False, "reason": "Nie ma takiej prośby (kod wygasł albo już użyty)."}
        if not allow:
            r["status"] = "denied"
            log.info("odrzucono prośbę o dołączenie: %s (%s)", r["name"], r["ip"])
            return {"ok": True, "status": "denied", "name": r["name"]}
        r["id"] = _add_keyed_locked(r["name"], r["class"], r["ip"], r["version"], r["pubkey"], now, by, r.get("hw", ""))
        r["status"] = "approved"
        return {"ok": True, "status": "approved", "name": r["name"], "id": r["id"]}


def name_of(did: str) -> str:
    with _lock:
        return (_load().get(did) or {}).get("name", "") if did else "panel serwera"


def approve_uri(base_url: str, code: str) -> str:
    """QR na ekranie nowego urządzenia: skanuje go sparowany telefon."""
    from urllib.parse import urlencode
    return "emustart://approve?" + urlencode({"u": base_url, "c": code})


def verify(key: str) -> str:
    """Id urządzenia dla klucza albo tokenu sesji (logowanie podpisem, emustart/enroll.py);
    '' = nieznany / unieważniony / wygasły."""
    if not key:
        return ""
    from emustart import enroll
    did = enroll.token_device(key)
    if did:
        with _lock:
            d = _load().get(did)
        return did if d and not d.get("revoked") else ""
    h = _h(key)
    with _lock:
        for did, d in _load().items():
            if not d.get("revoked") and hmac.compare_digest(d.get("key", ""), h):
                return did
    return ""


def _valid_hw(hw: str) -> str:
    hw = (hw or "").strip().lower()
    return hw if len(hw) == 32 and all(c in "0123456789abcdef" for c in hw) else ""


def replace_same_hw(did: str, hw: str) -> list:
    """Jedna rejestracja na sprzęt: inne aktywne rejestracje tego samego sprzętu (ponowne
    dołączenie, nowa instalacja aplikacji) są unieważniane. Wołający trzyma _lock. Zwraca nazwy.
    Bez sięgania po blokadę próśb (emustart/enroll.py) — odwrotna kolejność blokad niż w
    enroll.pending / decide groziłaby zakleszczeniem; sesji unieważnionych i tak nie przyjmie
    `verify` (sprawdza `revoked`)."""
    hw = _valid_hw(hw)
    if not hw:
        return []
    gone = []
    for other, d in _load().items():
        if other != did and not d.get("revoked") and d.get("hw") == hw:
            d["revoked"] = time.time()
            d["replaced_by"] = did
            gone.append(d.get("name", other))
    if gone:
        log.info("ten sam sprzęt — zastąpione rejestracje: %s", ", ".join(gone))
    return gone


def same_hw(hw: str) -> list:
    """Nazwy aktywnych rejestracji tego sprzętu (do prośby o dołączenie: „zastąpi …”)."""
    hw = _valid_hw(hw)
    with _lock:
        return [d.get("name", k) for k, d in _load().items() if hw and not d.get("revoked") and d.get("hw") == hw]


def touch(did: str, ip: str = "", version: str = "", hw: str = "") -> None:
    """Ostatni kontakt urządzenia (na dysk najwyżej co SEEN_SAVE s). `hw` — skrót
    identyfikatora sprzętu: zapamiętywany raz, gdy rejestracja go jeszcze nie ma (sprzed
    0.38.2). Bez unieważniania innych rejestracji — nagłówek ustawia samo urządzenie, więc
    dowolne urządzenie mogłoby tak odciąć cudze; zastępowanie robi tylko dołączanie
    (parowanie / prośba, którą ktoś zatwierdza i widzi „zastąpi: …”)."""
    global _dirty_at
    with _lock:
        d = _load().get(did)
        if not d:
            return
        now = time.time()
        hw = _valid_hw(hw)
        if hw and not d.get("hw"):
            d["hw"] = hw
            _save()
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
        out = [{"id": did, **{k: v for k, v in d.items() if k not in ("key", "pubkey")},
                "keyed": bool(d.get("pubkey")), "admin": _is_admin(d)} for did, d in _load().items()]
    out.sort(key=lambda d: -d.get("last_seen", 0))
    return out


def revoke(did: str) -> bool:
    with _lock:
        d = _load().get(did)
        if not d:
            return False
        d["revoked"] = time.time()
        _save()
    from emustart import enroll
    enroll.drop_tokens(did)
    log.info("unieważniono urządzenie %s", d.get("name"))
    return True


# ── konta i role (emustart/enroll.py) ──
def _is_admin(d: dict) -> bool:
    # urządzenia sprzed 0.37 (bez konta i klucza publicznego) — pełny dostęp jak dotąd
    return d.get("role") == "admin" or (not d.get("account") and not d.get("pubkey"))


def is_admin(did: str) -> bool:
    if not did:
        return True                    # wspólny klucz serwera / panel
    with _lock:
        d = _load().get(did)
    return bool(d) and not d.get("revoked") and _is_admin(d)


def account_of(did: str) -> str:
    """Konto, do którego urządzenie jest ograniczone ('' = wszystkie)."""
    if not did:
        return ""
    with _lock:
        d = _load().get(did) or {}
    return "" if _is_admin(d) else d.get("account", "")


def games_ok(did: str) -> bool:
    with _lock:
        d = _load().get(did) or {}
    return _is_admin(d) or bool(d.get("games"))


def of_account(account: str) -> list:
    """Aktywne urządzenia konta."""
    with _lock:
        return [did for did, d in _load().items()
                if not d.get("revoked") and (d.get("account") or "").lower() == account.lower()]


def pubkey_of(did: str) -> str:
    with _lock:
        d = _load().get(did) or {}
    return "" if d.get("revoked") else d.get("pubkey", "")


def add_keyed(name: str, cls: str, ip: str, version: str, pubkey: str, account: str,
              role: str = "user", games: bool = False, by: str = "", hw: str = "") -> str:
    """Urządzenie z kluczem publicznym (logowanie podpisem), przypisane do konta."""
    did = secrets.token_hex(6)
    now = time.time()
    with _lock:
        _load()[did] = {"name": (str(name).strip() or "urządzenie")[:40], "class": cls if cls in CLASSES else "pc",
                        "pubkey": pubkey, "account": account, "role": role if role in ("admin", "user") else "user",
                        "games": bool(games), "created": now, "last_seen": now, "ip": ip, "version": version,
                        "approved_by": by}
        if _valid_hw(hw):
            _load()[did]["hw"] = _valid_hw(hw)
            replace_same_hw(did, hw)
        _save()
    return did


def set_access(did: str, role: str | None = None, games: bool | None = None) -> bool:
    with _lock:
        d = _load().get(did)
        if not d:
            return False
        if role in ("admin", "user"):
            d["role"] = role
        if games is not None:
            d["games"] = bool(games)
        _save()
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
