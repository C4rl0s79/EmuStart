"""netsrc — pliki gier z serwera EmuStart (HTTP z kluczem) zamiast z udziału SMB.

Przez Tailscale na dużą odległość SMB idzie jednym połączeniem TCP (pomiar:
ok. 25 MB/s niezależnie od liczby strumieni), a serwer daje osobne połączenie na
strumień (8 strumieni: ok. 78 MB/s). Gra jest szukana na serwerze po (system,
ścieżka względna); plik bierzemy z serwera tylko przy zgodnym rozmiarze —
w każdym innym przypadku (brak serwera, brak gry, inny rozmiar, błąd) jak dotąd SMB.

`RemoteFile` udaje ścieżkę źródła w partial/cache; `open_src()` otwiera ją
jako plik do czytania (seek/read), każdy uchwyt = własne połączenie HTTP/1.1.

Adresy serwera (0.34): serwer podaje w /v1/info listę adresów (sieć lokalna, Tailscale,
zwykły port i szyfrowany) i odcisk swojego certyfikatu. Klient sprawdza je równolegle
i bierze najlepszy działający (sieć lokalna → Tailscale → publiczny; w obrębie rodzaju
zwykły port przed szyfrowanym). HTTPS — z przypiętym odciskiem (`conn`).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import re
import ssl
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

BEST_TTL = 300              # wybrany adres serwera ważny tyle s (potem ponowne sprawdzenie)
PROBE_TIMEOUT = 2.5
GRACE = 0.4                      # po pierwszym działającym adresie: czekanie na lepszy
KIND_ORDER = {"lan": 0, "tailscale": 1, "public": 2, "tunnel": 3}

_info: dict = {}
_info_lock = threading.Lock()
_PINS: dict = {}            # (host, port) → odcisk certyfikatu serwera (HTTPS)
_CA: set = set()            # (host, port) — HTTPS ze zwykłym certyfikatem (tunel Cloudflare)
_EPINFO: dict = {}          # (schemat, host, port) → {"mode": plain|pin|ca, "kind": …}
_best: dict = {}
_best_lock = threading.Lock()
_learned: dict = {}         # pamięć serwer-adresy.json


class PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS do serwera EmuStart: zaufanie = zgodny odcisk certyfikatu (emustart/tls.py)."""

    def __init__(self, host: str, port: int, fp: str, timeout: float = TIMEOUT):
        from emustart import tls
        super().__init__(host, port, timeout=timeout, context=tls.client_context())
        self.fp = fp

    def connect(self):
        super().connect()
        der = self.sock.getpeercert(binary_form=True) or b""
        if not self.fp or hashlib.sha256(der).hexdigest() != self.fp:
            self.close()
            raise CertMismatch(f"certyfikat serwera {self.host} nie zgadza się z zapamiętanym — "
                               "połącz urządzenie z serwerem ponownie")


def conn(host: str, port: int, timeout: float = TIMEOUT) -> http.client.HTTPConnection:
    """Połączenie z serwerem: HTTPS z przypiętym certyfikatem, gdy ten adres jest szyfrowany."""
    if (host, port) in _CA:
        import ssl as _ssl
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=_ssl.create_default_context())
    fp = _PINS.get((host, port))
    if fp is not None:
        return PinnedHTTPS(host, port, fp, timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


def secure_conn(host: str, port: int, timeout: float = TIMEOUT) -> http.client.HTTPConnection:
    """Połączenie do operacji z hasłem konta: szyfrowane, gdy się da — ten sam komputer na
    porcie HTTPS z przypiętym certyfikatem (zwykły port przyjmuje tylko sieć lokalną i
    Tailscale; z internetu i tak tylko HTTPS)."""
    if (host, port) in _CA or (host, port) in _PINS or not _cfg_ref:
        return conn(host, port, timeout)
    fp = server_fp(_cfg_ref[0])
    for sp in candidates(_cfg_ref[0]):
        if sp[0] == "https" and sp[1] == host and fp and _mode(sp) == "pin":
            return PinnedHTTPS(sp[1], sp[2], fp, timeout)
    return conn(host, port, timeout)


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
            self.conn = conn(self.f.host, self.f.port, TIMEOUT)
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
        c.request("GET", self.f.url, headers={**auth_headers(self.f.key),
                                              "Range": f"bytes={off}-{off + n - 1}"})
        r = c.getresponse()
        data = r.read()
        if r.status == 401 and self.f.key.startswith(DEV):
            drop_token()                         # wygasły token — ponowienie zaloguje od nowa
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
def _split(url: str) -> tuple[str, str, int] | None:
    """(schemat, host, port) adresu serwera."""
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        # nazwa domeny (np. emustart.emucloud.org — tunel) → https; adres IP / nazwa komputera → http
        host = url.split("/")[0].split(":")[0]
        domain = "." in host and not re.fullmatch(r"\d+(\.\d+){3}", host)
        url = ("https://" if domain else "http://") + url
    u = urllib.parse.urlsplit(url)
    if not u.hostname:
        return None
    return u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 8740)


