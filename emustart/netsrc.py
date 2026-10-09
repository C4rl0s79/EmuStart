"""netsrc — pliki gier z serwera EmuStart (HTTP z kluczem) zamiast z udziału SMB.

Przez Tailscale na dużą odległość SMB idzie jednym połączeniem TCP (pomiar:
ok. 25 MB/s niezależnie od liczby strumieni), a serwer daje osobne połączenie na
strumień (8 strumieni: ok. 78 MB/s). Gra jest szukana na serwerze po (system,
ścieżka względna); plik bierzemy z serwera tylko przy zgodnym rozmiarze —
w każdym innym przypadku (brak serwera, brak gry, inny rozmiar, błąd) jak dotąd SMB.

`RemoteFile` udaje ścieżkę źródła w partial/cache; `open_src()` otwiera ją
jako plik do czytania (seek/read), każdy uchwyt = własne połączenie HTTP/1.1.
"""

from __future__ import annotations

import http.client
import json
import logging
import threading
import time
import urllib.parse
from pathlib import Path

log = logging.getLogger("emustart.netsrc")

STREAMS = 8                 # strumieni pobierania z serwera (SMB: partial.STREAMS)
COALESCE = 4                # kolejne bloki 1 MB w jednym zapytaniu (pomiar: 4 MB ≈ 1,5× szybciej)
TIMEOUT = 30
INFO_TTL = 60               # wynik sprawdzenia serwera ważny tyle s
LAN_RTT = 0.004             # odpowiedź serwera szybsza = ta sama sieć lokalna

_info: dict = {}
_info_lock = threading.Lock()


class RemoteFile:
    """Plik gry na serwerze: /v1/games/<id>/file/<rel>."""

    def __init__(self, host: str, port: int, key: str, gid: int, rel: str, size: int):
        self.host, self.port, self.key = host, port, key
        self.gid, self.rel, self.size = gid, rel, size
        self.name = Path(rel.replace("\\", "/")).name
        self.url = f"/v1/games/{gid}/file/" + "/".join(
            urllib.parse.quote(p) for p in rel.replace("\\", "/").split("/"))

    def __str__(self) -> str:
        return f"serwer {self.host}: {self.rel}"



class RemoteReader:
    """Plik do czytania z serwera: seek/read zakresami (Range) po jednym połączeniu."""

    def __init__(self, f: RemoteFile):
        self.f = f
        self.pos = 0
        self.conn: http.client.HTTPConnection | None = None

    def _connect(self) -> http.client.HTTPConnection:
        if self.conn is None:
            self.conn = http.client.HTTPConnection(self.f.host, self.f.port, timeout=TIMEOUT)
        return self.conn

    def seek(self, off: int, whence: int = 0) -> int:
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.f.size + off)
        return self.pos

    def tell(self) -> int:
        return self.pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.f.size - self.pos
        n = min(n, self.f.size - self.pos)
        if n <= 0:
            self._connect()      # read(0): tylko otwarcie połączenia (rozgrzewanie)
            return b""
        last: Exception | None = None
        for attempt in range(3):
            try:
                data = self._get(self.pos, n)
                self.pos += len(data)
                return data
            except (OSError, http.client.HTTPException) as ex:
                last = ex
                self.close()                         # zerwane połączenie — nowe
                time.sleep(0.2 * (attempt + 1))
        raise OSError(f"serwer: {self.f.name} @ {self.pos}: {last}")

    def _get(self, off: int, n: int) -> bytes:
        c = self._connect()
        c.request("GET", self.f.url, headers={"Authorization": f"Bearer {self.f.key}",
                                              "Range": f"bytes={off}-{off + n - 1}"})
        r = c.getresponse()
        data = r.read()
        if r.status != 206 or len(data) != n:
            if r.will_close or r.status != 206:
                self.close()
            raise http.client.HTTPException(f"HTTP {r.status}, {len(data)}/{n} B")
        if r.will_close:
            self.close()
        return data

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except OSError:
                pass
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        self.close()


def open_src(src):
    """Plik źródłowy do czytania: z serwera (RemoteFile) albo z dysku/NAS-a."""
    if isinstance(src, RemoteFile):
        return RemoteReader(src)
    return open(src, "rb", buffering=0)


def is_remote(src) -> bool:
    return isinstance(src, RemoteFile)


# ── serwer ──
def endpoint(cfg: dict) -> tuple[str, int, str] | None:
    """(host, port, klucz) z ustawień albo None, gdy serwer nie jest skonfigurowany."""
    url = (cfg.get("server_url") or "").strip()
    key = (cfg.get("server_key") or "").strip()
    if not url or not key:
        return None
    if "://" not in url:
        url = "http://" + url
    u = urllib.parse.urlsplit(url)
    if not u.hostname:
        return None
    return u.hostname, u.port or 8740, key


def _request(ep, path: str, timeout: float = 5) -> tuple[int, bytes, float]:
    host, port, key = ep
    t = time.monotonic()
    c = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        c.request("GET", path, headers={"Authorization": f"Bearer {key}"})
        r = c.getresponse()
        return r.status, r.read(), time.monotonic() - t
    finally:
        c.close()


