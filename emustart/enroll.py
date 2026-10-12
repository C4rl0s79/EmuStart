"""enroll — nowe instalacje przez internet: konta, role, klucze urządzeń, logowanie podpisem (0.37).

Tożsamość = konto + konkretne urządzenie. Każda instalacja tworzy własną parę kluczy
(EC P-256); klucz prywatny nie opuszcza urządzenia (Android Keystore, na Windows TPM, bez
niego klucz zaszyfrowany DPAPI). Serwer zna tylko klucz publiczny.

Dołączenie (jedyna droga z internetu — przez tunel albo port publiczny):
1. Urządzenie: POST /v1/enroll/request {mode: new|existing, account, name, class, pubkey}
   → rid (tajny, do odpytywania) i kod weryfikacyjny (z klucza publicznego — ten sam na
   ekranie nowego urządzenia i u zatwierdzającego).
2. Zatwierdzający widzi prośbę (GET /v1/enroll/pending — przy otwarciu aplikacji, w panelu):
   czas, adres, nazwę, rodzaj, konto, kod. POST /v1/enroll/decide {id, allow, games}.
   - nowe konto — tylko administrator;
   - „mam konto” — urządzenie tego konta, a gdy konto nie ma żadnego — administrator.
3. Urządzenie: POST /v1/enroll/poll {rid} → id urządzenia po zatwierdzeniu.
4. Logowanie: POST /v1/auth/challenge {device} → wyzwanie; POST /v1/auth/token {device,
   nonce, sig} → token sesji (1 h, odnawiany przez urządzenie). Skopiowany plik nic nie da
   bez klucza prywatnego, a przechwycony token wygasa.

Do zatwierdzenia urządzenie nie ma dostępu do niczego poza stanem swojej prośby. Prośba czeka
24 h (zapisana na dysku — przetrwa restart i aktualizację serwera).

Role: `admin` (zatwierdza wszystko, urządzenie zero — zatwierdzone w panelu serwera albo
sparowane kodem z panelu), `user` (tylko swoje konto; zatwierdza nowe urządzenia swojego konta).
Urządzenia sprzed 0.37 (bez konta) mają dostęp jak administrator do czasu przejścia na klucze.
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

from emustart import paths

log = logging.getLogger("emustart.enroll")

ENROLL_TTL = 24 * 3600
MAX_PENDING_IP = 5
NONCE_TTL = 120
TOKEN_TTL = 3600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_lock = threading.Lock()
_reqs: dict | None = None        # rid → prośba (zapis: enroll.json)
_nonces: dict = {}               # wyzwanie → (id urządzenia, ważne do) — kilka naraz na urządzenie
_tokens: dict = {}               # token → (id urządzenia, ważny do)


def _path():
    return paths.SERVER / "enroll.json"


def _load() -> dict:
    global _reqs
    if _reqs is None:
        try:
            _reqs = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _reqs = {}
    return _reqs


def _save() -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(_load(), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def reset() -> None:
    """Dla testów."""
    global _reqs
    with _lock:
        _reqs = None
        _nonces.clear()
        _tokens.clear()


def _gc(now: float) -> None:
    reqs = _load()
    dead = [r for r, q in reqs.items() if q["expires"] < now and q["status"] in ("pending", "denied", "approved")]
    for r in dead:
        reqs.pop(r, None)
    if dead:
        _save()


# ── klucze ──
def _pub(pem: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(k, ec.EllipticCurvePublicKey) or k.curve.name != "secp256r1":
        raise ValueError("klucz musi być EC P-256")
    return k


def verify_code(pem: str) -> str:
    """Kod weryfikacyjny z klucza publicznego (8 znaków) — ten sam na obu ekranach."""
    from cryptography.hazmat.primitives import serialization
    der = _pub(pem).public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    h = hashlib.sha256(der).digest()
    return "".join(CODE_ALPHABET[b % len(CODE_ALPHABET)] for b in h[:8])


def pretty(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def _safe_account(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", (name or "").strip()).strip(" .")
    return name[:30]


# ── prośby ──
def request(mode: str, account: str, name: str, cls: str, ip: str, version: str, pubkey: str,
            accounts: list) -> dict:
    """Nowa instalacja prosi o dołączenie. `accounts` — istniejące konta (profile)."""
    from emustart import devices
    if mode not in ("new", "existing"):
        return {"ok": False, "reason": "zły rodzaj prośby"}
    try:
        code = verify_code(pubkey)
    except Exception:
        return {"ok": False, "reason": "zły klucz urządzenia"}
    acc = _safe_account(account)
    if not acc:
        return {"ok": False, "reason": "Podaj nazwę konta."}
    match = next((a for a in accounts if a.lower() == acc.lower()), "")
    if mode == "new" and match:
        return {"ok": False, "reason": f"Konto „{match}” już istnieje — wybierz „Mam konto”."}
    if mode == "existing":
        if not match:
            return {"ok": False, "reason": f"Nie ma konta „{acc}”."}
        acc = match
    now = time.time()
    with _lock:
        _gc(now)
        reqs = _load()
        if sum(1 for q in reqs.values() if q["ip"] == ip and q["status"] == "pending") >= MAX_PENDING_IP:
            return {"ok": False, "reason": "Za dużo oczekujących próśb z tego adresu — spróbuj później."}
        # ponowna prośba tego samego klucza — ta sama prośba (np. po restarcie aplikacji)
        for rid, q in reqs.items():
            if q["pubkey"] == pubkey and q["status"] == "pending":
                return {"ok": True, "rid": rid, "code": q["code"], "pretty": pretty(q["code"]), "expires": q["expires"]}
        rid = secrets.token_urlsafe(24)
        reqs[rid] = {"id": secrets.token_hex(4), "mode": mode, "account": acc,
                     "name": (str(name).strip() or "urządzenie")[:40],
                     "class": cls if cls in devices.CLASSES else "pc", "ip": ip, "version": version[:20],
                     "pubkey": pubkey, "code": code, "created": now, "expires": now + ENROLL_TTL,
                     "status": "pending", "device": "", "decided_by": ""}
        _save()
    log.info("prośba o dołączenie: %s (%s) — %s „%s”, %s, kod %s", name, cls,
             "nowe konto" if mode == "new" else "konto", acc, ip, pretty(code))
    return {"ok": True, "rid": rid, "code": code, "pretty": pretty(code), "expires": now + ENROLL_TTL}


def poll(rid: str) -> dict:
    with _lock:
        _gc(time.time())
        for r, q in _load().items():
            if rid and hmac.compare_digest(r, str(rid)):
                out = {"ok": True, "status": q["status"], "account": q["account"]}
                if q["status"] == "approved":
                    out["device"] = q["device"]
                return out
    return {"ok": True, "status": "expired"}


def _visible(q: dict, viewer: str) -> bool:
    """Czy urządzenie `viewer` ('' = panel serwera) może zatwierdzić tę prośbę."""
    from emustart import devices
    if not viewer or devices.is_admin(viewer):
        return True
    if q["mode"] != "existing":
        return False
    return devices.account_of(viewer).lower() == q["account"].lower()


def _admin_only(q: dict) -> bool:
    """„Mam konto”, gdy konto nie ma żadnego urządzenia — tylko administrator."""
    from emustart import devices
    return q["mode"] == "new" or not devices.of_account(q["account"])


def pending(viewer: str) -> list:
    """Prośby do zatwierdzenia przez to urządzenie ('' = panel)."""
    from emustart import devices
    with _lock:
        _gc(time.time())
        out = []
        for q in _load().values():
            if q["status"] != "pending":
                continue
            if not _visible(q, viewer) or (_admin_only(q) and viewer and not devices.is_admin(viewer)):
                continue
            out.append({k: q[k] for k in ("id", "mode", "account", "name", "class", "ip", "created", "expires")}
                       | {"code": pretty(q["code"])})
    return sorted(out, key=lambda q: q["created"])


def decide(req_id: str, allow: bool, games: bool, viewer: str, create_account=None) -> dict:
    """Zatwierdzenie / odrzucenie. `create_account(name)` — zakłada konto (folder profilu)."""
    from emustart import devices
    with _lock:
        _gc(time.time())
        hit = next(((r, q) for r, q in _load().items() if q["id"] == req_id and q["status"] == "pending"), None)
        if not hit:
            return {"ok": False, "reason": "Nie ma takiej prośby (wygasła albo już rozpatrzona)."}
        rid, q = hit
        if not _visible(q, viewer) or (_admin_only(q) and viewer and not devices.is_admin(viewer)):
            return {"ok": False, "reason": "To urządzenie nie może zatwierdzić tej prośby."}
        by = devices.name_of(viewer) if viewer else "panel serwera"
        if not allow:
            q.update(status="denied", decided_by=by)
            _save()
            log.info("odrzucono prośbę %s (%s, %s) — %s", q["name"], q["account"], q["ip"], by)
            return {"ok": True, "status": "denied", "name": q["name"]}
        if q["mode"] == "new" and create_account:
            create_account(q["account"])
        did = devices.add_keyed(q["name"], q["class"], q["ip"], q["version"], q["pubkey"], q["account"],
                                role="user", games=bool(games), by=by)
        q.update(status="approved", device=did, decided_by=by)
        _save()
    log.info("zatwierdzono %s dla konta %s (%s, gry: %s) — %s", q["name"], q["account"], q["ip"],
             "tak" if games else "nie", by)
    return {"ok": True, "status": "approved", "name": q["name"], "device": did}


# ── logowanie podpisem ──
def challenge(did: str) -> dict:
    from emustart import devices
    if not devices.pubkey_of(did):
        return {"ok": False, "reason": "nieznane urządzenie"}
    nonce = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        for k, (_d, exp) in list(_nonces.items()):
            if exp < now:
                _nonces.pop(k, None)
        if sum(1 for d, _e in _nonces.values() if d == did) >= 20:
            return {"ok": False, "reason": "za dużo prób logowania — chwilę poczekaj"}
        _nonces[nonce] = (did, now + NONCE_TTL)
    return {"ok": True, "nonce": nonce}


def message(did: str, nonce: str) -> bytes:
    return f"emustart-auth|{did}|{nonce}".encode("utf-8")


def token(did: str, nonce: str, sig_b64: str) -> dict:
    """Podpis wyzwania kluczem urządzenia → token sesji."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from emustart import devices
    pem = devices.pubkey_of(did)
    with _lock:
        n = _nonces.pop(str(nonce), None)          # jednorazowe
    if not pem or not n or n[1] < time.time() or not hmac.compare_digest(n[0], did):
        return {"ok": False, "reason": "wyzwanie nieważne — spróbuj ponownie"}
    try:
        sig = base64.b64decode(sig_b64)
        _pub(pem).verify(sig, message(did, str(nonce)), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError, TypeError):
        return {"ok": False, "reason": "zły podpis"}
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for t, (_d, exp) in list(_tokens.items()):
            if exp < now:
                _tokens.pop(t, None)
        _tokens[tok] = (did, now + TOKEN_TTL)
    return {"ok": True, "token": tok, "expires": now + TOKEN_TTL}


def token_device(tok: str) -> str:
    """Id urządzenia dla tokenu sesji ('' = nieznany / wygasły)."""
    with _lock:
        hit = _tokens.get(tok)
    if not hit or hit[1] < time.time():
        return ""
    return hit[0]


def drop_tokens(did: str) -> None:
    """Unieważnienie urządzenia — jego sesje też."""
    with _lock:
        for t, (d, _e) in list(_tokens.items()):
            if d == did:
                _tokens.pop(t, None)
