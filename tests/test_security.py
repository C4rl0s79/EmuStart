"""Audyt bezpieczeństwa 0.41 — każde znalezisko ma swój test."""

from __future__ import annotations

import time

import keyhelp
import pytest

from emustart import devices, enroll, nasfs, netsrc, remote, server
from test_three_devices import _req, srv  # noqa: F401  (fixture)


class _H:
    """Udawane zapytanie (adres połączenia, nagłówki, serwer)."""

    def __init__(self, ip, host="127.0.0.1:8740", headers=None, public=False, port=8740):
        self.client_address = (ip, 5555)
        self.headers = {"Host": host, **(headers or {})}
        self.server = type("S", (), {"server_address": ("0.0.0.0", port), "public": public})()


def test_ui_api_and_panel_only_from_this_computer():
    """Nagłówek Host ustawia klient — o dostępie do /api i /admin decyduje adres połączenia."""
    assert server._local_ui(_H("127.0.0.1"))
    assert server._local_ui(_H("::1", host="localhost:8740"))
    assert not server._local_ui(_H("192.168.1.20"))                   # sieć lokalna z podrobionym Host
    assert not server._local_ui(_H("100.85.1.2"))                     # Tailscale
    assert not server._local_ui(_H("127.0.0.1", headers={"CF-Connecting-IP": "81.2.69.1"}))   # tunel
    assert not server._local_ui(_H("127.0.0.1", public=True))         # port publiczny
    assert not server._local_ui(_H("127.0.0.1", host="evil.example:8740"))   # DNS rebinding


def _device(account="", role="admin"):
    with devices._lock:
        did = devices._add_keyed_locked("Urządzenie", "phone", "", "", keyhelp.new_key()[1], time.time())
        devices._load()[did].update(account=account, role=role, games=True)
        devices._save()
    tok = f"sesja-{did}"
    enroll._tokens[tok] = (did, time.time() + 600)
    return did, tok


def test_only_admin_approves_lan_requests(srv):
    """Prośba z sieci lokalnej daje pełny dostęp — urządzenie konta nie może jej zatwierdzić."""
    _did, user_tok = _device("Ola", "user")
    _adm, admin_tok = _device()
    st, req = _req(srv, "POST", "/v1/pair/request", body={"name": "Laptop", "class": "pc",
                                                         "pubkey": keyhelp.new_key()[1]})
    st, r = _req(srv, "POST", "/v1/pair/approve", user_tok, body={"code": req["code"], "allow": True})
    assert st == 403 and not r["ok"]
    st, r = _req(srv, "POST", "/v1/pair/approve", admin_tok, body={"code": req["code"], "allow": True})
    assert st == 200 and r["status"] == "approved"


@pytest.mark.parametrize("bad", ["Jezus.", "Jezus ", "Jezus./save/x.srm", "JEZUS~1/save", "CON", "con.txt",
                                 "Jezus/NUL", "a/../b", "C:x", "~admin", "Jez\x01us"])
def test_windows_path_aliases_rejected(bad):
    with pytest.raises(ValueError):
        nasfs.clean_rel(bad)


def test_normal_paths_still_fine():
    for ok in ("Jezus/save/Gra (USA).srm", "a~b/c", "Jezus/_backup/20261013/x", "Konsola/console.txt"):
        assert nasfs.clean_rel(ok) == ok


def test_alias_cannot_reach_other_account(srv):
    host, port, nas = srv
    (nas / "Jezus").mkdir()
    (nas / "Jezus" / "emustart.json").write_bytes(b"{}")
    _did, tok = _device("Ola", "user")
    for alias in ("Jezus./emustart.json", "Jezus%20/emustart.json", "JEZUS~1/emustart.json"):
        st, _r = _req(srv, "GET", f"/v1/nas/file?p={alias}", tok)
        assert st in (400, 403), alias


def test_old_pin_file_hidden_regardless_of_case():
    assert remote._is_auth("Jezus/.AUTH.JSON") and remote._is_auth("Jezus/.auth.json")


def test_hardware_header_cannot_revoke_other_devices():
    victim, _t = _device("Jezus", "user")
    devices._load()[victim]["hw"] = "a" * 32
    attacker, _t2 = _device("Ola", "user")
    devices.touch(attacker, "10.0.0.9", "0.41", "a" * 32)
    assert not devices._load()[victim].get("revoked")
    assert devices._load()[attacker].get("hw") == "a" * 32       # zapamiętany, ale nikogo nie odcina
    devices.touch(attacker, "10.0.0.9", "0.41", "b" * 32)
    assert devices._load()[attacker]["hw"] == "a" * 32            # raz ustawiony — bez zmian


def test_key_only_in_header(srv):
    _did, tok = _device()
    assert _req(srv, "GET", "/v1/info", tok)[0] == 200
    assert _req(srv, "GET", f"/v1/info?k={tok}")[0] == 401


def test_plain_response_cannot_replace_pinned_certificate():
    cfg = {}
    assert netsrc.learn(cfg, {"tls": {"fp": "a" * 64}, "endpoints": [{"url": "https://10.0.0.1:8741"}]}, trusted=False)
    assert netsrc.server_fp(cfg) == "a" * 64                     # pierwsze poznanie
    netsrc.learn(cfg, {"tls": {"fp": "e" * 64}, "endpoints": [{"url": "https://evil:443"}]}, trusted=False)
    assert netsrc.server_fp(cfg) == "a" * 64
    assert netsrc._store()["endpoints"] == [{"url": "https://10.0.0.1:8741"}]
    netsrc.learn(cfg, {"tls": {"fp": "b" * 64}}, trusted=True)  # przez szyfrowane — tak
    assert netsrc.server_fp(cfg) == "b" * 64


def test_enroll_requests_capped_overall(monkeypatch):
    monkeypatch.setattr(enroll, "MAX_PENDING", 3)
    for i in range(3):
        assert enroll.request("new", f"Konto{i}", "x", "phone", f"10.0.0.{i}", "0.41", keyhelp.new_key()[1],
                              [], password="Haslo-testowe1")["ok"]
    r = enroll.request("new", "Konto9", "x", "phone", "10.0.0.9", "0.41", keyhelp.new_key()[1], [],
                       password="Haslo-testowe1")
    assert not r["ok"] and "za dużo" in r["reason"].lower()


@pytest.mark.parametrize("name", ["CON", "nul", "_backup", "LPT1", "com3.txt"])
def test_reserved_account_names_refused(name):
    assert enroll._safe_account(name) == "" and remote._safe_profile_name(name) == ""


def test_panel_needs_local_connection_even_with_forged_host(srv):
    """Prawdziwe zapytanie z 127.0.0.1 działa; podrobiony Host z innego adresu — patrz _local_ui."""
    host, port, _nas = srv
    st, _r = _req(srv, "POST", "/admin/status", headers={"X-EmuStart": "1", "Host": f"127.0.0.1:{port}"})
    assert st == 200
