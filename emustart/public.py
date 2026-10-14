"""public — dostęp do serwera z internetu: port publiczny przez UPnP (0.35).

Włączany w panelu serwera („Dostęp z internetu”, config: `server_public`). Serwer:
- otwiera osobny port szyfrowany (domyślnie 8742, `server_public_port`) — tylko HTTPS
  z przypiętym certyfikatem (emustart/tls.py);
- prosi router (UPnP) o przekierowanie tego portu i odnawia je co RENEW s; odczytuje
  adres zewnętrzny i podaje go klientom w liście adresów (rodzaj „public” — klient bierze
  go dopiero, gdy sieć lokalna i Tailscale nie odpowiadają);
- wykrywa adres za NAT-em operatora (CGNAT) — wtedy port publiczny nie ma sensu.

Port publiczny jest węższy niż pozostałe: tylko API /v1 i grafiki (/media, /assets, /local), każde zapytanie
z tokenem urządzenia (bez tokenu tylko logowanie: prośba, stan prośby, podpis), bez panelu
i interfejsu. Seria złych kluczy z jednego adresu = przerwa
dla tego adresu; ograniczona liczba połączeń naraz z jednego adresu.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import threading
import time

log = logging.getLogger("emustart.public")

PORT_DEFAULT = 8742
RENEW = 20 * 60
BAN_FAILS = 20
BAN_WINDOW = 600
BAN_TIME = 1800
MAX_CONN_IP = 32
FW_NAME = "EmuStart Server (internet)"
# API i grafiki (okładki, logo systemów i paczka logo w /media/, wbudowane w /assets/)
ALLOWED = ("/v1/", "/media/", "/assets/")
OPEN_PATHS = ("/v1/enroll/request", "/v1/enroll/poll", "/v1/auth/challenge", "/v1/auth/token")

STATE: dict = {"enabled": False, "port": 0, "router": "", "ext_ip": "", "mapped": False, "lease": 0,
               "error": "", "cgnat": False, "checked": 0.0, "reach": "", "last_inbound": 0.0, "last_ip": ""}
_lock = threading.Lock()
_fails: dict = {}
_banned: dict = {}
_conns: dict = {}
_srv = None
CTX = None                       # kontekst TLS serwera (main._start_tls) — do włączenia z panelu
_stop = threading.Event()
_gw = None


def _local(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return a.is_private or a.is_loopback or a.is_link_local or a in ipaddress.ip_network("100.64.0.0/10")


# ── połączenia i zapytania na porcie publicznym ──
def _loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False


def client_ip(handler) -> str:
    """Adres klienta: przez tunel Cloudflare (cloudflared na tym komputerze łączy się
    z 127.0.0.1) — z nagłówka CF-Connecting-IP; inaczej — adres połączenia."""
    peer = handler.client_address[0]
    if _loopback(peer):
        cf = (handler.headers.get("CF-Connecting-IP") or "").strip()
        if cf:
            return cf
    return peer


def conn_open(ip: str) -> bool:
    if _loopback(ip):
        return True                  # tunel: wszyscy z 127.0.0.1 — limity po adresie z nagłówka (gate)
    now = time.time()
    with _lock:
        if _banned.get(ip, 0) > now:
            return False
        if _conns.get(ip, 0) >= MAX_CONN_IP:
            return False
        _conns[ip] = _conns.get(ip, 0) + 1
    return True


def conn_close(ip: str) -> None:
    with _lock:
        n = _conns.get(ip, 0) - 1
        if n > 0:
            _conns[ip] = n
        else:
            _conns.pop(ip, None)


def _fail(ip: str) -> None:
    now = time.time()
    with _lock:
        f = [t for t in _fails.get(ip, []) if now - t < BAN_WINDOW] + [now]
        _fails[ip] = f
        if len(f) >= BAN_FAILS:
            _banned[ip] = now + BAN_TIME
            _fails.pop(ip, None)
            log.warning("port publiczny: %s zablokowany na %d min (seria złych kluczy)", ip, BAN_TIME // 60)


def gate(handler) -> bool:
    """Zapytanie na porcie publicznym: True = obsłuż dalej; False = odpowiedź już wysłana."""
    from urllib.parse import urlsplit
    from emustart import remote
    from emustart.server import refuse
    ip = client_ip(handler)
    path = urlsplit(handler.path).path
    if _banned.get(ip, 0) > time.time():
        refuse(handler, 403)
        return False
    if not path.startswith(ALLOWED):
        refuse(handler, 404)
        return False
    if path in OPEN_PATHS:
        return True                  # dołączanie i logowanie podpisem — bez klucza (emustart/enroll.py)
    if not remote.authorized(handler):
        _fail(ip)
        refuse(handler, 401)
        return False
    if not _local(ip) and ip != STATE["ext_ip"]:          # własny test serwera się nie liczy
        STATE["last_inbound"], STATE["last_ip"] = time.time(), ip
    return True


# ── przekierowanie na routerze ──
def endpoint() -> dict | None:
    """Adres publiczny dla listy adresów serwera (gdy działa)."""
    if STATE["enabled"] and STATE["mapped"] and STATE["ext_ip"] and not STATE["cgnat"]:
        return {"url": f"https://{STATE['ext_ip']}:{STATE['port']}", "kind": "public", "tls": True}
    return None


def _refresh() -> None:
    from emustart import upnp
    global _gw
    port = STATE["port"]
    try:
        if _gw is None:
            _gw = upnp.find_gateway(known=_known_router())
            log.info("router UPnP: %s (ten komputer %s)", _gw.location, _gw.local_ip)
            _remember_router(_gw.location)
        ext = _gw.external_ip()
        cgnat = bool(ext) and _local(ext)
        STATE.update(router=_gw.location, ext_ip=ext, cgnat=cgnat, checked=time.time())
        if cgnat:
            STATE.update(mapped=False, error=f"router ma adres {ext} za NAT-em operatora (CGNAT) — z internetu "
                                             "nie da się do niego dojść; zostaje Tailscale (albo tunel)")
            return
        lease = _gw.add(port, port)
        STATE.update(mapped=True, lease=lease, error="")
        _self_check(ext, port)
    except upnp.UpnpError as ex:
        _gw = None if ex.code not in (718,) else _gw
        STATE.update(mapped=False, error=str(ex), checked=time.time())
        log.warning("UPnP: %s", ex)
    except OSError as ex:
        _gw = None
        STATE.update(mapped=False, error=f"router: {ex}", checked=time.time())
        log.warning("UPnP: %s", ex)


def _router_file():
    from emustart import paths
    return paths.SERVER / "router.json"


def _known_router() -> str:
    """Adres opisu routera z poprzedniego razu (UPnP bywa niesłowne przy wyszukiwaniu)."""
    import json
    try:
        return json.loads(_router_file().read_text(encoding="utf-8")).get("location", "")
    except (OSError, ValueError):
        return ""


def _remember_router(location: str) -> None:
    import json
    try:
        _router_file().parent.mkdir(parents=True, exist_ok=True)
        _router_file().write_text(json.dumps({"location": location}), encoding="utf-8")
    except OSError:
        pass


# Sprawdzenie, że odpowiada nasz serwer, bez klucza: wyzwanie logowania dla pustego id.
# /v1/info bez klucza na porcie publicznym to nieudane logowanie — liczone do blokady adresu
# (własny adres zewnętrzny = adres domu) i odrzucane ogólnym 401, nie do odróżnienia od obcego.
PROBE_PATH = "/v1/auth/challenge"
PROBE_BODY = b'{"device": ""}'


def ours(status: int, body: bytes) -> bool:
    """Odpowiedź serwera EmuStart na PROBE (nieznane urządzenie)."""
    try:
        d = json.loads(body or b"{}")
    except ValueError:
        return False
    return status in (200, 403) and isinstance(d, dict) and "reason" in d and d.get("ok") is False


def _self_check(ext: str, port: int) -> None:
    """Próba połączenia z własnym adresem zewnętrznym (działa, gdy router umie „zawrócić”
    ruch — NAT loopback). Niepowodzenie nie przesądza: decyduje połączenie z zewnątrz."""
    from emustart import netsrc, remote
    fp = (remote.TLS or {}).get("fp", "")
    if not fp:
        return
    try:
        c = netsrc.PinnedHTTPS(ext, port, fp, 5)
        c.request("POST", PROBE_PATH, body=PROBE_BODY, headers={"Content-Type": "application/json"})
        r = c.getresponse()
        ok = ours(r.status, r.read())
        c.close()
    except Exception:
        ok = False
    STATE["reach"] = "ok" if ok else "unknown"


def _loop() -> None:
    while not _stop.is_set():
        _refresh()
        _stop.wait(RENEW if STATE["mapped"] else 60)    # bez routera — kolejna próba za minutę


def start(cfg: dict, ctx) -> dict:
    """Włącza port publiczny (gdy `server_public`). Zwraca STATE."""
    global _srv
    from emustart import remote, server
    if not cfg.get("server_public") or _srv is not None or ctx is None:
        return STATE
    port = int(cfg.get("server_public_port") or PORT_DEFAULT)
    try:
        server.start_public(port, ctx)
    except OSError as ex:
        STATE.update(enabled=False, error=f"port {port} zajęty: {ex}")
        return STATE
    _srv = True
    remote.ensure_firewall(port, FW_NAME)
    STATE.update(enabled=True, port=port, error="")
    _stop.clear()
    threading.Thread(target=_loop, daemon=True, name="upnp").start()
    log.info("port publiczny %d włączony", port)
    return STATE


def stop() -> None:
    """Wyłączenie: przekierowanie na routerze usunięte, port przestaje przyjmować zapytania."""
    global _srv
    from emustart import server
    _stop.set()
    if _gw is not None and STATE["mapped"]:
        try:
            _gw.delete(STATE["port"])
        except Exception as ex:
            log.info("UPnP usuwanie przekierowania: %s", ex)
    server.stop_public()
    _srv = None
    STATE.update(enabled=False, mapped=False, reach="")
    log.info("port publiczny wyłączony")


def status() -> dict:
    with _lock:
        banned = sum(1 for t in _banned.values() if t > time.time())
    if TUNNEL["url"] and time.time() - TUNNEL["checked"] > 300:
        check_tunnel_later()
    return {**STATE, "banned": banned, "now": time.time(), "tunnel": dict(TUNNEL)}


# ── tunel (Cloudflare Tunnel → port publiczny) ──
TUNNEL: dict = {"url": "", "state": "", "error": "", "checked": 0.0}


def tunnel_endpoint() -> dict | None:
    """Adres tunelu dla listy adresów serwera: zwykły certyfikat (ca), rodzaj „tunnel” —
    urządzenia biorą go na końcu (po sieci lokalnej, Tailscale i porcie publicznym)."""
    if TUNNEL["url"]:
        return {"url": TUNNEL["url"], "kind": "tunnel", "tls": True, "ca": True}
    return None


def set_tunnel(url: str) -> str:
    """Adres tunelu z ustawień (`server_tunnel_url`) — znormalizowany, '' = brak."""
    url = (url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    if url and not url.startswith("https://"):
        raise ValueError("adres tunelu musi zaczynać się od https://")
    TUNNEL.update(url=url, state="", error="", checked=0.0)
    if url:
        check_tunnel_later()
    return url


def check_tunnel() -> None:
    """Czy serwer odpowiada przez tunel (zapytanie przez internet, bez klucza — PROBE)."""
    import ssl
    import urllib.request
    url = TUNNEL["url"]
    if not url:
        return
    req = urllib.request.Request(url + PROBE_PATH, data=PROBE_BODY, method="POST",
                                 headers={"User-Agent": "EmuStart-serwer", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as r:
            ok = ours(r.status, r.read())
        TUNNEL.update(state="ok" if ok else "error", error="" if ok else "nieoczekiwana odpowiedź")
    except Exception as ex:
        code = getattr(ex, "code", None)
        if code and ours(code, ex.read() if hasattr(ex, "read") else b""):
            TUNNEL.update(state="ok", error="", checked=time.time())      # odpowiedział nasz serwer
            log.info("tunel %s działa", url)
            return
        TUNNEL.update(state="error", error=(f"odpowiedź {code}" + (" — tunel nie dociera do serwera (502: "
                      "sprawdź usługę https://localhost:8742 i „Disable TLS verification”)" if code == 502 else ""))
                      if code else str(ex)[:200])
    TUNNEL["checked"] = time.time()
    if TUNNEL["state"] == "ok":
        log.info("tunel %s działa", url)
    else:
        log.warning("tunel %s: %s", url, TUNNEL["error"])


def check_tunnel_later() -> None:
    TUNNEL["checked"] = time.time()          # bez wielokrotnych prób naraz
    threading.Thread(target=check_tunnel, daemon=True, name="tunel").start()