def _kind(sp) -> int:
    from emustart.remote import kind_of
    k = (_EPINFO.get(sp) or {}).get("kind") or kind_of(sp[1])
    return KIND_ORDER.get(k, 2)


def candidates(cfg: dict) -> list:
    """Adresy serwera do sprawdzenia: wpisany + podane przez serwer.
    HTTPS z przypiętym certyfikatem tylko z odciskiem; tunel (i adres https wpisany ręcznie,
    którego serwer nie podał jako własny) — zwykłe sprawdzanie certyfikatu."""
    fp = server_fp(cfg)
    eps = {}
    for e in _store().get("endpoints") or []:
        sp = _split(e.get("url", ""))
        if sp:
            mode = "plain" if sp[0] == "http" else ("ca" if e.get("ca") else "pin")
            eps[sp] = {"mode": mode, "kind": e.get("kind") or ""}
    out, seen = [], set()
    for sp in [_split(cfg.get("server_url") or "")] + list(eps):
        if not sp or sp in seen or sp[0] != "https":   # tylko szyfrowane (0.43)
            continue
        # adres spoza listy serwera: IP / nazwa komputera — certyfikat serwera (przypięty),
        # domena — tunel ze zwykłym certyfikatem
        ip_like = re.fullmatch(r"[\d.]+|[0-9a-fA-F:]+", sp[1]) or "." not in sp[1]
        info = eps.get(sp) or ({"mode": "pin", "kind": ""} if ip_like else {"mode": "ca", "kind": "tunnel"})
        if info["mode"] == "pin" and not fp:
            continue
        seen.add(sp)
        _EPINFO[sp] = info
        out.append(sp)
    return out


def _mode(sp) -> str:
    return (_EPINFO.get(sp) or {}).get("mode") or ("plain" if sp[0] == "http" else "pin")


def _probe(sp, key: str, fp: str) -> float | None:
    """Czy pod adresem odpowiada serwer EmuStart — czas albo None (`key` — dla zgodności wywołań)."""
    scheme, host, port = sp
    t = time.monotonic()
    mode = _mode(sp)
    if mode == "pin":
        c = PinnedHTTPS(host, port, fp, PROBE_TIMEOUT)
    elif mode == "ca":
        c = http.client.HTTPSConnection(host, port, timeout=PROBE_TIMEOUT, context=ssl.create_default_context())
    else:
        c = http.client.HTTPConnection(host, port, timeout=PROBE_TIMEOUT)
    try:
        # bez logowania (wyzwanie dla pustego id): logowanie przez badany adres szło pod wspólną
        # blokadą — martwy adres trzymał ją do końca czasu połączenia i testy innych adresów
        # nie mieściły się w czasie (wybierany był martwy adres)
        from emustart import public
        c.request("POST", public.PROBE_PATH, body=public.PROBE_BODY, headers={"Content-Type": "application/json"})
        r = c.getresponse()
        return time.monotonic() - t if public.ours(r.status, r.read()) else None
    except (OSError, http.client.HTTPException):
        return None
    finally:
        c.close()


