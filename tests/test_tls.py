"""Port szyfrowany serwera (emustart/tls.py) i wybór adresu serwera w kliencie (netsrc)."""

from __future__ import annotations

import socket
import ssl
import time

import pytest

pytest.importorskip("cryptography")

from emustart import nasfs, netsrc, paths, remote, server, srvadmin, tls  # noqa: E402

KEY = "k" * 32


class _Api:
    _cfg = {"server_token": KEY}
    _scan: dict = {}


@pytest.fixture()
def srv(monkeypatch):
    monkeypatch.setattr(remote, "API", _Api())
    monkeypatch.setattr(remote, "NAS_ROOT", None)
    url = server.start(0, bind="127.0.0.1")
    port = int(url.rsplit(":", 1)[1])
    cert, key, fp = tls.ensure(paths.SERVER)
    tport = server.start_tls(0, tls.server_context(cert, key), bind="127.0.0.1")
    monkeypatch.setattr(remote, "PORT", port)
    monkeypatch.setattr(remote, "TLS", {"port": tport, "fp": fp})
    netsrc._info.clear()
    nasfs.reset()
    return port, tport, fp


def test_certificate_is_created_once():
    a = tls.ensure(paths.SERVER)
    b = tls.ensure(paths.SERVER)
    assert a and b and a[2] == b[2] and len(a[2]) == 64


def test_pinned_https_works_and_wrong_pin_is_refused(srv):
    _port, tport, fp = srv
    c = netsrc.PinnedHTTPS("127.0.0.1", tport, fp, 5)
    c.request("GET", "/v1/info", headers=netsrc.auth_headers(KEY))
    r = c.getresponse()
    assert r.status == 200 and b'"tls"' in r.read()
    c.close()
    bad = netsrc.PinnedHTTPS("127.0.0.1", tport, "0" * 64, 5)
    with pytest.raises(ssl.SSLError):
        bad.request("GET", "/v1/info", headers=netsrc.auth_headers(KEY))


def test_garbage_on_tls_port_does_not_block_server(srv):
    _port, tport, fp = srv
    s = socket.create_connection(("127.0.0.1", tport), timeout=5)
    s.sendall(b"GET / HTTP/1.1\r\n\r\n")             # zwykły HTTP na port szyfrowany
    slow = socket.create_connection(("127.0.0.1", tport), timeout=5)   # i klient, który milczy
    c = netsrc.PinnedHTTPS("127.0.0.1", tport, fp, 5)
    t = time.monotonic()
    c.request("GET", "/v1/info", headers=netsrc.auth_headers(KEY))
    assert c.getresponse().status == 200 and time.monotonic() - t < 3
    s.close(), slow.close(), c.close()


def test_client_learns_endpoints_and_picks_best(srv, monkeypatch):
    """Klient zna tylko adresy szyfrowane (0.43): z odpowiedzi serwera przez przypięte
    połączenie uczy się listy adresów; gdy wpisany nie działa — bierze inny z listy."""
    port, tport, fp = srv
    monkeypatch.setattr(srvadmin, "_addresses", lambda: ["127.0.0.1"])
    cfg = {"server_url": f"https://127.0.0.1:{tport}", "server_fp": fp, "server_key": KEY}
    assert netsrc.info(cfg, fresh=True)["ok"]
    st = netsrc._store()
    assert st["fp"] == fp and [e["url"] for e in st["endpoints"]] == [f"https://127.0.0.1:{tport}"]
    assert (paths.DATA / "serwer-adresy.json").is_file()
    # zwykły port nie jest już adresem dla urządzeń — nawet wpisany ręcznie
    assert netsrc.candidates({**cfg, "server_url": f"http://127.0.0.1:{port}"}) == [("https", "127.0.0.1", tport)]
    # wpisany adres nie odpowiada — zostaje adres z listy serwera
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dport = dead.getsockname()[1]
    dead.close()
    cfg2 = {"server_url": f"https://127.0.0.1:{dport}", "server_fp": fp, "server_key": KEY}
    assert netsrc.best(cfg2, fresh=True) == ("https", "127.0.0.1", tport)
    host, p, _k = netsrc.endpoint(cfg2)
    assert (host, p) == ("127.0.0.1", tport) and netsrc._PINS[(host, p)] == fp
    netsrc._info.clear()
    assert netsrc.info(cfg2, fresh=True)["ok"]


