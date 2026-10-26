"""server — lokalny serwer HTTP dla UI: /web, /media (okładki), /assets (loga).

Okładki leżą poza katalogiem UI, a przesyłanie ich przez most pywebview jako
data-URI byłoby wolne przy przewijaniu tysięcy gier. Serwer słucha tylko na
127.0.0.1, na losowym porcie.
"""

from __future__ import annotations

import json
import mimetypes
import ssl
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from emustart import paths

ROUTES = {"/media/": paths.MEDIA, "/assets/": paths.ASSETS}


# tryb deweloperski (main.py --browser): UI w zwykłej przeglądarce woła API przez
# POST /api/<metoda> zamiast mostu pywebview. Domyślnie wyłączony.
DEV_API = None
# () → ustawienia: brakujące grafiki (/media/…) pobierane z serwera EmuStart — komputer
# z biblioteką z serwera. Wcześniej tylko w trybie --browser (DEV_API), więc w zwykłym
# oknie logo systemów i grafiki spoza pamięci komputera nigdy się nie pojawiały.
MEDIA_CFG = None


def _near(handler) -> bool:
    """Zwykły i szyfrowany port: tylko ten komputer, sieć lokalna i Tailscale (także tunel —
    łączy się lokalnie). Z internetu wolno wyłącznie na port publiczny (emustart/public.py) —
    nawet gdy router przekierowuje tu ruch (DMZ, zakres portów)."""
    import ipaddress
    ip = handler.client_address[0]
    try:
        a = ipaddress.ip_address(ip)
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        ok = a.is_private or a.is_loopback or a.is_link_local or a in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        ok = False
    if not ok:
        refuse(handler, 403)
        _warn_public(ip, handler.server.server_address[1])
    return ok


_warned: dict = {}
MAX_DRAIN = 1 << 20


def refuse(handler, code: int) -> None:
    """Odmowa przed przeczytaniem treści zapytania (POST/PUT/DELETE): najpierw doczytujemy
    treść (do 1 MB; większa — zamykamy połączenie), dopiero potem odpowiedź. Odpowiedź przy
    nieprzeczytanej treści Windows potrafi zerwać (RST) zamiast ją oddać — klient dostaje
    „połączenie przerwane” zamiast kodu błędu."""
    headers = getattr(handler, "headers", None) or {}
    try:
        n = int(headers.get("Content-Length") or 0)
    except ValueError:
        n = MAX_DRAIN + 1
    if 0 < n <= MAX_DRAIN:
        try:
            handler.rfile.read(n)
        except OSError:
            pass
    handler.close_connection = True
    handler.send_error(code)


def _warn_public(ip: str, port: int) -> None:
    import logging
    import time
    now = time.time()
    if now - _warned.get(port, 0) > 3600:            # raz na godzinę na port
        _warned[port] = now
        logging.getLogger("emustart.server").warning(
            "port %d: zapytanie z internetu (%s) odrzucone — router przekierowuje tu ruch z zewnątrz; "
            "z internetu tylko port publiczny (panel → Dostęp z internetu)", port, ip)


def _host_ok(handler) -> bool:
    """Tylko nasz adres w nagłówku Host — strona z internetu przepięta przez DNS
    na 127.0.0.1 (DNS rebinding) wysyła własną nazwę hosta i dostaje odmowę."""
    host = (handler.headers.get("Host") or "").lower()
    port = handler.server.server_address[1]
    return host in (f"127.0.0.1:{port}", f"localhost:{port}")


def _local_ui(handler) -> bool:
    """Interfejs, API programu (/api) i panel serwera (/admin) — wyłącznie z tego komputera.
    Rozstrzyga adres połączenia: nagłówek Host ustawia klient, więc urządzenie z sieci
    lokalnej albo Tailscale mogłoby go podrobić (tryb serwera słucha na 0.0.0.0). Do tego
    nasz Host (DNS rebinding); nie port publiczny i nie tunel (cloudflared łączy się lokalnie,
    ale dokłada CF-Connecting-IP)."""
    if getattr(handler.server, "public", False) or handler.headers.get("CF-Connecting-IP"):
        return False
    return _loopback(handler) and _host_ok(handler)


def _v1_ok(handler) -> bool:
    """API urządzeń (/v1) tylko połączeniem szyfrowanym (port HTTPS z certyfikatem serwera,
    port publiczny, tunel) — zwykły port tylko z tego komputera (panel, testy). 0.43: wcześniej
    sieć lokalna i Tailscale łączyły się zwykłym HTTP."""
    if isinstance(handler.server, _TLSServer):
        return True
    if _loopback(handler) and not handler.headers.get("CF-Connecting-IP"):
        return True
    refuse(handler, 403)
    return False


def _loopback(handler) -> bool:
    import ipaddress
    try:
        a = ipaddress.ip_address(handler.client_address[0])
        if a.version == 6 and a.ipv4_mapped:
            a = a.ipv4_mapped
        return a.is_loopback
    except ValueError:
        return False