def best(cfg: dict, fresh: bool = False):
    """Najlepszy działający adres serwera (schemat, host, port); bez sprawdzania, gdy jest jeden."""
    cands = candidates(cfg)
    if len(cands) <= 1:
        return cands[0] if cands else None
    key, fp = cfg.get("server_key") or "", server_fp(cfg)
    sig = (tuple(cands), key, fp)
    with _best_lock:
        if not fresh and _best.get("sig") == sig and time.monotonic() - _best.get("t", 0) < BEST_TTL:
            return _best["sp"]
    res: dict = {}
    first_ok: dict = {}
    done = threading.Condition()

    def rank(sp):
        return _kind(sp), sp[0] == "https"

    def one(sp):
        r = _probe(sp, key, fp)
        with done:
            res[sp] = r
            done.notify_all()

    for sp in cands:
        threading.Thread(target=one, args=(sp,), daemon=True).start()
    # koniec, gdy odpowiedział adres, od którego żaden niesprawdzony nie byłby lepszy
    # (sieć lokalna odpowiada w milisekundach — nie czekamy na nieosiągalne)
    end = time.monotonic() + PROBE_TIMEOUT + 1
    with done:
        while True:
            ok = [sp for sp in cands if res.get(sp) is not None]
            waiting = [sp for sp in cands if sp not in res]
            if not waiting or (ok and min(map(rank, ok)) <= min(map(rank, waiting))):
                break
            if ok:
                # jest działający adres — na lepszy (sieć lokalna) czekamy chwilę, nie do końca
                # limitu: adresy sieci lokalnej serwera z innej sieci nigdy nie odpowiadają
                end = min(end, first_ok.setdefault("t", time.monotonic()) + GRACE)
            left = end - time.monotonic()
            if left <= 0:
                break
            done.wait(left)
        ok = [sp for sp in cands if res.get(sp) is not None]
        _alive[:] = ok                      # logowanie tylko przez adresy, które odpowiedziały
        pick = min(ok, key=lambda sp: (rank(sp), res[sp])) if ok else cands[0]
        res = dict(res)
    with _best_lock:
        if _best.get("sp") != pick:
            seen = ", ".join(f"{s[0]}://{s[1]}:{s[2]} " + (f"{1000 * res[s]:.0f} ms" if res.get(s) is not None else "brak")
                             for s in cands)
            log.info("serwer: wybrany adres %s://%s:%d (%s)", pick[0], pick[1], pick[2], seen)
        _best.update(sig=sig, t=time.monotonic(), sp=pick)
    return pick


def current_kind(cfg: dict) -> str:
    """Którędy idzie połączenie z serwerem (do ustawień): sieć lokalna / Tailscale / … ."""
    with _best_lock:
        sp = _best.get("sp")
    if not sp:
        return ""
    k = (_EPINFO.get(sp) or {}).get("kind") or ""
    return {"lan": "sieć lokalna", "tailscale": "Tailscale", "public": "port publiczny", "tunnel": "tunel"}.get(k, "")


def forget_best() -> None:
    """Adres przestał odpowiadać — następne zapytanie sprawdzi wszystkie od nowa."""
    with _best_lock:
        _best.clear()


def endpoint(cfg: dict) -> tuple[str, int, str] | None:
    """(host, port, klucz) najlepszego adresu serwera albo None, gdy serwer nie jest ustawiony."""
    key = (cfg.get("server_key") or "").strip()
    if not key:
        return None
    if key.startswith(DEV):
        use_device(cfg)
    sp = best(cfg)
    if not sp:
        return None
    scheme, host, port = sp
    if _mode(sp) == "ca":
        _CA.add((host, port))
    elif scheme == "https":
        _PINS[(host, port)] = server_fp(cfg)
    return host, port, key


def _store_path():
    from emustart import paths
    return paths.DATA / "serwer-adresy.json"


