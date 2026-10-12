"""upnp — przekierowanie portu na routerze (UPnP IGD) i adres zewnętrzny, bez bibliotek.

1. `discover()` — SSDP M-SEARCH (239.255.255.250:1900) → adresy opisów urządzeń.
2. `Gateway.from_location()` — opis XML routera → usługa WANIPConnection / WANPPPConnection
   (adres sterowania SOAP) i adres tego komputera w sieci routera.
3. `external_ip()`, `add()`, `delete()` — akcje SOAP.

Przekierowanie ma czas życia (LEASE); serwer odnawia je co kilkanaście minut, więc po
wyłączeniu serwera samo wygasa. Routery, które przyjmują tylko stałe przekierowania
(błąd 725), dostają czas 0.
"""

from __future__ import annotations

import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

log = logging.getLogger("emustart.upnp")

SSDP = ("239.255.255.250", 1900)
TARGETS = ("urn:schemas-upnp-org:device:InternetGatewayDevice:1",
           "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
           "urn:schemas-upnp-org:service:WANIPConnection:1",
           "urn:schemas-upnp-org:service:WANIPConnection:2",
           "urn:schemas-upnp-org:service:WANPPPConnection:1")
SERVICES = ("WANIPConnection", "WANPPPConnection")
LEASE = 3600


class UpnpError(Exception):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


def lan_addresses() -> list:
    """Adresy IPv4 tego komputera w sieciach lokalnych (bez Tailscale i pętli zwrotnej)."""
    import ipaddress
    out = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            a = info[4][0]
            ip = ipaddress.ip_address(a)
            if ip.is_private and not ip.is_loopback and ip not in ipaddress.ip_network("100.64.0.0/10") \
                    and a not in out:
                out.append(a)
    except OSError:
        pass
    return out


def _search(src: str, timeout: float) -> list:
    """M-SEARCH z jednej karty sieciowej (src = jej adres; '' = domyślna)."""
    found: list = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if src:
            s.bind((src, 0))
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(src))
        s.settimeout(timeout)
        for st in TARGETS:
            msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                   f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {st}\r\n\r\n')
            s.sendto(msg.encode("ascii"), SSDP)
        while True:
            try:
                data, _addr = s.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            m = re.search(rb"^location:\s*(\S+)", data, re.I | re.M)
            if m:
                loc = m.group(1).decode("ascii", "replace")
                if loc not in found:
                    found.append(loc)
    except OSError as ex:
        log.info("SSDP z %s: %s", src or "domyślnej karty", ex)
    finally:
        s.close()
    return found


def discover(timeout: float = 3.0, rounds: int = 3) -> list:
    """Adresy opisów (LOCATION) bram internetowych w sieci lokalnej — zapytanie z każdej karty
    sieciowej osobno (domyślna bywa kartą Tailscale albo wirtualną), kilka prób (UDP ginie)."""
    found: list = []
    srcs = lan_addresses() or [""]
    for _ in range(rounds):
        for src in srcs:
            for loc in _search(src, timeout):
                if loc not in found:
                    found.append(loc)
        if found:
            break
    return found


def _local_ip_towards(host: str, port: int) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, port or 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


class Gateway:
    def __init__(self, control: str, service: str, local_ip: str, location: str):
        self.control, self.service, self.local_ip, self.location = control, service, local_ip, location

    @classmethod
    def from_location(cls, location: str, timeout: float = 5) -> "Gateway":
        with urllib.request.urlopen(location, timeout=timeout) as r:
            root = ET.fromstring(r.read())
        base = location
        for el in root.iter():
            if _tag(el) == "URLBase" and (el.text or "").strip():
                base = el.text.strip()
        best = None
        for svc in root.iter():
            if _tag(svc) != "service":
                continue
            f = {_tag(c): (c.text or "").strip() for c in svc}
            stype = f.get("serviceType", "")
            if any(f":{name}:" in stype for name in SERVICES) and f.get("controlURL"):
                cand = (stype, urllib.parse.urljoin(base, f["controlURL"]))
                if best is None or "WANIPConnection" in stype:      # IP przed PPP
                    best = cand
        if not best:
            raise UpnpError("router nie udostępnia przekierowań portów (brak usługi WAN*Connection)")
        u = urllib.parse.urlsplit(best[1])
        return cls(best[1], best[0], _local_ip_towards(u.hostname or "", u.port or 80), location)

    def _soap(self, action: str, args: dict, timeout: float = 8) -> dict:
        body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
        env = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
               's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
               f'<u:{action} xmlns:u="{self.service}">{body}</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(self.control, data=env.encode("utf-8"), method="POST", headers={
            "Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{self.service}#{action}"'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                root = ET.fromstring(r.read())
        except urllib.error.HTTPError as ex:
            text = ex.read().decode("utf-8", "replace")
            code = re.search(r"<errorCode>(\d+)</errorCode>", text)
            desc = re.search(r"<errorDescription>([^<]*)</errorDescription>", text)
            raise UpnpError(f"{action}: {desc.group(1) if desc else ex.code}",
                            int(code.group(1)) if code else ex.code) from None
        except (OSError, ET.ParseError) as ex:
            raise UpnpError(f"{action}: {ex}") from None
        out = {}
        for el in root.iter():
            if len(el) == 0 and el.text is not None:
                out[_tag(el)] = el.text.strip()
        return out

    def external_ip(self) -> str:
        return self._soap("GetExternalIPAddress", {}).get("NewExternalIPAddress", "")

    def add(self, ext_port: int, int_port: int, desc: str = "EmuStart", lease: int = LEASE) -> int:
        """Przekierowanie TCP ext_port → ten komputer:int_port. Zwraca użyty czas życia."""
        args = {"NewRemoteHost": "", "NewExternalPort": ext_port, "NewProtocol": "TCP",
                "NewInternalPort": int_port, "NewInternalClient": self.local_ip, "NewEnabled": 1,
                "NewPortMappingDescription": desc, "NewLeaseDuration": lease}
        try:
            self._soap("AddPortMapping", args)
            return lease
        except UpnpError as ex:
            if ex.code == 725 and lease:                 # tylko stałe przekierowania
                args["NewLeaseDuration"] = 0
                self._soap("AddPortMapping", args)
                return 0
            if ex.code == 718:
                raise UpnpError(f"port {ext_port} na routerze jest już przekierowany na inny komputer", 718) from None
            raise

    def delete(self, ext_port: int) -> None:
        self._soap("DeletePortMapping", {"NewRemoteHost": "", "NewExternalPort": ext_port, "NewProtocol": "TCP"})


def find_gateway(timeout: float = 3.0, known: str = "") -> Gateway:
    """Router z UPnP: najpierw zapamiętany adres opisu (`known`), potem wyszukiwanie."""
    errors = []
    if known:
        try:
            return Gateway.from_location(known)
        except (UpnpError, OSError, ET.ParseError) as ex:
            errors.append(f"{known}: {ex}")
    for loc in discover(timeout):
        try:
            return Gateway.from_location(loc)
        except (UpnpError, OSError, ET.ParseError) as ex:
            errors.append(f"{loc}: {ex}")
    raise UpnpError("nie znaleziono routera z UPnP" + (f" ({'; '.join(errors)[:300]})" if errors else
                                                        " — włączone na routerze?"))
