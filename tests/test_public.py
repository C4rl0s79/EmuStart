"""Dostęp z internetu (0.35): UPnP na udawanym routerze i ograniczenia portu publicznego."""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("cryptography")

from emustart import netsrc, paths, public, remote, server, tls, upnp  # noqa: E402

KEY = "k" * 32
DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<deviceType>urn:schemas-upnp-org:device:InternetGatewayDevice:1</deviceType><deviceList><device>
<deviceList><device><serviceList><service>
<serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
<controlURL>/ctl/IPConn</controlURL></service></serviceList></device></deviceList>
</device></deviceList></device></root>"""


class _Router(BaseHTTPRequestHandler):
    ext_ip = "81.2.69.142"
    only_permanent = False
    taken = False
    mappings: dict = {}

    def log_message(self, *_a):
        pass

    def _send(self, code, body):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send(200, DESC)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        action = self.headers["SOAPAction"].strip('"').split("#")[1]
        val = lambda k: (re.search(f"<{k}>([^<]*)</{k}>", body) or [None, ""])[1]   # noqa: E731
        err = lambda code, d: self._send(500, f"<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/' xmlns:u='urn:x'><s:Body><s:Fault><detail><UPnPError xmlns='urn:schemas-upnp-org:control-1-0'>"  # noqa: E731
                                              f"<errorCode>{code}</errorCode><errorDescription>{d}</errorDescription>"
                                              f"</UPnPError></detail></s:Fault></s:Body></s:Envelope>")
        if action == "GetExternalIPAddress":
            return self._send(200, f"<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/' xmlns:u='urn:x'><s:Body><u:R><NewExternalIPAddress>{self.ext_ip}"
                                   "</NewExternalIPAddress></u:R></s:Body></s:Envelope>")
        if action == "AddPortMapping":
            if _Router.taken:
                return err(718, "ConflictInMappingEntry")
            if _Router.only_permanent and val("NewLeaseDuration") != "0":
                return err(725, "OnlyPermanentLeasesSupported")
            _Router.mappings[val("NewExternalPort")] = (val("NewInternalClient"), val("NewInternalPort"),
                                                         val("NewLeaseDuration"))
            return self._send(200, "<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/' xmlns:u='urn:x'><s:Body/></s:Envelope>")
        if action == "DeletePortMapping":
            _Router.mappings.pop(val("NewExternalPort"), None)
            return self._send(200, "<s:Envelope xmlns:s='http://schemas.xmlsoap.org/soap/envelope/' xmlns:u='urn:x'><s:Body/></s:Envelope>")
        err(401, "Invalid Action")


@pytest.fixture()
def router():
    _Router.mappings, _Router.only_permanent, _Router.taken, _Router.ext_ip = {}, False, False, "81.2.69.142"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Router)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/desc.xml"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _clean_public(monkeypatch):
    monkeypatch.setattr(public, "STATE", dict(public.STATE, enabled=False, mapped=False, ext_ip="", error="",
                                              cgnat=False, reach="", last_inbound=0.0, last_ip=""))
    monkeypatch.setattr(public, "_gw", None)
    public._fails.clear(), public._banned.clear(), public._conns.clear()
    yield
    server.stop_public()
    monkeypatch.setattr(public, "_srv", None)


def test_gateway_maps_port_and_reads_external_ip(router):
    gw = upnp.Gateway.from_location(router)
    assert gw.control.endswith("/ctl/IPConn") and "WANIPConnection" in gw.service and gw.local_ip
    assert gw.external_ip() == "81.2.69.142"
    assert gw.add(8742, 8742) == upnp.LEASE and _Router.mappings["8742"][1] == "8742"
    gw.delete(8742)
    assert "8742" not in _Router.mappings


def test_router_with_permanent_leases_only(router):
    _Router.only_permanent = True
    assert upnp.Gateway.from_location(router).add(8742, 8742) == 0
    _Router.taken = True
    with pytest.raises(upnp.UpnpError, match="inny komputer"):
        upnp.Gateway.from_location(router).add(8742, 8742)


def test_refresh_publishes_public_endpoint_and_detects_cgnat(router, monkeypatch):
    monkeypatch.setattr(upnp, "find_gateway", lambda timeout=3.0, known="": upnp.Gateway.from_location(router))
    monkeypatch.setattr(public, "_self_check", lambda ext, port: None)
    public.STATE.update(enabled=True, port=8742)
    public._refresh()
    assert public.STATE["mapped"] and public.endpoint()["url"] == "https://81.2.69.142:8742"
    monkeypatch.setattr(public, "_gw", None)
    _Router.ext_ip = "100.72.1.9"                       # adres za NAT-em operatora
    public._refresh()
    assert public.STATE["cgnat"] and not public.STATE["mapped"] and public.endpoint() is None
    assert "CGNAT" in public.STATE["error"]


class _Api:
    _cfg = {"server_token": KEY}
    _scan: dict = {}


def _req(port, fp, method, path, key="", extra=None):
    c = netsrc.PinnedHTTPS("127.0.0.1", port, fp, 5)
    try:
        c.request(method, path, body=b"{}" if method == "POST" else None,
                  headers={**({"Authorization": f"Bearer {key}"} if key else {}), "X-EmuStart": "1",
                           "Host": f"127.0.0.1:{port}", **(extra or {})})
        return c.getresponse().status
    finally:
        c.close()


def test_public_port_only_api_with_key_and_bans_guessers(monkeypatch):
    monkeypatch.setattr(remote, "API", _Api())
    monkeypatch.setattr(public, "BAN_FAILS", 5)
    cert, key, fp = tls.ensure(paths.SERVER)
    port = server.start_public(0, tls.server_context(cert, key), bind="127.0.0.1")
    assert _req(port, fp, "GET", "/v1/info", KEY) == 200
    assert _req(port, fp, "GET", "/v1/info") == 401
    assert _req(port, fp, "POST", "/admin/status", KEY) == 404          # panel — nigdy z internetu
    assert _req(port, fp, "POST", "/v1/pair/request", KEY) == 404       # parowanie — tylko lokalnie
    assert _req(port, fp, "GET", "/index.html", KEY) == 404             # interfejs — nie
    assert _req(port, fp, "GET", "/assets/systems/3DO.png", KEY) == 200        # logo wbudowane — przepuszczone
    assert _req(port, fp, "GET", "/assets/systems/nie-ma.png") == 401          # ale tylko z kluczem
    # tunel Cloudflare: połączenie z 127.0.0.1, klient w nagłówku CF-Connecting-IP —
    # blokada dotyczy zgadującego, nie wszystkich przychodzących tunelem
    for _ in range(5):
        _req(port, fp, "GET", "/v1/info", "zgaduje", {"CF-Connecting-IP": "81.2.69.10"})
    assert _req(port, fp, "GET", "/v1/info", KEY, {"CF-Connecting-IP": "81.2.69.10"}) == 403
    # wspólny klucz serwera przez tunel nie działa (0.39 — tylko na komputerze serwera); bez blokady
    assert _req(port, fp, "GET", "/v1/info", KEY, {"CF-Connecting-IP": "81.2.69.11"}) == 401
    import keyhelp
    from emustart import enroll
    _did, tok = keyhelp.device("Telefon", name="Telefon")
    enroll._tokens["sesja-telefonu"] = enroll._tokens.pop(tok)        # zalogowany kluczem urządzenia
    assert _req(port, fp, "GET", "/v1/info", "sesja-telefonu", {"CF-Connecting-IP": "81.2.69.11"}) == 200
    assert public.status()["banned"] == 1
    assert public.STATE["last_ip"] == "81.2.69.11"


def test_plain_ports_refuse_internet_clients():
    class H:
        def __init__(self, ip):
            self.client_address = (ip, 5555)
            self.server = type("S", (), {"server_address": ("0.0.0.0", 8740)})()
            self.close_connection = False
            self.sent = None

        def send_error(self, code):
            self.sent = code
    for ip in ("127.0.0.1", "192.168.1.20", "10.0.0.5", "100.85.254.31", "::ffff:192.168.1.7"):
        assert server._near(H(ip)), ip
    h = H("81.2.69.142")
    assert not server._near(h) and h.sent == 403 and h.close_connection


def test_known_router_is_tried_first(router, monkeypatch):
    monkeypatch.setattr(upnp, "discover", lambda *a, **k: [])          # wyszukiwanie nic nie daje
    gw = upnp.find_gateway(known=router)
    assert gw.location == router
    public._remember_router(router)
    assert public._known_router() == router


def test_tunnel_endpoint_is_listed_with_normal_certificate(monkeypatch):
    monkeypatch.setattr(public, "TUNNEL", {"url": "", "state": "", "error": "", "checked": 0.0})
    monkeypatch.setattr(public, "check_tunnel_later", lambda: None)
    assert public.set_tunnel("emustart.emucloud.org/") == "https://emustart.emucloud.org"
    assert public.tunnel_endpoint() == {"url": "https://emustart.emucloud.org", "kind": "tunnel", "tls": True, "ca": True}
    with pytest.raises(ValueError):
        public.set_tunnel("http://emustart.emucloud.org")
    # klient: tunel ostatni w kolejności, zwykły certyfikat
    monkeypatch.setitem(netsrc._learned, "store", {"fp": "a" * 64, "endpoints": [
        {"url": "http://192.168.1.130:8740", "kind": "lan"}, {"url": "https://81.2.69.142:8742", "kind": "public", "tls": True},
        public.tunnel_endpoint()]})
    cands = netsrc.candidates({"server_url": "http://100.85.254.31:8740", "server_key": KEY})
    assert all(sp[0] == "https" for sp in cands)                    # zwykłe http — pominięte (0.43)
    order = sorted(cands, key=lambda sp: (netsrc._kind(sp), sp[0] == "https"))
    assert order[-1] == ("https", "emustart.emucloud.org", 443) and netsrc._mode(order[-1]) == "ca"
    assert order[0] == ("https", "81.2.69.142", 8742) and netsrc._mode(order[0]) == "pin"


def test_reachability_probe_recognises_us_without_counting_failures(monkeypatch):
    """Kontrola tunelu i portu publicznego: bez klucza, bez liczenia do blokady adresu
    (0.39–0.41: /v1/info bez klucza → 401, „tunel nie działa” i nieudane logowanie z adresu domu)."""
    monkeypatch.setattr(remote, "API", _Api())
    cert, key, fp = tls.ensure(paths.SERVER)
    port = server.start_public(0, tls.server_context(cert, key), bind="127.0.0.1")
    public._fails.clear()
    for _ in range(3):
        c = netsrc.PinnedHTTPS("127.0.0.1", port, fp, 5)
        c.request("POST", public.PROBE_PATH, body=public.PROBE_BODY,
                  headers={"Content-Type": "application/json", "CF-Connecting-IP": "81.2.69.50"})
        r = c.getresponse()
        assert public.ours(r.status, r.read())
        c.close()
    assert not public._fails.get("81.2.69.50")
    assert not public.ours(401, b"<html>Unauthorized</html>") and not public.ours(200, b'{"version": "x"}')