def _store() -> dict:
    """Adresy i odcisk certyfikatu nauczone od serwera (osobny plik — nie config.json)."""
    with _best_lock:
        if "store" not in _learned:
            try:
                _learned["store"] = json.loads(_store_path().read_text(encoding="utf-8"))
            except (OSError, ValueError):
                _learned["store"] = {}
        return _learned["store"]


def server_fp(cfg: dict) -> str:
    return cfg.get("server_fp") or _store().get("fp") or ""


def learn(cfg: dict, d: dict, trusted: bool = True) -> bool:
    """Adresy i odcisk certyfikatu z odpowiedzi serwera → serwer-adresy.json. True = zmienione.
    `trusted` — odpowiedź z połączenia szyfrowanego (przypięty certyfikat albo tunel) albo
    z zalogowania. Inna nie zmienia już zapamiętanych: podszywający się w sieci podałby
    własny certyfikat i adresy, a urządzenie wysyłałoby tam token i hasła (certyfikat serwera
    jest ważny 20 lat — zmienia się tylko z nową instalacją, wtedy logowanie od nowa)."""
    eps = [e for e in (d.get("endpoints") or []) if isinstance(e, dict) and e.get("url")]
    tls = d.get("tls") if isinstance(d.get("tls"), dict) else {}
    fp = tls.get("fp") or ""
    st = dict(_store())
    changed = False
    if eps and eps != st.get("endpoints") and (trusted or not st.get("endpoints")):
        st["endpoints"] = eps
        changed = True
    if fp and fp != st.get("fp") and (trusted or not st.get("fp")):
        if st.get("fp"):
            log.warning("serwer ma nowy certyfikat (%s… → %s…)", st["fp"][:12], fp[:12])
        st["fp"] = fp
        changed = True
    if changed:
        with _best_lock:
            _learned["store"] = st
        try:
            path = _store_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as ex:
            log.info("adresy serwera: %s", ex)
        forget_best()
    return changed


def auth_headers(key: str) -> dict:
    """Token i wersja EmuStart (panel serwera pokazuje wersje urządzeń). Urządzenie z kluczem
    urządzenia („dev:<id>”) — token sesji z logowania podpisem; inny klucz — tak jak podany
    (wspólny klucz serwera działa tylko na komputerze serwera)."""
    from emustart import __version__
    if key.startswith(DEV):
        key = session_token(key[len(DEV):]) or "brak-tokenu"
    return {"Authorization": f"Bearer {key}", "X-EmuStart-Version": __version__, "X-EmuStart-Hw": hw_id()}


_hw: list = []


def hw_id() -> str:
    """Skrót identyfikatora sprzętu (serwer: jedna rejestracja na sprzęt). Windows: MachineGuid
    + konto Windows (dwa konta na jednym komputerze to dwa urządzenia); inne systemy:
    /etc/machine-id. Wysyłany jest tylko skrót SHA-256, nie sam identyfikator."""
    if _hw:
        return _hw[0]
    import os
    raw = ""
    try:
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                raw = str(winreg.QueryValueEx(k, "MachineGuid")[0]) + "|" + os.environ.get("USERNAME", "")
        else:
            raw = Path("/etc/machine-id").read_text().strip() + "|" + os.environ.get("USER", "")
    except OSError:
        raw = ""
    _hw.append(hashlib.sha256(("emustart-hw|" + raw).encode()).hexdigest()[:32] if raw else "")
    return _hw[0]