class _Handler(BaseHTTPRequestHandler):
    # HTTP/1.1: połączenie zostaje otwarte między zapytaniami. Przy HTTP/1.0 każdy blok
    # pobieranej gry zaczynał TCP od nowa (uzgodnienie + wolny start) — przy dalekim
    # serwerze kilkukrotnie wolniej. Każda odpowiedź musi mieć Content-Length.
    protocol_version = "HTTP/1.1"
    timeout = 120                     # bezczynne połączenie zamykamy (wątek nie wisi)

    def log_message(self, *_a):
        pass

    def do_POST(self):
        if getattr(self.server, "public", False):
            from emustart import public      # port z internetu: tylko /v1 i grafiki, z kluczem
            if not public.gate(self):
                return
        elif not _near(self):
            return
        p = urllib.parse.urlsplit(self.path).path
        if getattr(self.server, "public", False):
            from emustart import webportal   # strona serwera (logowanie Google, kod logowania)
            if webportal.handles(p):
                return webportal.post(self)
        if p.startswith("/v1/") and not _v1_ok(self):
            return
        if p in ("/v1/enroll/request", "/v1/enroll/poll", "/v1/enroll/pending", "/v1/enroll/decide",
                 "/v1/auth/challenge", "/v1/auth/token", "/v1/auth/logout"):
            from emustart import remote      # zalogowanie urządzenia, logowanie podpisem
            return remote.enroll_flow(self, p.rsplit("/", 1)[1])
        if p.startswith("/v1/savesync/"):
            from emustart import remote      # synchronizacja zapisów gier (emustart/savesync.py)
            return remote.savesync_post(self)
        if p.startswith("/v1/lock/"):
            from emustart import remote      # jedna gra naraz na profil (emustart/plock.py)
            return remote.lock_post(self)
        if p.startswith("/v1/"):
            return refuse(self, 404)
        # API tylko dla naszego UI: właściwy Host i własny nagłówek (zwykła strona
        # z internetu nie wyśle go do 127.0.0.1 bez zgody CORS, której nie dajemy)
        if not _local_ui(self) or self.headers.get("X-EmuStart") != "1":
            return refuse(self, 403)
        if self.path.startswith("/admin/"):
            from emustart import srvadmin     # panel serwera (tylko z tego komputera)
            return srvadmin.handle(self, DEV_API)
        name = urllib.parse.urlsplit(self.path).path.removeprefix("/api/")
        fn = getattr(DEV_API, name, None) if DEV_API and not name.startswith("_") else None
        if not callable(fn):
            return refuse(self, 404)
        n = int(self.headers.get("Content-Length") or 0)
        args = json.loads(self.rfile.read(n) or b"[]")
        body = json.dumps(fn(*args), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self):
        if getattr(self.server, "public", False):
            from emustart import public      # port z internetu: tylko /v1 i grafiki, z kluczem
            if not public.gate(self):
                return
        elif not _near(self):
            return
        if urllib.parse.urlsplit(self.path).path.startswith("/v1/"):
            if not _v1_ok(self):
                return
            from emustart import remote
            if urllib.parse.urlsplit(self.path).path.startswith("/v1/files/u/"):
                return remote.files_put(self)     # paczka zapisów — uprawnienie w podpisanym adresie
            return remote.handle_write(self, "PUT")   # zapis w folderze profili (z kluczem)
        return refuse(self, 405)

    def do_DELETE(self):
        if getattr(self.server, "public", False):
            from emustart import public      # port z internetu: tylko /v1 i grafiki, z kluczem
            if not public.gate(self):
                return
        elif not _near(self):
            return
        if urllib.parse.urlsplit(self.path).path.startswith("/v1/"):
            if not _v1_ok(self):
                return
            from emustart import remote
            return remote.handle_write(self, "DELETE")
        return refuse(self, 405)

    def do_GET(self):
        if getattr(self.server, "public", False):
            from emustart import public      # port z internetu: tylko /v1 i grafiki, z kluczem
            if not public.gate(self):
                return
        elif not _near(self):
            return
        raw = urllib.parse.urlsplit(self.path).path
        if getattr(self.server, "public", False):
            from emustart import webportal   # strona serwera: start, logowanie Google, programy
            if webportal.handles(raw):
                return webportal.get(self)
        if raw.startswith("/v1/"):
            if not _v1_ok(self):
                return
            from emustart import remote      # API urządzeń — token zamiast sprawdzania Host
            if raw.startswith("/v1/files/d/"):
                return remote.files_get(self)     # paczka zapisów — uprawnienie w podpisanym adresie
            return remote.handle(self)
        if not _local_ui(self):
            from emustart import remote      # grafiki dla urządzeń (z tokenem, szyfrowanym połączeniem)
            if not raw.startswith(("/media/", "/assets/")):
                return self.send_error(403)
            if not _v1_ok(self):
                return
            if not remote.authorized(self):
                return self.send_error(403)
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        base, rel = paths.WEB, path.lstrip("/") or "index.html"
        for prefix, root in ROUTES.items():
            if path.startswith(prefix):
                base, rel = root, path[len(prefix):]
                break
        target = (base / rel).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError:
            return self.send_error(403)
        if path.startswith("/serwer/") and MEDIA_CFG:
            from emustart import libsync           # podgląd z serwera (logo z paczki) — bez zapisu
            got = libsync.fetch_preview(MEDIA_CFG(), path[len("/serwer"):])
            if not got:
                return self.send_error(404)
            data, ctype = got
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "private, max-age=3600")   # ponowne otwarcie okna — z pamięci
            self.end_headers()
            self.wfile.write(data)
            return
        if base == ROUTES.get("/assets/") and rel.startswith("systems/") and rel.count("/") == 1:
            from emustart import mediastore            # wbudowane logo platform (assets/systems.media)
            data = mediastore.bundled_get(rel[len("systems/"):])
            if data is None:
                return self.send_error(404)
            return self._send_bytes(data, "image/png")
        if base == ROUTES.get("/media/"):
            from emustart import mediastore
            where = mediastore.file_name(rel)
            if where:                                  # grafika gry — z pliku systemu (.media)
                got = mediastore.get(*where)
                if got is None and not target.is_file():
                    cfg = MEDIA_CFG() if MEDIA_CFG else getattr(DEV_API, "_cfg", None)
                    if cfg:
                        from emustart import libsync   # PC: lokalna kopia z serwera
                        libsync.fetch_media(cfg, rel, target)
                        got = mediastore.get(*where)
                if got is not None:
                    return self._send_bytes(got[0], mimetypes.guess_type(where[1])[0] or "image/png")
                if not target.is_file():
                    return self.send_error(404)
                # stary plik grafiki, jeszcze nieprzeniesiony do pliku systemu — jak dotąd
        if not target.is_file() and base == ROUTES.get("/media/"):
            cfg = MEDIA_CFG() if MEDIA_CFG else getattr(DEV_API, "_cfg", None)
            if cfg:
                from emustart import libsync       # komputer z biblioteką z serwera — grafika z serwera
                libsync.fetch_media(cfg, rel, target)
        if not target.is_file():
            return self.send_error(404)
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0]
                         or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if base == paths.WEB else "max-age=86400")
        if base == paths.WEB:
            self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)


