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
    port, tport, fp = srv
    monkeypatch.setattr(srvadmin, "_addresses", lambda: ["127.0.0.1"])
    # adres wpisany przy parowaniu przestał działać (np. inna sieć) — klient zna inne od serwera
    cfg = {"server_url": f"http://127.0.0.1:{port}", "server_key": KEY}
    assert netsrc.info(cfg, fresh=True)["ok"]
    st = netsrc._store()
    assert st["fp"] == fp and any(e["url"] == f"https://127.0.0.1:{tport}" for e in st["endpoints"])
    assert (paths.DATA / "serwer-adresy.json").is_file()
    # zwykły port działa → wybrany przed szyfrowanym (ten sam rodzaj adresu)
    assert netsrc.best(cfg, fresh=True) == ("http", "127.0.0.1", port)
    # wpisany adres nie odpowiada, zwykły port też nie — zostaje szyfrowany
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    dport = dead.getsockname()[1]
    dead.close()
    cfg2 = {"server_url": f"http://127.0.0.1:{dport}", "server_key": KEY}
    monkeypatch.setitem(netsrc._learned, "store", {"fp": fp, "endpoints": [
        {"url": f"http://127.0.0.1:{dport}", "kind": "lan"}, {"url": f"https://127.0.0.1:{tport}", "kind": "lan"}]})
    assert netsrc.best(cfg2, fresh=True) == ("https", "127.0.0.1", tport)
    host, p, _k = netsrc.endpoint(cfg2)
    assert (host, p) == ("127.0.0.1", tport) and netsrc._PINS[(host, p)] == fp
    # cały klient przez HTTPS: info i folder profili
    netsrc._info.clear()
    assert netsrc.info(cfg2, fresh=True)["ok"]


def test_https_without_known_fingerprint_is_never_used(srv):
    _port, tport, _fp = srv
    cfg = {"server_url": f"https://127.0.0.1:{tport}", "server_key": KEY}
    assert netsrc.candidates(cfg) == []                  # bez odcisku — nie ufamy


def test_pairing_response_carries_fingerprint(srv, monkeypatch):
    port, tport, fp = srv
    monkeypatch.setattr(srvadmin, "ENABLED", True)
    code = srvadmin.pair_code(None)["code"]
    r = netsrc.pair(f"127.0.0.1:{port}", code, "Laptop")
    assert r["ok"] and r["tls"]["fp"] == fp
    cfg = {"server_url": r["url"], "server_key": r["key"]}
    netsrc.learn(cfg, r)
    assert netsrc.server_fp(cfg) == fp
