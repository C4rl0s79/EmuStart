"""accounts — hasła kont na serwerze EmuStart (0.40).

Konto = profil gracza w folderze profili. Nowe konto powstaje tylko z zaproszenia (0.49:
logowanie Google na stronie serwera — emustart/webportal.py) albo w panelu serwera. Hasło
konta (opcjonalne — ustawiane na stronie serwera albo w aplikacji) potwierdza użytkownika:
„Mam konto” przy dołączaniu nowego urządzenia (nadal czeka na zatwierdzenie) i gdy na
urządzeniu gra inny użytkownik niż właściciel.
Urządzenie przypisane do konta nie podaje hasła przy każdym zapytaniu — loguje się kluczem
urządzenia (emustart/enroll.py). PIN jest tylko lokalną blokadą profilu na urządzeniu.

Skrót hasła: scrypt (N=2^15, r=8, p=1, losowa sól 16 B), w danych serwera
(`accounts.json` obok `devices.json`) — nie w folderze profili na udziale sieciowym. Serwer
nigdy go nie wydaje. Złe hasła: rosnące opóźnienie na konto i limit na adres.

Token konta (nagłówek X-Profile-Token) = HMAC(sekret serwera, konto + skrót hasła): zmiana
hasła unieważnia tokeny na wszystkich urządzeniach.

Administrator (0.43) to cecha konta, nie urządzenia: wszystkie urządzenia konta administratora
widzą wszystkie konta i zatwierdzają nowe. Ustawiana w panelu serwera; konto zalogowane
pierwszy raz kodem QR z panelu, gdy żadne nie jest administratorem, zostaje nim samo.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time

from emustart import paths

log = logging.getLogger("emustart.accounts")

MIN_LEN = 10
MAX_LEN = 128
N, R, P = 2 ** 15, 8, 1               # scrypt: ok. 100 ms na sprawdzenie
FREE_TRIES = 3                        # tyle złych haseł bez czekania (na konto)
MAX_WAIT = 600.0
IP_WINDOW, IP_MAX = 600.0, 20         # złe hasła z jednego adresu (wszystkie konta) w 10 min

RULES = "Hasło: co najmniej 10 znaków, w tym wielka litera, cyfra i znak specjalny."

_lock = threading.Lock()
_kdf = threading.BoundedSemaphore(2)  # scrypt naraz — zalew zapytań nie wyczerpie pamięci
_db: dict | None = None               # konto (małe litery) → rekord
_fails: dict = {}                     # konto (małe litery) → (liczba, czas ostatniej)
_ip_fails: dict = {}                  # adres → [czasy złych prób]


def policy(pw: str) -> str:
    """'' = hasło spełnia wymagania; inaczej powód (po polsku, do pokazania)."""
    pw = pw or ""
    missing = []
    if len(pw) < MIN_LEN:
        missing.append(f"co najmniej {MIN_LEN} znaków")
    if not re.search(r"[A-ZĄĆĘŁŃÓŚŹŻ]", pw):
        missing.append("wielka litera")
    if not re.search(r"\d", pw):
        missing.append("cyfra")
    if not re.search(r"[^\w\s]|_", pw):
        missing.append("znak specjalny")
    if len(pw) > MAX_LEN:
        return f"Hasło może mieć najwyżej {MAX_LEN} znaków."
    return ("Hasło musi mieć: " + ", ".join(missing) + ".") if missing else ""


def make(pw: str) -> dict:
    why = policy(pw)
    if why:
        raise ValueError(why)
    salt = secrets.token_bytes(16)
    with _kdf:
        h = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=32, maxmem=64 << 20)
    return {"v": 1, "alg": "scrypt", "n": N, "r": R, "p": P, "salt": salt.hex(), "hash": h.hex(),
            "set": time.time()}


def _check(rec: dict, pw: str) -> bool:
    try:
        salt, want = bytes.fromhex(rec["salt"]), bytes.fromhex(rec["hash"])
        n, r, p = int(rec.get("n", N)), int(rec.get("r", R)), int(rec.get("p", P))
    except (KeyError, ValueError, TypeError):
        return False
    if not pw or len(pw) > MAX_LEN:
        return False
    with _kdf:
        got = hashlib.scrypt(pw.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=len(want), maxmem=64 << 20)
    return hmac.compare_digest(got, want)


# ── zapis ──
def _path():
    return paths.SERVER / "accounts.json"


def _load() -> dict:
    global _db
    if _db is None:
        try:
            _db = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _db = {}
    return _db


def _save() -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(_load(), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def reset() -> None:
    """Dla testów."""
    global _db
    with _lock:
        _db = None
        _fails.clear()
        _ip_fails.clear()


def record(name: str) -> dict | None:
    with _lock:
        rec = _load().get((name or "").lower())
    return dict(rec) if rec else None


def has_password(name: str) -> bool:
    return bool((record(name) or {}).get("hash"))


def names_with_password() -> set:
    with _lock:
        return {k for k, r in _load().items() if r.get("hash")}


def set_record(name: str, rec: dict, by: str = "") -> None:
    """Zapisuje gotowy skrót (np. z prośby o zalogowanie — hasło nie jest tam trzymane);
    flaga administratora zostaje."""
    with _lock:
        old = _load().get(name.lower()) or {}
        _load()[name.lower()] = {**old, **rec, "name": name}
        _save()
        _fails.pop(name.lower(), None)
    log.info("hasło konta %s ustawione%s", name, f" — {by}" if by else "")


def is_admin(name: str) -> bool:
    return bool(name) and bool((record(name) or {}).get("admin"))


def any_admin() -> bool:
    with _lock:
        return any(r.get("admin") for r in _load().values())


def set_admin(name: str, admin: bool, by: str = "") -> None:
    with _lock:
        rec = _load().setdefault(name.lower(), {"name": name})
        rec["admin"] = bool(admin)
        _save()
    log.info("konto %s %s administratorem%s", name, "jest" if admin else "nie jest", f" — {by}" if by else "")


def set_password(name: str, pw: str, by: str = "") -> str:
    """'' = ustawione; inaczej powód."""
    try:
        rec = make(pw)
    except ValueError as ex:
        return str(ex)
    set_record(name, rec, by)
    return ""


def create(name: str, by: str = "") -> None:
    """Rekord konta bez hasła (konto z zaproszenia — logowanie Google, kod ze strony)."""
    with _lock:
        _load().setdefault(name.lower(), {"name": name})
        _save()
    log.info("konto %s założone%s", name, f" — {by}" if by else "")


# ── konto Google (emustart/webportal.py) ──
def link_google(name: str, sub: str, email: str) -> None:
    """Konto ↔ konto Google: stały identyfikator Google (`sub`), e-mail tylko do wyświetlenia."""
    with _lock:
        for r in _load().values():
            if r.get("google_sub") == sub and r.get("name", "").lower() != name.lower():
                for k in ("google_sub", "google_email", "google_at"):
                    r.pop(k, None)
        rec = _load().setdefault(name.lower(), {"name": name})
        rec.update(google_sub=sub, google_email=email.lower(), google_at=time.time())
        _save()
    log.info("konto %s połączone z kontem Google %s", name, email)


def by_google(sub: str) -> str:
    """Nazwa konta połączonego z kontem Google `sub` ('' — żadne)."""
    with _lock:
        return next((r.get("name", k) for k, r in _load().items() if sub and r.get("google_sub") == sub), "")


def unlink_google(name: str, by: str = "") -> bool:
    with _lock:
        rec = _load().get((name or "").lower())
        if not rec or not rec.get("google_sub"):
            return False
        for k in ("google_sub", "google_email", "google_at"):
            rec.pop(k, None)
        _save()
    log.info("konto %s odłączone od konta Google%s", name, f" — {by}" if by else "")
    return True


def rename(old: str, new: str) -> None:
    with _lock:
        rec = _load().pop(old.lower(), None)
        if rec:
            _load()[new.lower()] = {**rec, "name": new}
            _save()


# ── sprawdzanie z opóźnieniem ──
def wait_left(name: str, ip: str = "") -> float:
    now = time.time()
    with _lock:
        n, last = _fails.get(name.lower(), (0, 0.0))
        recent = [t for t in _ip_fails.get(ip, []) if now - t < IP_WINDOW] if ip else []
    acc = 0.0 if n < FREE_TRIES else max(0.0, last + min(MAX_WAIT, 2.0 ** (n - FREE_TRIES + 1)) - now)
    by_ip = max(0.0, recent[0] + IP_WINDOW - now) if len(recent) >= IP_MAX else 0.0
    return max(acc, by_ip)


def _failed(name: str, ip: str) -> None:
    now = time.time()
    with _lock:
        n, _t = _fails.get(name.lower(), (0, 0.0))
        _fails[name.lower()] = (n + 1, now)
        if ip:
            _ip_fails[ip] = [t for t in _ip_fails.get(ip, []) if now - t < IP_WINDOW] + [now]


def verify(name: str, pw: str, ip: str = "") -> tuple[bool, str, float]:
    """(dobre hasło?, powód, ile czekać). Konto bez hasła: (False, "brak", 0)."""
    left = wait_left(name, ip)
    if left > 0:
        return False, f"Za dużo złych haseł — spróbuj za {int(left) + 1} s.", left
    rec = record(name)
    if not (rec or {}).get("hash"):
        return False, "brak", 0.0
    if _check(rec, pw):
        with _lock:
            _fails.pop(name.lower(), None)
        return True, "", 0.0
    _failed(name, ip)
    log.warning("złe hasło konta %s (%s)", name, ip or "?")
    return False, "Złe hasło.", wait_left(name, ip)


# ── token konta (inny użytkownik na urządzeniu) ──
def token(secret: str, name: str) -> str:
    rec = record(name)
    if not (rec or {}).get("hash"):
        return ""
    msg = ("konto\n" + name.lower() + "\n" + rec.get("hash", "")).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def token_ok(secret: str, name: str, got: str) -> bool:
    want = token(secret, name)
    return bool(got and want) and hmac.compare_digest(want, got)
