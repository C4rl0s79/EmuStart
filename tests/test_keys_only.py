"""0.39: urządzenia łączą się tylko kluczem urządzenia (logowanie podpisem).

Stare klucze-hasła działają wyłącznie do przejścia na klucz urządzenia (/v1/auth/upgrade)
i sprawdzenia serwera (/v1/info); wspólny klucz serwera — tylko na komputerze serwera."""

import secrets
import time

import keyhelp

from emustart import devices, netsrc, srvadmin
from test_three_devices import _req, srv  # noqa: F401  (fixture)


def _legacy(name="Stary telefon", cls="phone", account="Jezus"):
    """Wpis sprzed 0.39: klucz-hasło (skrót), bez klucza urządzenia."""
    key, did = secrets.token_urlsafe(32), secrets.token_hex(6)
    with devices._lock:
        devices._load()[did] = {"name": name, "class": cls, "key": devices._h(key), "created": time.time(),
                                "last_seen": time.time(), "ip": "", "version": "0.38.3", "account": account}
        devices._save()
    return did, key


def test_pairing_without_device_key_is_refused(srv):
    code = srvadmin.pair_code(None)["code"]
    st, d = _req(srv, "POST", "/v1/pair", body={"code": code, "name": "Stary PC", "class": "pc"})
    assert not d["ok"] and "klucza urządzenia" in d["reason"]
    st, d = _req(srv, "POST", "/v1/pair/request", body={"name": "Stary PC", "class": "pc"})
    assert not d["ok"] and "klucza urządzenia" in d["reason"]
    # kod nie przepadł — nowa wersja z kluczem paruje się nim
    host, port, _n = srv
    tok, did, _k = keyhelp.pair(host, port, "Nowy PC", "pc", code=code)
    assert "key" not in devices.listing()[0] and _req(srv, "GET", "/v1/info", tok)[0] == 200


def test_legacy_key_only_reaches_info_and_upgrade(srv):
    did, key = _legacy()
    assert _req(srv, "GET", "/v1/info", key)[0] == 200                 # „serwer odpowiada, przejdź na klucz”
    assert _req(srv, "GET", "/v1/library", key)[0] == 401              # gry — już nie
    assert _req(srv, "GET", "/v1/systems", key)[0] == 401


def test_legacy_device_upgrades_to_device_key_in_place(srv):
    host, port, _n = srv
    did, key = _legacy()
    k, pem = keyhelp.new_key()
    st, r = _req(srv, "POST", "/v1/auth/upgrade", key, body={"pubkey": pem, "hw": "a" * 32})
    assert st == 200 and r == {"ok": True, "id": did}                  # ten sam wpis — bez duplikatu
    d = devices._load()[did]
    assert d["pubkey"] == pem and "key" not in d and d["account"] == "Jezus" and d["role"] == "admin"
    assert _req(srv, "GET", "/v1/info", key)[0] == 401                 # stary klucz przestał działać
    tok = keyhelp.login(host, port, did, k)
    assert _req(srv, "GET", "/v1/library", tok)[0] == 200
    # drugi raz — nie (urządzenie ma już klucz); obcy wpis z kluczem nie przejmie cudzego
    st, r = _req(srv, "POST", "/v1/auth/upgrade", tok, body={"pubkey": keyhelp.new_key()[1]})
    assert not r["ok"]


def test_upgrade_needs_valid_public_key(srv):
    did, key = _legacy()
    st, r = _req(srv, "POST", "/v1/auth/upgrade", key, body={"pubkey": "nie-klucz"})
    assert not r["ok"] and not devices._load()[did].get("pubkey")
    assert _req(srv, "POST", "/v1/auth/upgrade", "", body={"pubkey": keyhelp.new_key()[1]})[0] == 401


def test_shared_server_token_works_only_on_server_machine(srv):
    from emustart import remote
    token = remote.API._cfg["server_token"]
    assert _req(srv, "GET", "/v1/info", token)[0] == 200                # ten komputer (127.0.0.1)
    assert _req(srv, "GET", "/v1/info", token, headers={"CF-Connecting-IP": "81.2.69.20"})[0] == 401


def test_pc_upgrades_legacy_key_at_start(srv, monkeypatch):
    """Komputer ze starym kluczem-hasłem przy starcie przechodzi na klucz urządzenia."""
    from emustart import api as api_mod
    host, port, _n = srv
    did, key = _legacy("Laptop", "pc")
    saved = {}
    monkeypatch.setattr("emustart.config.save", lambda c: saved.update(c))
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg = {"server_url": f"http://{host}:{port}", "server_key": key}
    a._upgrade_legacy_key()
    assert a._cfg["server_key"] == netsrc.DEV + did and a._cfg["server_keyname"]
    assert devices._load()[did].get("pubkey") and saved["server_key"] == netsrc.DEV + did
    netsrc.use_device(a._cfg)
    assert netsrc.info(a._cfg).get("version")                           # logowanie podpisem działa


def test_panel_no_longer_hands_out_shared_key():
    assert "key" not in srvadmin.ACTIONS