# ── logowanie podpisem (emustart/enroll.py, emustart/keystore.py) ──
DEV = "dev:"
TOKEN_MARGIN = 300               # odnowienie tokenu tyle s przed wygaśnięciem
_session: dict = {}              # {"did", "tok", "exp"}
# serwer odpowiedział jednoznacznie „nie” (nie zna urządzenia, inny certyfikat) — {"reason", "at"};
# przez REJECT_TTL bez ponownych prób: wcześniej klient w kółko pytał wszystkie adresy (po 10 s
# na każdy martwy), a pasek długo pokazywał „łączę”
_rejected: dict = {}
REJECT_TTL = 120
_signer: dict = {}               # id urządzenia → (nazwa klucza, rodzaj)
_session_lock = threading.Lock()
_login_lock = threading.Lock()   # jedno logowanie naraz — reszta wątków czeka i bierze ten sam token
_hint: list = []                 # ostatni działający adres (schemat, host, port)
_alive: list = []                # adresy, które odpowiedziały przy ostatnim wyborze (best)
_cfg_ref: list = []


def use_device(cfg: dict) -> None:
    """Klucz urządzenia z ustawień (server_key = „dev:<id>”)."""
    key = cfg.get("server_key") or ""
    if key.startswith(DEV) and cfg.get("server_keyname"):
        _signer[key[len(DEV):]] = (cfg["server_keyname"], cfg.get("server_keykind") or "dpapi")
        _cfg_ref[:] = [cfg]


def drop_token() -> None:
    with _session_lock:
        _session.clear()
        _rejected.clear()


def rejected() -> str:
    """Powód ostatniej jednoznacznej odmowy serwera ('' — brak, albo minęło REJECT_TTL)."""
    if _rejected and time.time() - _rejected.get("at", 0) < REJECT_TTL:
        return _rejected.get("reason", "")
    return ""


def forget_server() -> None:
    """Wylogowanie: zapomniane adresy, odcisk certyfikatu, tokeny i klucz podpisu."""
    drop_token()
    forget_best()
    _info.clear()
    _signer.clear()
    _cfg_ref.clear()
    _PINS.clear()
    _CA.clear()
    _EPINFO.clear()
    with _best_lock:
        _learned["store"] = {}
    try:
        _store_path().unlink(missing_ok=True)
    except OSError as ex:
        log.info("adresy serwera: %s", ex)


def _conn_sp(sp, timeout: float):
    scheme, host, port = sp
    mode = _mode(sp)
    if mode == "ca" or (scheme == "https" and mode != "pin"):
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context())
    if mode == "pin":
        return PinnedHTTPS(host, port, (_cfg_ref and server_fp(_cfg_ref[0])) or "", timeout)
    return http.client.HTTPConnection(host, port, timeout=timeout)


