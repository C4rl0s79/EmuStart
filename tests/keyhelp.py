"""Pomocnik testów: urządzenie zalogowane kontem i kluczem urządzenia (jedyna droga od 0.43).

Logowanie kodem z panelu (jak kod QR): bez czekania na zatwierdzenie; pierwsze takie konto
zostaje administratorem. Zapytania do zwykłego portu z 127.0.0.1 — jak panel na serwerze."""

from __future__ import annotations

import base64
import http.client
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

PW = "Haslo-testowe1"
ACCOUNT = "Test"


def new_key():
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return k, pem


def post(host, port, path, body, key="", headers=None):
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {}), **(headers or {})}
    c.request("POST", path, body=json.dumps(body).encode(), headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def login(host, port, did, key) -> str:
    """Wyzwanie → podpis → token sesji (jak urządzenie)."""
    from emustart import enroll
    _st, ch = post(host, port, "/v1/auth/challenge", {"device": did})
    sig = key.sign(enroll.message(did, ch["nonce"]), ec.ECDSA(hashes.SHA256()))
    _st, t = post(host, port, "/v1/auth/token", {"device": did, "nonce": ch["nonce"],
                                                 "sig": base64.b64encode(sig).decode()})
    return t["token"]


def enroll_device(host, port, name, cls="pc", hw="", account=ACCOUNT, pem="", code=True, password=PW) -> dict:
    """Prośba o zalogowanie (konto istnieje → „mam konto”); `code` — kod z panelu (od razu
    zatwierdzona). → odpowiedź serwera (rid, status)."""
    from emustart import accounts, enroll, remote
    exists = accounts.record(account) is not None or account.lower() in (n.lower() for n in remote.profile_names()) \
        if remote.NAS_ROOT else accounts.record(account) is not None
    body = {"mode": "existing" if exists else "new", "account": account, "password": password, "name": name,
            "class": cls, "version": "test", "pubkey": pem, "hw": hw,
            "login_code": enroll.new_login_code()["code"] if code else ""}
    st, d = post(host, port, "/v1/enroll/request", body)
    assert st == 200 and d["ok"], d
    return d


def pair(host, port, name, cls="pc", hw="", account=ACCOUNT):
    """Urządzenie zalogowane kodem z panelu → (token sesji, id urządzenia, klucz prywatny)."""
    key, pem = new_key()
    d = enroll_device(host, port, name, cls, hw, account, pem)
    _st, p = post(host, port, "/v1/enroll/poll", {"rid": d["rid"]})
    assert p["status"] == "approved", p
    return login(host, port, p["device"], key), p["device"], key


def pc_cfg(host, port, name="Komputer", monkeypatch=None, account=ACCOUNT):
    """Ustawienia komputera zalogowanego kluczem urządzenia z magazynu kluczy (bez TPM w testach),
    łączącego się portem szyfrowanym z przypiętym certyfikatem."""
    import secrets
    from _srv import tls_server
    from emustart import keystore, netsrc
    url, fp = tls_server(monkeypatch)
    kname = f"EmuStart-test-{secrets.token_hex(4)}"
    kind, pem = keystore.create(kname)
    d = enroll_device(host, port, name, "pc", "", account, pem)
    _st, p = post(host, port, "/v1/enroll/poll", {"rid": d["rid"]})
    assert p["status"] == "approved", p
    return {"server_url": url, "server_fp": fp, "server_key": netsrc.DEV + p["device"],
            "server_keyname": kname, "server_keykind": kind, "server_account": account}


def device(account=ACCOUNT, admin=False, games=True, name="Urządzenie"):
    """Urządzenie konta wpisane wprost do rejestru + token sesji (bez sieci) → (id, token)."""
    import time
    from emustart import accounts, devices, enroll
    did = devices.add_keyed(name, "phone", "", "", new_key()[1], account, games=games, by="test")
    if admin:
        accounts.set_admin(account, True)
    tok = f"sesja-{did}"
    enroll._tokens[tok] = (did, time.time() + 600)
    return did, tok
