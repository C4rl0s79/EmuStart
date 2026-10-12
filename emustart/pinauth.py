"""pinauth — PIN profilu na urządzeniu (od 0.40 tylko lokalny) i nazwy profili.

PIN (4–8 cyfr) to blokada profilu na jednym urządzeniu: skrót scrypt z losową solą leży
w bazie programu (emustart/profiles.py: local_pin_*), serwer go nie zna. Złe PIN-y: rosnące
opóźnienie. Konto na serwerze chroni hasło (emustart/accounts.py).

`AUTH` — nazwa dawnego pliku PIN-u w folderze profilu (0.27–0.39): serwer go nie wydaje.
`find` — nazwy profili są unikalne bez względu na wielkość liter.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time

AUTH = ".auth.json"
PIN_RE = re.compile(r"^\d{4,8}$")
N, R, P = 2 ** 14, 8, 1               # scrypt: ok. 50 ms na sprawdzenie
FREE_TRIES = 3                        # tyle złych PIN-ów bez czekania
MAX_WAIT = 300.0

_fails: dict = {}                     # profil (małe litery) → (liczba, czas ostatniej)
_lock = threading.Lock()


def valid_pin(pin: str) -> bool:
    return bool(PIN_RE.match(pin or ""))


def make(pin: str) -> dict:
    if not valid_pin(pin):
        raise ValueError("PIN to 4–8 cyfr.")
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pin.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return {"v": 1, "salt": salt.hex(), "hash": h.hex(), "set": time.time()}


def check(rec: dict, pin: str) -> bool:
    try:
        salt = bytes.fromhex(rec["salt"])
        want = bytes.fromhex(rec["hash"])
    except (KeyError, ValueError, TypeError):
        return False
    if not valid_pin(pin):
        return False
    got = hashlib.scrypt(pin.encode(), salt=salt, n=N, r=R, p=P, dklen=len(want))
    return hmac.compare_digest(got, want)


# ── złe PIN-y: opóźnienie ──
def wait_left(profile: str) -> float:
    """Ile s jeszcze czekać przed następną próbą (0 = można)."""
    with _lock:
        n, last = _fails.get(profile.lower(), (0, 0.0))
    if n < FREE_TRIES:
        return 0.0
    wait = min(MAX_WAIT, 2.0 ** (n - FREE_TRIES + 1))
    return max(0.0, last + wait - time.time())


def failed(profile: str) -> None:
    with _lock:
        n, _t = _fails.get(profile.lower(), (0, 0.0))
        _fails[profile.lower()] = (n + 1, time.time())


def succeeded(profile: str) -> None:
    with _lock:
        _fails.pop(profile.lower(), None)


def find(names, wanted: str) -> str:
    """Istniejący profil o tej nazwie bez względu na wielkość liter ('' = nie ma)."""
    w = wanted.strip().lower()
    return next((n for n in names if n.lower() == w), "")