def _post_sp(sp, path: str, data: dict, timeout: float = 10) -> dict:
    c = _conn_sp(sp, timeout)
    try:
        c.request("POST", path, body=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"})
        r = c.getresponse()
        d = json.loads(r.read() or b"{}")
        return d if isinstance(d, dict) else {}
    finally:
        c.close()


def _login(did: str, sp) -> str:
    """Wyzwanie → podpis kluczem urządzenia → token sesji (przez adres `sp`)."""
    import base64
    from emustart import keystore
    name, kind = _signer[did]
    ch = _post_sp(sp, "/v1/auth/challenge", {"device": did})
    if not ch.get("ok"):
        raise Rejected(ch.get("reason") or "serwer nie zna tego urządzenia")
    msg = f"emustart-auth|{did}|{ch['nonce']}".encode("utf-8")
    sig = base64.b64encode(keystore.sign(name, kind, msg)).decode()
    t = _post_sp(sp, "/v1/auth/token", {"device": did, "nonce": ch["nonce"], "sig": sig})
    if not t.get("ok"):
        raise Rejected(t.get("reason") or "logowanie odrzucone")
    with _session_lock:
        _session.update(did=did, tok=t["token"], exp=float(t["expires"]))
    return t["token"]


def session_token(did: str, sp=None) -> str:
    """Ważny token sesji urządzenia; logowanie przez `sp`, ostatni działający adres albo
    kolejne adresy serwera."""
    with _session_lock:
        if _session.get("did") == did and _session.get("exp", 0) - TOKEN_MARGIN > time.time():
            return _session["tok"]
    if did not in _signer:
        return ""
    with _login_lock:
        with _session_lock:
            if _session.get("did") == did and _session.get("exp", 0) - TOKEN_MARGIN > time.time():
                return _session["tok"]        # zalogował się inny wątek, gdy ten czekał
        if rejected():
            return ""                         # serwer niedawno odmówił — bez kolejnej rundy
        return _login_any(did, sp)


class Rejected(OSError):
    """Serwer odpowiedział, ale nie wpuszcza tego urządzenia (nieznane, unieważnione)."""


class CertMismatch(ssl.SSLError):
    """Pod adresem serwera jest inny certyfikat niż zapamiętany (inny serwer albo podmiana)."""


def _login_any(did: str, sp) -> str:
    tries = [sp] if sp else []
    tries += [h for h in _hint if h not in tries]
    if _cfg_ref:
        # martwy adres = 10 s czekania na połączenie — przy kilku logowanie trwało długo
        tries += [c for c in candidates(_cfg_ref[0]) if c not in tries and (not _alive or c in _alive)]
    for t in tries:
        try:
            tok = _login(did, t)
            _hint[:] = [t]
            _rejected.clear()
            return tok
        except (Rejected, CertMismatch) as ex:
            # odpowiedź jednoznaczna — pozostałe adresy prowadzą do tego samego serwera
            log.info("logowanie urządzenia przez %s:%s: %s", t[1], t[2], ex)
            _rejected.update(reason=("Serwer nie zna tego komputera — zaloguj się ponownie (Ustawienia → Zaloguj)."
                                     if isinstance(ex, Rejected) else
                                     "Serwer ma inny certyfikat niż zapamiętany (inny serwer?) — zaloguj się "
                                     "ponownie (Ustawienia → Zaloguj)."), at=time.time())
            return ""
        except (OSError, http.client.HTTPException, ValueError, KeyError) as ex:
            log.info("logowanie urządzenia przez %s:%s: %s", t[1], t[2], ex)
    return ""


def _request(ep, path: str, timeout: float = 5, gzip: bool = False) -> tuple[int, bytes, float]:
    """GET z kluczem; gzip — odpowiedź skompresowana (listy gier: kilka MB → kilkaset KB)."""
    host, port, key = ep
    t = time.monotonic()
    c = conn(host, port, timeout)
    try:
        c.request("GET", path, headers={**auth_headers(key), **({"Accept-Encoding": "gzip"} if gzip else {})})
        r = c.getresponse()
        body = r.read()
        if (r.getheader("Content-Encoding") or "").lower() == "gzip":
            import gzip as _gz
            body = _gz.decompress(body)
        return r.status, body, time.monotonic() - t
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
        if st == 401 and ep[2].startswith(DEV) and not rejected():
            drop_token()
            st, body, rtt = _request(ep, "/v1/info")
        if st == 401:
            res = {"ok": False, "key_rejected": True,
                   "reason": "zły klucz serwera" if not ep[2].startswith(DEV)
                   else rejected() or "serwer nie przyjął klucza urządzenia (unieważnione?)"}
        elif st != 200:
            res = {"ok": False, "reason": f"serwer odpowiedział {st}"}
        else:
            d = json.loads(body)
            learn(cfg, d, trusted=(ep[0], ep[1]) in _PINS or (ep[0], ep[1]) in _CA)
            res = {"ok": True, "name": d.get("name", ""), "version": d.get("version", ""),
                   "features": d.get("features") or [], "nas_id": d.get("nas_id", ""), "rtt": rtt,
                   "admin": bool(d.get("admin"))}
    except (OSError, http.client.HTTPException, ValueError) as ex:
        res = {"ok": False, "reason": f"brak połączenia z {ep[0]}:{ep[1]} ({ex})"}
        forget_best()
    with _info_lock:
        _info.update(ep=ep, t=time.monotonic(), res=res)
    return res


_info_bg = threading.Lock()


def info_nowait(cfg: dict) -> dict | None:
    """Ostatni znany stan serwera bez czekania (pasek, start programu): odświeżenie w tle,
    gdy nieaktualny. None = jeszcze nic nie wiadomo (łączenie)."""
    with _info_lock:
        res, fresh = _info.get("res"), time.monotonic() - _info.get("t", 0) < INFO_TTL
    if not fresh and _info_bg.acquire(blocking=False):
        def run():
            try:
                info(cfg, fresh=True)
            finally:
                _info_bg.release()
        threading.Thread(target=run, daemon=True, name="stan-serwera").start()
    return res


def sources(cfg: dict, g: dict) -> dict:
    """{rel: RemoteFile} dla plików gry dostępnych na serwerze w tym samym rozmiarze.
    Pusty / niepełny = serwer niedostępny albo nie ma tej gry."""
    return find(cfg, g)[0]


def find(cfg: dict, g: dict) -> tuple[dict, list]:
    """(pliki gry {rel: RemoteFile}, zależności arcade [(rel, RemoteFile, rozmiar)] — gra
    nadrzędna i BIOS, które serwer ma obok gry). Serwer niedostępny: ({}, [])."""
    ep = endpoint(cfg)
    if not ep:
        return {}, []
    i = info(cfg)
    if not i.get("ok") or "find" not in i.get("features", []):
        if i.get("ok"):
            log.info("serwer %s (EmuStart %s) nie umie szukać gier — zaktualizuj go",
                     i.get("name"), i.get("version"))
        return {}, []
    q = urllib.parse.urlencode({"es": g["es"], "rel": g["rel"].replace("\\", "/")})
    try:
        st, body, _rtt = _request(ep, f"/v1/find?{q}", timeout=10)
    except (OSError, http.client.HTTPException) as ex:
        log.info("serwer: szukanie %s: %s", g["title"], ex)
        return {}, []
    if st != 200:
        log.info("serwer nie ma gry %s (%s)", g["title"], st)
        return {}, []
    d = json.loads(body)
    remote = {f["path"].replace("\\", "/"): f["size"] for f in d.get("files", [])}
    out = {}
    for rel, size, _mt in g["files"]:
        if remote.get(rel.replace("\\", "/")) == size:
            out[rel] = RemoteFile(ep[0], ep[1], ep[2], int(d["id"]), rel, size)
    deps = [(f["path"], RemoteFile(ep[0], ep[1], ep[2], int(d["id"]), f["path"], int(f["size"])), int(f["size"]))
            for f in d.get("deps") or [] if f.get("path") and ".." not in f["path"].split("/")]
    log.info("serwer %s: %d/%d plików gry %s%s", ep[0], len(out), len(g["files"]), g["title"],
             f" + zależności: {', '.join(r for r, _s, _n in deps)}" if deps else "")
    return out, deps


def parse_login(text: str) -> dict:
    """Wpis „Zaloguj”: link z kodu QR panelu (emustart://login?d=…) albo adres tunelu.
    → {"ok", "endpoints": [(url, kind, ca)], "fp", "code", "name"} albo {"ok": False, "reason"}.
    Kod QR niesie adresy i odcisk certyfikatu serwera — połączenie od pierwszego zapytania
    szyfrowane i przypięte. Sam adres: tylko domena z prawdziwym certyfikatem (tunel); adres
    IP nie ma czym zweryfikować — wtedy kod QR / link z panelu."""
    import base64
    text = (text or "").strip()
    if text.lower().startswith("emustart://login"):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query)
        raw = (q.get("d") or [""])[0]
        try:
            d = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
            eps = [(e["u"], e.get("k", ""), bool(e.get("ca"))) for e in d.get("e") or []
                   if str(e.get("u", "")).startswith("https://")]
            fp = str(d.get("f") or "")
            if not eps or not re.fullmatch(r"[0-9a-f]{64}", fp):
                raise ValueError("brak adresów albo odcisku")
            return {"ok": True, "endpoints": eps, "fp": fp, "code": str(d.get("c") or ""), "name": str(d.get("n") or "")}
        except (ValueError, KeyError, TypeError, AttributeError) as ex:
            return {"ok": False, "reason": f"To nie jest kod logowania EmuStart ({ex})."}
    if not text:
        return {"ok": False, "reason": "Podaj adres serwera (np. emustart.emucloud.org) albo zeskanuj kod QR z panelu."}
    if text.lower().startswith("http://"):
        return {"ok": False, "reason": "Tylko połączenie szyfrowane — podaj adres https (tunel) albo użyj kodu QR z panelu."}
    sp = _split(text if "://" in text else "https://" + text)
    if not sp or re.fullmatch(r"[\d.]+|[0-9a-f:]+", sp[1]) or "." not in sp[1]:
        return {"ok": False, "reason": "Adres IP albo nazwę komputera da się sprawdzić tylko kodem QR z panelu serwera "
                                       "(Dodaj urządzenie) — wpisz adres tunelu (np. emustart.emucloud.org)."}
    url = f"https://{sp[1]}" + ("" if sp[2] == 443 else f":{sp[2]}")
    return {"ok": True, "endpoints": [(url, "tunnel", True)], "fp": "", "code": "", "name": ""}


