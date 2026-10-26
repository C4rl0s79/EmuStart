"""invites — zaproszenia na serwer (0.49): adres Google + konto.

Administrator w panelu serwera zaprasza adres e-mail (konto Google) do konta — nowego albo
istniejącego — i dostaje link do strony serwera (`<adres tunelu>/?zaproszenie=<token>`), który
sam wysyła zaproszonemu. Zaproszony loguje się na stronie przez Google (emustart/webportal.py):
serwer wpuszcza tylko adres z ważnym zaproszeniem, zakłada albo łączy konto i od tej chwili
rozpoznaje je po stałym identyfikatorze Google (`sub`) — zaproszenie się zużywa.

Token z linku nie zastępuje adresu: zaproszenie działa tylko dla konta Google o zaproszonym
adresie (link przekazany dalej nic nie da). Serwer trzyma skrót tokenu, nie token.

Plik: `invites.json` w danych serwera (obok `accounts.json`).
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

log = logging.getLogger("emustart.invites")

TTL = 30 * 24 * 3600
_EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_lock = threading.Lock()
_db: dict | None = None               # id → zaproszenie


def _path():
    return paths.SERVER / "invites.json"


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


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def norm_email(email: str) -> str:
    return (email or "").strip().lower()


def _public(i: dict) -> dict:
    return {k: i.get(k) for k in ("id", "email", "account", "created", "expires", "by", "used", "used_account")}


def create(email: str, account: str, by: str = "panel serwera") -> dict:
    """Nowe zaproszenie → {ok, invite, token} (token tylko teraz — do linku). Poprzednie
    niewykorzystane zaproszenie tego adresu znika."""
    from emustart import enroll
    email = norm_email(email)
    if not _EMAIL.match(email):
        return {"ok": False, "reason": "Podaj adres e-mail (konto Google)."}
    acc = enroll._safe_account(account)
    if not acc:
        return {"ok": False, "reason": "Podaj nazwę konta (litery, cyfry, spacje; bez znaków / \\ : * ? \" < > |)."}
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        db = _load()
        for k in [k for k, i in db.items() if i["email"] == email and not i.get("used")]:
            db.pop(k)
        iid = secrets.token_hex(6)
        db[iid] = {"id": iid, "email": email, "account": acc, "hash": _hash(token), "created": now,
                   "expires": now + TTL, "by": by, "used": 0, "used_account": ""}
        _save()
        inv = _public(db[iid])
    log.info("zaproszenie %s → konto %s (%s)", email, acc, by)
    return {"ok": True, "invite": inv, "token": token}


def listing() -> list:
    now = time.time()
    with _lock:
        return sorted((_public(i) | {"expired": not i.get("used") and i["expires"] < now} for i in _load().values()),
                      key=lambda i: -i["created"])


def delete(iid: str) -> bool:
    with _lock:
        if _load().pop(str(iid), None) is None:
            return False
        _save()
    return True


def find(email: str, token: str = "") -> tuple[dict | None, str]:
    """Ważne zaproszenie dla adresu `email` → (zaproszenie, ''); inaczej (None, powód: 'none' |
    'other' — token z linku jest dla innego adresu)."""
    email = norm_email(email)
    now = time.time()
    with _lock:
        live = [i for i in _load().values() if not i.get("used") and i["expires"] >= now]
        if token:
            hit = next((i for i in live if hmac.compare_digest(i["hash"], _hash(token))), None)
            if hit and hit["email"] != email:
                return None, "other"
        hit = next((i for i in live if i["email"] == email), None)
        return (dict(hit), "") if hit else (None, "none")


def token_ok(token: str) -> bool:
    """Czy link zaproszenia jest jeszcze ważny (strona pokazuje wtedy „Masz zaproszenie”)."""
    now = time.time()
    with _lock:
        return bool(token) and any(not i.get("used") and i["expires"] >= now and hmac.compare_digest(i["hash"], _hash(token))
                                   for i in _load().values())


def use(iid: str, account: str) -> None:
    with _lock:
        i = _load().get(iid)
        if i:
            i.update(used=time.time(), used_account=account)
            _save()
