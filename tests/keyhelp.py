"""Pomocnik testów: urządzenie z kluczem urządzenia (od 0.39 serwer nie wydaje kluczy-haseł)."""

from __future__ import annotations

import base64
import http.client
import json

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


def new_key():
    k = ec.generate_private_key(ec.SECP256R1())
    pem = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return k, pem


def post(host, port, path, body, key=""):
    c = http.client.HTTPConnection(host, int(port), timeout=10)
    h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {key}"} if key else {})}
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


def pair(host, port, name, cls="pc", hw="", code=None):
    """Kod z panelu + klucz urządzenia → (token sesji, id urządzenia, klucz prywatny)."""
    from emustart import srvadmin
    code = code or srvadmin.pair_code(None)["code"]
    key, pem = new_key()
    st, d = post(host, port, "/v1/pair", {"code": code, "name": name, "class": cls, "version": "test",
                                          "pubkey": pem, "hw": hw})
    assert st == 200 and d["ok"], d
    return login(host, port, d["id"], key), d["id"], key


def pc_cfg(host, port, name="Komputer"):
    """Ustawienia komputera połączonego kluczem urządzenia z magazynu kluczy (bez TPM w testach)."""
    import secrets
    from emustart import keystore, netsrc, srvadmin
    kname = f"EmuStart-test-{secrets.token_hex(4)}"
    kind, pem = keystore.create(kname)
    code = srvadmin.pair_code(None)["code"]
    r = netsrc.pair(f"{host}:{port}", code, name, "pc", pem)
    assert r["ok"], r
    return {"server_url": r["url"], "server_key": netsrc.DEV + r["id"], "server_keyname": kname,
            "server_keykind": kind}
