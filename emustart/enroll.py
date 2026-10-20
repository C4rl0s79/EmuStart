"""enroll — logowanie urządzeń: konto + hasło + klucz urządzenia, logowanie podpisem.

Tożsamość = konto + konkretne urządzenie. Każda instalacja tworzy własną parę kluczy
(EC P-256); klucz prywatny nie opuszcza urządzenia (Android Keystore, na Windows TPM, bez
niego klucz zaszyfrowany DPAPI). Serwer zna tylko klucz publiczny.

Zalogowanie urządzenia (jedyna droga do serwera — 0.43; tylko połączenia szyfrowane):
1. Urządzenie: POST /v1/enroll/request {mode: new|existing, account, password, name, class, pubkey}
   — hasło konta (0.40): nowe konto je ustawia, „mam konto” je sprawdza (emustart/accounts.py)
   → rid (tajny, do odpytywania) i kod weryfikacyjny (z klucza publicznego — ten sam na
   ekranie nowego urządzenia i u zatwierdzającego).
2. Zatwierdzający widzi prośbę (GET /v1/enroll/pending — przy otwarciu aplikacji, w panelu):
   czas, adres, nazwę, rodzaj, konto, kod. POST /v1/enroll/decide {id, allow, games}.
   - nowe konto — tylko administrator;
   - „mam konto” — urządzenie tego konta, a gdy konto nie ma żadnego — administrator.
   Kod QR z panelu serwera (`login_code` w prośbie, jednorazowy, 10 min) zastępuje
   zatwierdzenie — kto go zeskanował, ma dostęp do serwera. Gdy żadne konto nie jest
   administratorem, konto zalogowane takim kodem zostaje nim (pierwsze urządzenie).
3. Urządzenie: POST /v1/enroll/poll {rid} → id urządzenia po zatwierdzeniu.
4. Logowanie: POST /v1/auth/challenge {device} → wyzwanie; POST /v1/auth/token {device,
   nonce, sig} → token sesji (1 h, odnawiany przez urządzenie). Skopiowany plik nic nie da
   bez klucza prywatnego, a przechwycony token wygasa.

Do zatwierdzenia urządzenie nie ma dostępu do niczego poza stanem swojej prośby. Prośba czeka
24 h (zapisana na dysku — przetrwa restart i aktualizację serwera).

Uprawnienia — z konta (emustart/accounts.py): administrator zatwierdza wszystko i widzi
wszystkie konta; zwykłe konto zatwierdza tylko nowe urządzenia swojego konta.
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
MAX_PENDING = 50                 # wszystkich oczekujących (zapisanych na dysku) — z wielu adresów
NONCE_TTL = 120
TOKEN_TTL = 3600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_lock = threading.Lock()
_reqs: dict | None = None        # rid → prośba (zapis: enroll.json)
_nonces: dict = {}               # wyzwanie → (id urządzenia, ważne do) — kilka naraz na urządzenie
_tokens: dict = {}               # token → (id urządzenia, ważny do)
_login_codes: dict = {}          # kod z panelu (QR) → ważny do — jednorazowy
_code_fails: dict = {}           # adres → [czasy złych kodów]
LOGIN_CODE_TTL = 600
CODE_FAILS_MAX = 10


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
        _login_codes.clear()
        _code_fails.clear()


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
    from emustart import nasfs
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", (name or "").strip()).strip(" .")[:30].strip(" .")
    try:
        ok = nasfs.clean_rel(name) == name and not name.startswith(("_", "."))
    except ValueError:
        ok = False
    return name if ok else ""


# ── kod logowania z panelu serwera (QR) ──
def new_login_code() -> dict:
    """Jednorazowy kod z panelu: zalogowanie nim nie czeka na zatwierdzenie."""
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    now = time.time()
    with _lock:
        for c, exp in list(_login_codes.items()):
            if exp < now:
                _login_codes.pop(c, None)
        _login_codes[code] = now + LOGIN_CODE_TTL
    return {"code": code, "pretty": pretty(code), "expires": now + LOGIN_CODE_TTL}


def _norm_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch in CODE_ALPHABET)


def _take_login_code(code: str, ip: str) -> str:
    """'' = kod przyjęty (i zużyty); inaczej powód. Złe kody z adresu — przerwa."""
    now = time.time()
    with _lock:
        fails = [t for t in _code_fails.get(ip, []) if now - t < LOGIN_CODE_TTL]
        _code_fails[ip] = fails
        if len(fails) >= CODE_FAILS_MAX:
            return "Za dużo błędnych kodów — spróbuj za kilka minut."
        c = _norm_code(code)
        exp = _login_codes.pop(c, None)
        if not exp or exp < now:
            fails.append(now)
            return "Kod z panelu serwera jest nieprawidłowy albo wygasł — pokaż nowy (panel → Dodaj urządzenie)."
    return ""


# ── prośby ──
def request(mode: str, account: str, name: str, cls: str, ip: str, version: str, pubkey: str,
            accounts: list, hw: str = "", password: str = "", login_code: str = "",
            create_account=None, app: str = "") -> dict:
    """Nowa instalacja prosi o dołączenie. `accounts` — istniejące konta (profile).
    „Nowe konto” ustawia hasło, „Mam konto” je sprawdza (konto bez hasła — ustawia je
    przy zatwierdzeniu); w prośbie zostaje tylko skrót hasła (emustart/accounts.py)."""
    from emustart import accounts as acc_pw
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
    match = next((a for a in accounts if a.lower() == acc.lower()), "") or \
        (acc_pw.record(acc) or {}).get("name", "")
    if mode == "new" and match:
        return {"ok": False, "reason": f"Konto „{match}” już istnieje — wybierz „Mam konto”."}
    pw_rec = None
    if mode == "existing":
        if not match:
            return {"ok": False, "reason": f"Nie ma konta „{acc}”."}
        acc = match
        if acc_pw.has_password(acc):
            ok, why, _wait = acc_pw.verify(acc, password, ip)
            if not ok:
                return {"ok": False, "reason": why}
    if mode == "new" or not acc_pw.has_password(acc):
        why = acc_pw.policy(password)
        if why:
            return {"ok": False, "reason": why}
        pw_rec = acc_pw.make(password)
    if login_code:
        why = _take_login_code(login_code, ip)
        if why:
            return {"ok": False, "reason": why}
    now = time.time()
    with _lock:
        _gc(now)
        reqs = _load()
        pend = [q for q in reqs.values() if q["status"] == "pending"]
        if sum(1 for q in pend if q["ip"] == ip) >= MAX_PENDING_IP:
            return {"ok": False, "reason": "Za dużo oczekujących próśb z tego adresu — spróbuj później."}
        if len(pend) >= MAX_PENDING and not any(q["pubkey"] == pubkey for q in pend):
            return {"ok": False, "reason": "Serwer ma za dużo oczekujących próśb — spróbuj później."}
        # ponowna prośba tego samego klucza — ta sama prośba (np. po restarcie aplikacji)
        for rid, q in reqs.items():
            if pubkey and q["pubkey"] == pubkey and q["status"] == "pending":
                if login_code:
                    _approve_locked(q, True, "kod QR z panelu serwera", create_account, first_admin=True)
                    _save()
                return {"ok": True, "rid": rid, "code": q["code"], "pretty": pretty(q["code"]), "expires": q["expires"],
                        "status": q["status"]}
        rid = secrets.token_urlsafe(24)
        reqs[rid] = {"id": secrets.token_hex(4), "mode": mode, "account": acc,
                     "name": (str(name).strip() or "urządzenie")[:40],
                     "class": cls if cls in devices.CLASSES else "pc", "ip": ip, "version": version[:20],
                     "pubkey": pubkey, "code": code, "created": now, "expires": now + ENROLL_TTL,
                     "status": "pending", "device": "", "decided_by": "", "hw": devices._valid_hw(hw),
                     "app": devices.app_name(app, cls), "pw": pw_rec}
        if login_code:                    # kod z panelu = zatwierdzenie
            _approve_locked(reqs[rid], True, "kod QR z panelu serwera", create_account, first_admin=True)
        _save()
        status = reqs[rid]["status"]
    log.info("prośba o zalogowanie: %s (%s) — %s „%s”, %s, kod %s%s", name, cls,
             "nowe konto" if mode == "new" else "konto", acc, ip, pretty(code),
             " — zatwierdzona kodem z panelu" if status == "approved" else "")
    return {"ok": True, "rid": rid, "code": code, "pretty": pretty(code), "expires": now + ENROLL_TTL,
            "status": status}


MAX_ACCOUNT_REQ = 3              # oczekujących próśb o nowe konto z jednego urządzenia


def request_account(viewer: str, account: str, password: str, ip: str, accounts: list,
                    create_account=None) -> dict:
    """Zalogowane urządzenie prosi o **nowe konto** (inny użytkownik na tym urządzeniu): serwer
    wie, kto pyta (token urządzenia), konto powstaje dopiero po zatwierdzeniu przez
    administratora (urządzenie administratora — od razu). Po zatwierdzeniu urządzenie dostaje
    dostęp do nowego konta (token konta) — bez ponownego podawania hasła."""
    from emustart import accounts as acc_pw
    from emustart import devices
    if not viewer:
        return {"ok": False, "reason": "Tylko z zalogowanego urządzenia."}
    acc = _safe_account(account)
    if not acc:
        return {"ok": False, "reason": "Zła nazwa konta."}
    if any(a.lower() == acc.lower() for a in accounts) or acc_pw.record(acc):
        return {"ok": False, "exists": True, "reason": f"Konto „{acc}” już istnieje — zaloguj się jego hasłem."}
    why = acc_pw.policy(password)
    if why:
        return {"ok": False, "reason": why}
    now = time.time()
    with _lock:
        _gc(now)
        reqs = _load()
        mine = [q for q in reqs.values() if q.get("for_device") == viewer and q["status"] == "pending"]
        if any(q["account"].lower() == acc.lower() for q in mine):
            return {"ok": False, "reason": f"Prośba o konto „{acc}” już czeka na zatwierdzenie."}
        if len(mine) >= MAX_ACCOUNT_REQ:
            return {"ok": False, "reason": "Za dużo oczekujących próśb o nowe konto z tego urządzenia."}
        if len([q for q in reqs.values() if q["status"] == "pending"]) >= MAX_PENDING:
            return {"ok": False, "reason": "Serwer ma za dużo oczekujących próśb — spróbuj później."}
        rid = secrets.token_urlsafe(24)
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        d = devices.listing_one(viewer)
        reqs[rid] = {"id": secrets.token_hex(4), "mode": "new", "account": acc, "name": d.get("name", "urządzenie"),
                     "class": d.get("class", "pc"), "ip": ip, "version": d.get("version", ""), "pubkey": "",
                     "code": code, "created": now, "expires": now + ENROLL_TTL, "status": "pending",
                     "device": "", "decided_by": "", "hw": "", "pw": acc_pw.make(password),
                     "for_device": viewer, "for_account": devices.account_raw(viewer)}
        if devices.is_admin(viewer):          # administrator zakłada konto sam
            _approve_locked(reqs[rid], True, devices.name_of(viewer), create_account)
        _save()
        status = reqs[rid]["status"]
    log.info("prośba o nowe konto „%s” z urządzenia %s (konto %s)%s", acc, d.get("name"), d.get("account"),
             " — założone (administrator)" if status == "approved" else "")
    return {"ok": True, "rid": rid, "account": acc, "status": status, "code": code, "pretty": pretty(code)}


def poll_account(rid: str, viewer: str) -> dict:
    """Stan prośby o nowe konto — tylko dla urządzenia, które o nie prosiło."""
    with _lock:
        _gc(time.time())
        for r, q in _load().items():
            if rid and hmac.compare_digest(r, str(rid)) and q.get("for_device") == viewer:
                return {"ok": True, "status": q["status"], "account": q["account"]}
    return {"ok": True, "status": "expired"}


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
                       | {"code": pretty(q["code"]), "app": devices.app_name(q.get("app", ""), q["class"]),
                          "replaces": devices.same_hw(q.get("hw", ""), q.get("app", ""), q["class"]),
                          "from_account": q.get("for_account", "")})
    return sorted(out, key=lambda q: q["created"])


def _approve_locked(q: dict, games: bool, by: str, create_account=None, first_admin: bool = False) -> None:
    """Zatwierdzona prośba → konto (nowe: folder i hasło) i urządzenie. Wołający trzyma _lock.
    `first_admin` — kod z panelu: gdy żadne konto nie jest administratorem, to zostaje."""
    from emustart import accounts as acc_pw
    from emustart import devices
    if q["mode"] == "new" and create_account:
        create_account(q["account"])
    if q.get("pw") and (q["mode"] == "new" or not acc_pw.has_password(q["account"])):
        acc_pw.set_record(q["account"], q["pw"], by)
    q.pop("pw", None)
    if first_admin and not acc_pw.any_admin():
        acc_pw.set_admin(q["account"], True, by)
    if q.get("for_device"):                   # nowe konto dla urządzenia już zalogowanego
        q.update(status="approved", device=q["for_device"], decided_by=by)
        return
    did = devices.add_keyed(q["name"], q["class"], q["ip"], q["version"], q["pubkey"], q["account"],
                            games=bool(games), by=by, hw=q.get("hw", ""), app=q.get("app", ""))
    q.update(status="approved", device=did, decided_by=by)


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
            q.pop("pw", None)
            _save()
            log.info("odrzucono prośbę %s (%s, %s) — %s", q["name"], q["account"], q["ip"], by)
            return {"ok": True, "status": "denied", "name": q["name"]}
        _approve_locked(q, games, by, create_account)
        _save()
    log.info("zatwierdzono %s dla konta %s (%s, gry: %s) — %s", q["name"], q["account"], q["ip"],
             "tak" if games else "nie", by)
    return {"ok": True, "status": "approved", "name": q["name"], "device": q["device"]}


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