def _send_bytes(self, data: bytes, ctype: str) -> None:
    self.send_response(200)
    self.send_header("Content-Type", ctype)
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Cache-Control", "max-age=86400")
    self.send_header("Referrer-Policy", "no-referrer")
    self.end_headers()
    self.wfile.write(data)


_Handler._send_bytes = _send_bytes


class _TLSServer(ThreadingHTTPServer):
    """Port szyfrowany (emustart/tls.py). Uzgadnianie TLS w wątku połączenia — wolny albo
    obcy klient (skaner portów) nie blokuje przyjmowania kolejnych."""
    daemon_threads = True

    def __init__(self, addr, handler, ctx: ssl.SSLContext):
        super().__init__(addr, handler)
        self.ctx = ctx

    def get_request(self):
        sock, addr = self.socket.accept()
        return self.ctx.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def finish_request(self, request, client_address):
        try:
            request.settimeout(15)
            request.do_handshake()
        except (OSError, ssl.SSLError):
            return                                   # nie TLS, zły klient, przerwane
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        pass                                         # zerwane połączenia z internetu — bez śladu w konsoli


def start_tls(port: int, ctx: ssl.SSLContext, dev_api=None, bind: str = "0.0.0.0") -> int:
    """HTTPS obok zwykłego portu (tryb serwera). Zwraca numer portu."""
    global DEV_API
    if dev_api is not None:
        DEV_API = dev_api
    srv = _TLSServer((bind, port), _Handler, ctx)
    threading.Thread(target=srv.serve_forever, daemon=True, name="https").start()
    return srv.server_address[1]


class _PublicServer(_TLSServer):
    """Port z internetu (emustart/public.py): jak szyfrowany, plus limit połączeń naraz
    z jednego adresu i odrzucanie zablokowanych adresów jeszcze przed uzgodnieniem TLS."""
    public = True

    def process_request(self, request, client_address):
        from emustart import public
        if not public.conn_open(client_address[0]):
            self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        from emustart import public
        try:
            super().process_request_thread(request, client_address)
        finally:
            public.conn_close(client_address[0])


_PUBLIC = None


def start_public(port: int, ctx: ssl.SSLContext, bind: str = "0.0.0.0") -> int:
    global _PUBLIC
    srv = _PublicServer((bind, port), _Handler, ctx)
    threading.Thread(target=srv.serve_forever, daemon=True, name="https-public").start()
    _PUBLIC = srv
    return srv.server_address[1]


def stop_public() -> None:
    global _PUBLIC
    if _PUBLIC is not None:
        _PUBLIC.shutdown()
        _PUBLIC.server_close()
        _PUBLIC = None


def start(port: int = 0, dev_api=None, bind: str = "127.0.0.1") -> str:
    """bind 0.0.0.0 tylko w trybie serwera (--server): z zewnątrz dostępne wyłącznie
    /v1/ i grafiki z kluczem; interfejs i API administracyjne — tylko lokalnie."""
    global DEV_API
    DEV_API = dev_api
    srv = ThreadingHTTPServer((bind, port), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="http").start()
    return f"http://127.0.0.1:{srv.server_address[1]}"