def _post(url: str, path: str, data: dict, key: str = "", fp: str = "", timeout: float = 15) -> dict:
    """POST JSON na serwer tylko szyfrowanym połączeniem: z odciskiem `fp` — przypięty
    certyfikat serwera, bez — zwykłe sprawdzanie certyfikatu (tunel). → odpowiedź z "url"
    albo {"ok": False, "reason"}."""
    sp = _split(url)
    if not sp or sp[0] != "https":
        return {"ok": False, "reason": "Tylko połączenie szyfrowane (https)."}
    _scheme, host, port = sp
    h = {"Content-Type": "application/json", **(auth_headers(key) if key else {})}
    c = (PinnedHTTPS(host, port, fp, timeout) if fp
         else http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl.create_default_context()))
    try:
        c.request("POST", path, body=json.dumps(data).encode("utf-8"), headers=h)
        r = c.getresponse()
        d = json.loads(r.read() or b"{}")
    except (OSError, http.client.HTTPException, ValueError) as ex:
        return {"ok": False, "reason": f"Brak połączenia z {host}:{port} ({ex})", "unreachable": True}
    finally:
        c.close()
    if not isinstance(d, dict):
        d = {}
    if r.status == 404:
        return {"ok": False, "reason": "Serwer ma starszą wersję EmuStart — zaktualizuj go (panel serwera)."}
    d.setdefault("ok", False)
    if not d["ok"] and not d.get("reason"):
        d["reason"] = d.get("error") or f"Serwer odpowiedział {r.status}."
    d["url"] = f"https://{host}" + ("" if port == 443 else f":{port}")
    return d


def authed_post(cfg: dict, path: str, data: dict) -> dict:
    """POST JSON do serwera z ustawień (najlepszy adres, klucz / token urządzenia)."""
    ep = endpoint(cfg)
    if not ep:
        return {"ok": False, "reason": "serwer nie jest ustawiony"}
    for attempt in range(2):
        c = conn(ep[0], ep[1], 15)
        try:
            c.request("POST", path, body=json.dumps(data).encode("utf-8"),
                      headers={**auth_headers(ep[2]), "Content-Type": "application/json"})
            r = c.getresponse()
            body = r.read()
        except (OSError, http.client.HTTPException) as ex:
            forget_best()
            return {"ok": False, "reason": f"brak połączenia z serwerem ({ex})"}
        finally:
            c.close()
        if r.status == 401 and ep[2].startswith(DEV) and attempt == 0:
            drop_token()
            continue
        try:
            d = json.loads(body or b"{}")
        except ValueError:
            d = {}
        if r.status == 404 and not d:
            return {"ok": False, "reason": "Serwer ma starszą wersję EmuStart — zaktualizuj go (panel serwera)."}
        return d if isinstance(d, dict) else {"ok": False}
    return {"ok": False, "reason": "serwer nie przyjął klucza"}
