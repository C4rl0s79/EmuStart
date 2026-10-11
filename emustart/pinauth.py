"""pinauth — PIN profilu gracza.

Nazwa profilu jest unikalna (bez względu na wielkość liter), a PIN (4–8 cyfr)
potwierdza, że to ten gracz. Skrót PIN-u (scrypt, losowa sól) leży w folderze
profilu: `<profil>/.auth.json`. Serwer EmuStart nigdy go nie wydaje (/v1/nas go
ukrywa) i wydaje pliki profilu z PIN-em tylko z tokenem profilu (nagłówek
X-Profile-Token), który dostaje urządzenie po podaniu PIN-u — urządzenie pamięta
token do wylogowania. Token = HMAC(sekret serwera, profil + skrót PIN-u): zmiana
PIN-u unieważnia tokeny wszystkich urządzeń. Złe PIN-y: rosnące opóźnienie.

Przez SMB (komputer bez serwera) PIN sprawdza EmuStart sam — kto ma dostęp do
udziału, ma też dostęp do plików, więc to rozróżnienie kont, nie zabezpieczenie.
"""

from __future__ import annotations

import hashlib
import hmac
import json
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


def token(secret: str, profile: str, rec: dict) -> str:
    msg = (profile.lower() + "\n" + rec.get("hash", "")).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def token_ok(secret: str, profile: str, rec: dict, got: str) -> bool:
    return bool(got) and hmac.compare_digest(token(secret, profile, rec), got)


def read(fs, profile: str) -> dict | None:
    """Rekord PIN-u profilu (None = profil bez PIN-u)."""
    try:
        data = fs.read(f"{profile}/{AUTH}")
    except OSError:
        return None
    if not data:
        return None
    try:
        rec = json.loads(data.decode("utf-8"))
    except ValueError:
        return None
    return rec if isinstance(rec, dict) and rec.get("hash") else None


def write(fs, profile: str, rec: dict) -> None:
    fs.write(f"{profile}/{AUTH}", json.dumps(rec).encode("utf-8"))


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


def verify(fs, profile: str, pin: str) -> tuple[bool, str, float]:
    """(dobry PIN?, powód, ile czekać). Profil bez PIN-u: (False, "brak", 0)."""
    left = wait_left(profile)
    if left > 0:
        return False, f"Za dużo złych PIN-ów — spróbuj za {int(left) + 1} s.", left
    rec = read(fs, profile)
    if rec is None:
        return False, "brak", 0.0
    if check(rec, pin):
        succeeded(profile)
        return True, "", 0.0
    failed(profile)
    return False, "Zły PIN.", wait_left(profile)


def find(names, wanted: str) -> str:
    """Istniejący profil o tej nazwie bez względu na wielkość liter ('' = nie ma)."""
    w = wanted.strip().lower()
    return next((n for n in names if n.lower() == w), "")
