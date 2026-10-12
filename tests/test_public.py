"""Dostęp z internetu (0.35): UPnP na udawanym routerze i ograniczenia portu publicznego."""

from __future__ import annotations

import http.client
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
    monkeypatch.setattr(upnp, "find_gateway", lambda timeout=3.0: upnp.Gateway.from_location(router))
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


def _req(port, fp, method, path, key=""):
    c = netsrc.PinnedHTTPS("127.0.0.1", port, fp, 5)
    try:
        c.request(method, path, body=b"{}" if method == "POST" else None,
                  headers={**({"Authorization": f"Bearer {key}"} if key else {}), "X-EmuStart": "1",
                           "Host": f"127.0.0.1:{port}"})
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
    for _ in range(4):
        _req(port, fp, "GET", "/v1/info", "zgaduje")
    with pytest.raises((OSError, http.client.HTTPException)):          # zablokowany: połączenie zrywane
        _req(port, fp, "GET", "/v1/info", KEY)
    assert public.status()["banned"] == 1