def info(cfg: dict, fresh: bool = False) -> dict:
    """{ok, name, version, features, rtt, reason} — sprawdzenie serwera, z pamięcią INFO_TTL s."""
    ep = endpoint(cfg)
    if not ep:
        return {"ok": False, "reason": "serwer nie jest ustawiony"}
    with _info_lock:
        if not fresh and _info.get("ep") == ep and time.monotonic() - _info.get("t", 0) < INFO_TTL:
            return _info["res"]
    try:
        st, body, rtt = _request(ep, "/v1/info")
        if st == 401:
            res = {"ok": False, "reason": "zły klucz serwera"}
        elif st != 200:
            res = {"ok": False, "reason": f"serwer odpowiedział {st}"}
        else:
            d = json.loads(body)
            res = {"ok": True, "name": d.get("name", ""), "version": d.get("version", ""),
                   "features": d.get("features") or [], "nas_id": d.get("nas_id", ""), "rtt": rtt}
    except (OSError, http.client.HTTPException, ValueError) as ex:
        res = {"ok": False, "reason": f"brak połączenia z {ep[0]}:{ep[1]} ({ex})"}
    with _info_lock:
        _info.update(ep=ep, t=time.monotonic(), res=res)
    return res


def sources(cfg: dict, g: dict) -> dict:
    """{rel: RemoteFile} dla plików gry dostępnych na serwerze w tym samym rozmiarze.
    Pusty słownik = wszystko jak dotąd przez SMB."""
    if cfg.get("server_games", True) is False:
        return {}
    ep = endpoint(cfg)
    if not ep:
        return {}
    i = info(cfg)
    if not i.get("ok") or "find" not in i.get("features", []):
        if i.get("ok"):
            log.info("serwer %s (EmuStart %s) nie umie szukać gier — zaktualizuj go; pobieranie przez SMB",
                     i.get("name"), i.get("version"))
        return {}
    q = urllib.parse.urlencode({"es": g["es"], "rel": g["rel"].replace("\\", "/")})
    try:
        st, body, _rtt = _request(ep, f"/v1/find?{q}", timeout=10)
    except (OSError, http.client.HTTPException) as ex:
        log.info("serwer: szukanie %s: %s — SMB", g["title"], ex)
        return {}
    if st != 200:
        log.info("serwer nie ma gry %s (%s) — SMB", g["title"], st)
        return {}
    d = json.loads(body)
    remote = {f["path"].replace("\\", "/"): f["size"] for f in d.get("files", [])}
    out = {}
    for rel, size, _mt in g["files"]:
        if remote.get(rel.replace("\\", "/")) == size:
            out[rel] = RemoteFile(ep[0], ep[1], ep[2], int(d["id"]), rel, size)
    log.info("serwer %s: %d/%d plików gry %s", ep[0], len(out), len(g["files"]), g["title"])
    return out


def share_rel(path: str) -> str | None:
    """Ścieżka względem udziału sieciowego: Z:\\WHDLoad (Z: = \\\\nas\\EMU_ROMS) → „WHDLoad”."""
    p = str(path)
    if len(p) >= 2 and p[1] == ":":
        if not _unc_of_drive(p[:2]):
            return None                      # dysk lokalny — serwer go nie zobaczy
        rest = p[2:]
    elif p.startswith("\\\\"):
        parts = p[2:].split("\\", 2)
        if len(parts) < 2:
            return None
        rest = parts[2] if len(parts) > 2 else ""
    else:
        return None
    return rest.replace("\\", "/").strip("/")


def push_roots(cfg: dict) -> list:
    """Foldery z grami z tego komputera → serwer (dopisuje brakujące i skanuje). Zwraca dodane."""
    from emustart import config
    ep = endpoint(cfg)
    if not ep:
        return []
    i = info(cfg)
    if not i.get("ok") or "roots" not in i.get("features", []):
        return []
    rels = [r for r in (share_rel(x) for x in config.rom_roots(cfg)) if r is not None]
    if not rels:
        return []
    body = json.dumps({"roots": rels}).encode("utf-8")
    c = http.client.HTTPConnection(ep[0], ep[1], timeout=20)
    try:
        c.request("PUT", "/v1/roots", body=body, headers={"Authorization": f"Bearer {ep[2]}",
                                                          "Content-Type": "application/json"})
        r = c.getresponse()
        d = json.loads(r.read() or b"{}")
    except (OSError, http.client.HTTPException, ValueError) as ex:
        log.info("foldery z grami na serwer: %s", ex)
        return []
    finally:
        c.close()
    if d.get("added"):
        log.info("serwer dodał foldery z grami: %s", ", ".join(d["added"]))
    if d.get("missing"):
        log.info("serwer nie widzi folderów: %s", ", ".join(d["missing"]))
    return d.get("added") or []


def on_lan(cfg: dict) -> bool:
    i = info(cfg)
    return bool(i.get("ok")) and i.get("rtt", 1) < LAN_RTT


def guess_url(rom_roots: list) -> str:
    """Adres serwera z udziału sieciowego z grami (\\\\host\\udział albo dysk sieciowy)."""
    for r in rom_roots:
        p = str(r)
        if len(p) >= 2 and p[1] == ":":
            p = _unc_of_drive(p[:2]) or p
        if p.startswith("\\\\"):
            host = p[2:].split("\\", 1)[0]
            if host:
                return f"http://{host}:8740"
    return ""


def _unc_of_drive(drive: str) -> str:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(1024)
        n = ctypes.c_ulong(len(buf))
        if ctypes.windll.mpr.WNetGetConnectionW(drive, buf, ctypes.byref(n)) == 0:
            return buf.value
    except (AttributeError, OSError):
        pass
    return ""
