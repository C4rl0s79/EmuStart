"""enroll — logowanie urządzeń: kod logowania (albo konto + hasło) + klucz urządzenia, logowanie podpisem.

Tożsamość = konto + konkretne urządzenie. Każda instalacja tworzy własną parę kluczy
(EC P-256); klucz prywatny nie opuszcza urządzenia (Android Keystore, na Windows TPM, bez
niego klucz zaszyfrowany DPAPI). Serwer zna tylko klucz publiczny.

Zalogowanie urządzenia (jedyna droga do serwera — 0.43; tylko połączenia szyfrowane):
1. Urządzenie: POST /v1/enroll/request {mode, login_code | account + password, name, class, pubkey}
   - mode "code" — **kod logowania** (jednorazowy, 10 min) ze strony serwera (po zalogowaniu
     Google — emustart/webportal.py) albo z panelu serwera („Dodaj urządzenie”). Kod jest
     przypisany do konta — urządzenie go nie wybiera — i zastępuje zatwierdzenie. Gdy żadne
     konto nie jest administratorem, konto zalogowane takim kodem zostaje nim;
   - mode "existing" — „Mam konto”: nazwa i hasło konta (emustart/accounts.py) → rid (tajny,
     do odpytywania) i kod weryfikacyjny (z klucza publicznego — ten sam na ekranie nowego
     urządzenia i u zatwierdzającego).
   Nowe konto (0.49) powstaje tylko z zaproszenia (strona serwera) albo w panelu — urządzenie
   samo konta nie zakłada.
2. „Mam konto”: zatwierdzający widzi prośbę (GET /v1/enroll/pending — przy otwarciu aplikacji,
   w panelu): czas, adres, nazwę, rodzaj, konto, kod. POST /v1/enroll/decide {id, allow, games}
   — urządzenie tego konta, a gdy konto nie ma żadnego — administrator.
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
_login_codes: dict = {}          # kod logowania → {exp, account, create, by} — jednorazowy
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
        # 0.49: prośby o nowe konto (urządzenie zakładało konto samo) już nie istnieją
        old = [r for r, q in _reqs.items() if q.get("status") == "pending" and (q.get("mode") != "existing"
                                                                                or q.get("for_device"))]
        for r in old:
            _reqs.pop(r, None)
        if old:
            _save()
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


# ── kod logowania (strona serwera, panel) ──
def new_login_code(account: str, create: bool = False, by: str = "panel serwera") -> dict:
    """Jednorazowy kod logowania konta `account`: zalogowanie nim nie czeka na zatwierdzenie.
    `create` — konto powstanie przy zalogowaniu (nowe konto z panelu)."""
    acc = _safe_account(account)
    if not acc:
        raise ValueError("zła nazwa konta")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    now = time.time()
    with _lock:
        for c, e in list(_login_codes.items()):
            if e["exp"] < now:
                _login_codes.pop(c, None)
        _login_codes[code] = {"exp": now + LOGIN_CODE_TTL, "account": acc, "create": bool(create), "by": by}
    return {"code": code, "pretty": pretty(code), "expires": now + LOGIN_CODE_TTL, "account": acc}


def _norm_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch in CODE_ALPHABET)


def _take_login_code(code: str, ip: str) -> tuple[str, dict | None]:
    """('', kod) = kod przyjęty (i zużyty); inaczej (powód, None). Złe kody z adresu — przerwa."""
    now = time.time()
    with _lock:
        fails = [t for t in _code_fails.get(ip, []) if now - t < LOGIN_CODE_TTL]
        _code_fails[ip] = fails
        if len(fails) >= CODE_FAILS_MAX:
            return "Za dużo błędnych kodów — spróbuj za kilka minut.", None
        e = _login_codes.pop(_norm_code(code), None)
        if not e or e["exp"] < now:
            fails.append(now)
            return "Kod logowania jest nieprawidłowy albo wygasł — weź nowy ze strony serwera.", None
    return "", e


# ── prośby ──
def request(mode: str, account: str, name: str, cls: str, ip: str, version: str, pubkey: str,
            accounts: list, hw: str = "", password: str = "", login_code: str = "",
            create_account=None, app: str = "") -> dict:
    """Nowa instalacja prosi o dołączenie. `accounts` — istniejące konta (profile).
    mode "code" — kod logowania (konto z kodu, bez zatwierdzania); "existing" — „Mam konto”:
    nazwa i hasło konta, zatwierdza urządzenie konta albo administrator."""
    from emustart import accounts as acc_pw
    from emustart import devices
    if mode not in ("code", "existing"):
        return {"ok": False, "reason": "Nowe konto zakłada się z zaproszenia — zaloguj się na stronie serwera "
                                       "i weź kod logowania."}
    try:
        code = verify_code(pubkey)
    except Exception:
        return {"ok": False, "reason": "zły klucz urządzenia"}
    create = False
    if mode == "code":
        if not login_code:
            return {"ok": False, "reason": "Podaj kod logowania ze strony serwera."}
        why, lc = _take_login_code(login_code, ip)
        if why:
            return {"ok": False, "reason": why}
        acc = lc["account"]
        match = next((a for a in accounts if a.lower() == acc.lower()), "") or \
            (acc_pw.record(acc) or {}).get("name", "")
        if not match and not lc["create"]:
            return {"ok": False, "reason": f"Nie ma już konta „{acc}”."}
        acc, create = match or acc, not match
    else:
        acc = _safe_account(account)
        if not acc:
            return {"ok": False, "reason": "Podaj nazwę konta."}
        match = next((a for a in accounts if a.lower() == acc.lower()), "") or \
            (acc_pw.record(acc) or {}).get("name", "")
        if not match:
            return {"ok": False, "reason": f"Nie ma konta „{acc}”."}
        acc = match
        if not acc_pw.has_password(acc):
            return {"ok": False, "reason": f"Konto „{acc}” nie ma hasła — zaloguj się kodem ze strony serwera "
                                           "(albo ustaw tam hasło konta)."}
        ok, why, _wait = acc_pw.verify(acc, password, ip)
        if not ok:
            return {"ok": False, "reason": why}
    by = lc["by"] if mode == "code" else ""
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
                if mode == "code":
                    q.update(account=acc, create=create)
                    _approve_locked(q, True, f"kod logowania ({by})", create_account, first_admin=True)
                    _save()
                return {"ok": True, "rid": rid, "code": q["code"], "pretty": pretty(q["code"]), "expires": q["expires"],
                        "status": q["status"]}
        rid = secrets.token_urlsafe(24)
        reqs[rid] = {"id": secrets.token_hex(4), "mode": mode, "account": acc,
                     "name": (str(name).strip() or "urządzenie")[:40],
                     "class": cls if cls in devices.CLASSES else "pc", "ip": ip, "version": version[:20],
                     "pubkey": pubkey, "code": code, "created": now, "expires": now + ENROLL_TTL,
                     "status": "pending", "device": "", "decided_by": "", "hw": devices._valid_hw(hw),
                     "app": devices.app_name(app, cls), "create": create}
        if mode == "code":                # kod logowania = zatwierdzenie
            _approve_locked(reqs[rid], True, f"kod logowania ({by})", create_account, first_admin=True)
        _save()
        status = reqs[rid]["status"]
    log.info("prośba o zalogowanie: %s (%s) — konto „%s”%s, %s, kod %s%s", name, cls, acc,
             " (nowe)" if create else "", ip, pretty(code),
             f" — zatwierdzona kodem logowania ({by})" if status == "approved" else "")
    return {"ok": True, "rid": rid, "code": code, "pretty": pretty(code), "expires": now + ENROLL_TTL,
            "status": status}


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
    return not devices.of_account(q["account"])


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
    """Zatwierdzona prośba → konto (nowe z kodu logowania: folder profilu) i urządzenie. Wołający
    trzyma _lock. `first_admin` — kod logowania: gdy żadne konto nie jest administratorem, to zostaje."""
    from emustart import accounts as acc_pw
    from emustart import devices
    if q.pop("create", False):
        if create_account:
            create_account(q["account"])
        acc_pw.create(q["account"], by)
    if first_admin and not acc_pw.any_admin():
        acc_pw.set_admin(q["account"], True, by)
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