def test_https_without_known_fingerprint_is_never_used(srv):
    _port, tport, _fp = srv
    cfg = {"server_url": f"https://127.0.0.1:{tport}", "server_key": KEY}
    # adres IP serwera bez zapamiętanego odcisku: nie ma czym sprawdzić certyfikatu — nie używany
    assert netsrc.candidates(cfg) == []
    assert netsrc._probe(("https", "127.0.0.1", tport), KEY, "") is None


def test_login_response_carries_addresses_and_fingerprint(srv, monkeypatch):
    """Po zatwierdzeniu logowania serwer podaje (połączeniem szyfrowanym) adresy i odcisk."""
    import keyhelp
    port, tport, fp = srv
    monkeypatch.setattr(srvadmin, "_addresses", lambda: ["127.0.0.1"])
    d = keyhelp.enroll_device("127.0.0.1", port, "Laptop", "pc", pem=keyhelp.new_key()[1])
    r = netsrc._post(f"https://127.0.0.1:{tport}", "/v1/enroll/poll", {"rid": d["rid"]}, fp=fp)
    assert r["status"] == "approved" and r["tls"]["fp"] == fp
    assert [e["url"] for e in r["endpoints"]] == [f"https://127.0.0.1:{tport}"]
    cfg = {"server_url": r["url"], "server_key": netsrc.DEV + r["device"]}
    netsrc.learn(cfg, r, trusted=True)
    assert netsrc.server_fp(cfg) == fp
    # bez odcisku zwykłe sprawdzanie certyfikatu — własny certyfikat serwera nie przejdzie
    assert not netsrc._post(f"https://127.0.0.1:{tport}", "/v1/enroll/poll", {"rid": "x"})["ok"]


def test_typed_address_domain_means_https():
    assert netsrc._split("emustart.emucloud.org") == ("https", "emustart.emucloud.org", 443)
    assert netsrc._split("100.85.254.31") == ("http", "100.85.254.31", 8740)
    assert netsrc._split("EMUSTATION") == ("http", "emustation", 8740)
    assert netsrc._split("https://emustart.emucloud.org/") == ("https", "emustart.emucloud.org", 443)


def test_dead_address_does_not_block_choosing_a_working_one(srv, monkeypatch):
    """Tailscale wyłączony: adres, który przyjmuje połączenie i milczy, nie może zablokować
    wyboru działającego (test adresów bez logowania — wcześniej logowanie pod wspólną blokadą)."""
    _port, tport, fp = srv
    hang = socket.socket()
    hang.bind(("127.0.0.2", 0))
    hang.listen(8)                                   # przyjmuje połączenia, nic nie odpowiada
    hport = hang.getsockname()[1]
    monkeypatch.setitem(netsrc._learned, "store", {"fp": fp, "endpoints": [
        {"url": f"https://127.0.0.2:{hport}", "kind": "lan"}, {"url": f"https://127.0.0.1:{tport}", "kind": "tailscale"}]})
    cfg = {"server_url": f"https://127.0.0.2:{hport}", "server_fp": fp, "server_key": netsrc.DEV + "abc",
           "server_keyname": "x", "server_keykind": "dpapi"}
    t = time.monotonic()
    assert netsrc.best(cfg, fresh=True) == ("https", "127.0.0.1", tport)
    assert time.monotonic() - t < netsrc.PROBE_TIMEOUT + 1.5
    hang.close()
