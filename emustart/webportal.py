"""webportal — strona serwera w internecie (0.49): logowanie Google na zaproszenie, programy, kod logowania.

Adres tunelu (np. https://emustart.emucloud.org, emustart/public.py → port publiczny) pokazuje
stronę startową (web/portal): logo, grafika i „Zaloguj przez Google”. Wpuszczony jest tylko adres
z zaproszeniem z panelu serwera (emustart/invites.py) albo konto Google już połączone z kontem
serwera. Po zalogowaniu strona daje:
- programy do pobrania (emustart/clientdist.py — kopie z serwera, sprawdzone podpisem);
- **kod logowania** urządzenia (jednorazowy, 10 min, przypisany do konta — emustart/enroll.py):
  link / kod QR dla aplikacji albo adres serwera + 8 znaków do wpisania padem;
- hasło konta (opcjonalne — do „Mam konto” i dodania konta na wspólnym urządzeniu).

Logowanie Google (OpenID Connect, przepływ z kodem + PKCE): serwer wymienia kod na tokeny
bezpośrednio u Google (z sekretem klienta, szyfrowanym połączeniem) — token tożsamości nie
przechodzi przez przeglądarkę, więc wystarcza sprawdzenie pól (wydawca, odbiorca, ważność,
nonce, potwierdzony e-mail). Konfiguracja (identyfikator i sekret klienta OAuth z Google Cloud)
— panel serwera; plik `google.json` w danych serwera (tylko na tym komputerze).

Sesja strony: losowy token w ciasteczku (HttpOnly, Secure, SameSite=Lax), 12 h, w pamięci
serwera (restart = ponowne logowanie). Zapytania zmieniające coś (POST) wymagają nagłówka
X-EmuStart-Web — zwykła strona z innej domeny go nie wyśle (brak zgody CORS).

Adresy (tylko port publiczny / tunel):
    GET  /                       strona (web/portal/index.html), GET /portal/… — jej pliki
    GET  /auth/google[?zaproszenie=…]   → Google;  GET /auth/google/callback
    GET  /web/me                 stan: zalogowany, konto, programy
    POST /web/code | /web/password | /web/logout
    GET  /dl/<program>           plik programu (zalogowani)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request

from emustart import paths

log = logging.getLogger("emustart.webportal")

PATHS = ("/", "/portal/", "/auth/", "/web/", "/dl/", "/favicon.ico")
SESSION_TTL = 12 * 3600
FLOW_TTL = 600
COOKIE, FLOW_COOKIE = "es_portal", "es_oauth"
GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
MAX_SESSIONS, MAX_FLOWS = 500, 200
# opisy programów na stronie (pliki — emustart/clientdist.py; zrzuty — web/portal/shots)
APPS = [
    {"id": "emustart-windows", "name": "EmuStart", "platform": "Windows 10/11 (64-bit)", "shot": "emustart.jpg",
     "desc": "Frontend emulatorów sterowany padem: biblioteka gier z serwera z okładkami, jedno kliknięcie do gry, "
             "emulatory i rdzenie instalowane i aktualizowane z serwera, zapisy i stany w chmurze serwera — "
             "zaczynasz na PC, kończysz na telefonie. Menu w grze, profile graczy, RetroAchievements."},
    {"id": "emustart-android", "name": "EmuStart na Androida", "platform": "Android 8+ (telefon, tablet, TV)",
     "shot": "android.jpg",
     "desc": "Ta sama biblioteka i te same zapisy na telefonie: gry pobierane z serwera, RetroArch i ArmSX2, "
             "synchronizacja zapisów przy starcie i końcu gry, „Wczytaj zapis” z innego urządzenia. "
             "Logowanie kodem QR z tej strony."},
    {"id": "lumen-windows", "name": "Lumen", "platform": "Windows 10/11 (64-bit)", "shot": "lumen.jpg",
     "desc": "Launcher na kanapę: gry Steam i gry z EmuStart w jednej bibliotece, tryb pulpitu i tryb TV "
             "sterowany padem. W środku silnik EmuStart — to samo konto, te same zapisy i emulatory z serwera."},
]
APP_IDS = {a["id"] for a in APPS}

_lock = threading.Lock()
_sessions: dict = {}             # token → {account, email, sub, exp}
_flows: dict = {}                # state → {verifier, nonce, invite, exp}


# ── konfiguracja Google ──
def _cfg_path():
    return paths.SERVER / "google.json"


def google_cfg() -> dict:
    try:
        d = json.loads(_cfg_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def set_google(client_id: str, client_secret: str = "") -> dict:
    """Panel: identyfikator i sekret klienta OAuth. Pusty sekret = bez zmiany sekretu."""
    client_id = (client_id or "").strip()
    cur = google_cfg()
    secret = (client_secret or "").strip() or (cur.get("client_secret", "") if client_id == cur.get("client_id") else "")
    if client_id and not client_id.endswith(".apps.googleusercontent.com"):
        return {"ok": False, "reason": "Identyfikator klienta kończy się na .apps.googleusercontent.com."}
    if client_id and not secret:
        return {"ok": False, "reason": "Podaj sekret klienta."}
    p = _cfg_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps({"client_id": client_id, "client_secret": secret} if client_id else {}), encoding="utf-8")
    os.replace(tmp, p)
    log.info("logowanie Google: %s", "skonfigurowane" if client_id else "wyłączone")
    return {"ok": True, **google_status()}


def base_url() -> str:
    from emustart import public
    return public.TUNNEL["url"]


def redirect_uri() -> str:
    return base_url() + "/auth/google/callback" if base_url() else ""


def google_status() -> dict:
    g = google_cfg()
    return {"client_id": g.get("client_id", ""), "secret": bool(g.get("client_secret")),
            "redirect_uri": redirect_uri(), "ready": ready()}


def ready() -> bool:
    g = google_cfg()
    return bool(g.get("client_id") and g.get("client_secret") and base_url())


# ── odpowiedzi ──
def _headers(h, ctype: str, n: int, extra: dict | None = None) -> None:
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(n))
    h.send_header("Cache-Control", "no-store")
    h.send_header("X-Content-Type-Options", "nosniff")
    h.send_header("X-Frame-Options", "DENY")
    h.send_header("Referrer-Policy", "no-referrer")
    h.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                             "script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
    for k, v in (extra or {}).items():
        for one in v if isinstance(v, list) else [v]:
            h.send_header(k, one)


def _json(h, data: dict, code: int = 200, cookies: list | None = None) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    _headers(h, "application/json; charset=utf-8", len(body), {"Set-Cookie": cookies or []})
    h.end_headers()
    h.wfile.write(body)


def _redirect(h, url: str, cookies: list | None = None) -> None:
    h.send_response(302)
    _headers(h, "text/plain", 0, {"Location": url, "Set-Cookie": cookies or []})
    h.end_headers()


def _cookie(name: str, value: str, max_age: int, path: str = "/") -> str:
    return f"{name}={value}; Max-Age={max_age}; Path={path}; HttpOnly; Secure; SameSite=Lax"


def _cookies(h) -> dict:
    out = {}
    for part in (h.headers.get("Cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k:
            out[k] = v
    return out


def _session(h) -> dict | None:
    from emustart import accounts
    tok = _cookies(h).get(COOKIE, "")
    now = time.time()
    with _lock:
        s = _sessions.get(tok) if tok else None
        if s and s["exp"] < now:
            _sessions.pop(tok, None)
            s = None
    # konto odłączone od Google w panelu — sesja przestaje działać od razu
    if s and accounts.by_google(s["sub"]).lower() != s["account"].lower():
        with _lock:
            _sessions.pop(tok, None)
        return None
    return s


def _body(h) -> dict:
    try:
        n = int(h.headers.get("Content-Length") or 0)
    except ValueError:
        n = 0
    if n <= 0 or n > 16384:
        return {}
    try:
        d = json.loads(h.rfile.read(n) or b"{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


# ── wejście z server.py ──
def handles(path: str) -> bool:
    return path == "/" or path == "/favicon.ico" or path.startswith(PATHS[1:5])


def get(h) -> None:
    sp = urllib.parse.urlsplit(h.path)
    path, q = sp.path, urllib.parse.parse_qs(sp.query)
    if path == "/auth/google":
        return _start(h, (q.get("zaproszenie") or [""])[0])
    if path == "/auth/google/callback":
        return _callback(h, q)
    if path == "/web/me":
        return _json(h, _me(h, (q.get("zaproszenie") or [""])[0]))
    if path.startswith("/dl/"):
        return _download(h, path[4:])
    if path == "/" or path == "/favicon.ico":
        return _static(h, "index.html" if path == "/" else "favicon.svg")
    if path.startswith("/portal/"):
        return _static(h, urllib.parse.unquote(path[len("/portal/"):]))
    return _json(h, {"ok": False}, 404)


def post(h) -> None:
    path = urllib.parse.urlsplit(h.path).path
    if h.headers.get("X-EmuStart-Web") != "1":
        return _json(h, {"ok": False, "reason": "brak nagłówka"}, 403)
    if path == "/web/logout":
        tok = _cookies(h).get(COOKIE, "")
        with _lock:
            _sessions.pop(tok, None)
        return _json(h, {"ok": True}, cookies=[_cookie(COOKIE, "", 0)])
    s = _session(h)
    if not s:
        return _json(h, {"ok": False, "reason": "Zaloguj się ponownie."}, 401)
    d = _body(h)
    if path == "/web/code":
        return _json(h, _new_code(s))
    if path == "/web/password":
        from emustart import accounts
        why = accounts.set_password(s["account"], str(d.get("password", "")), f"strona serwera ({s['email']})")
        return _json(h, {"ok": not why, "reason": why}, 200 if not why else 400)
    return _json(h, {"ok": False}, 404)


def _static(h, rel: str) -> None:
    base = (paths.WEB / "portal").resolve()
    target = (base / (rel or "index.html")).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return _json(h, {"ok": False}, 403)
    if not target.is_file():
        return _json(h, {"ok": False}, 404)
    data = target.read_bytes()
    ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    if target.suffix in (".svg",):
        ctype = "image/svg+xml"
    if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
        ctype += "; charset=utf-8"
    h.send_response(200)
    _headers(h, ctype, len(data))
    h.end_headers()
    h.wfile.write(data)


# ── stan strony ──
def _me(h, invite: str) -> dict:
    from emustart import accounts, clientdist, invites
    s = _session(h)
    out = {"ok": True, "google": ready(), "logged": bool(s)}
    if not s:
        out["invite"] = invites.token_ok(invite) if invite else False
        return out
    idx = clientdist.load_index()
    apps = []
    for a in APPS:
        e = idx.get(a["id"]) or {}
        apps.append({**a, "version": e.get("version", ""), "size": e.get("size", 0),
                     "ready": bool(clientdist.path(a["id"], idx))})
    out.update(account=s["account"], email=s["email"], apps=apps, password=accounts.has_password(s["account"]),
               rules=accounts.RULES, server=base_url().removeprefix("https://"))
    return out


def _new_code(s: dict) -> dict:
    from emustart import enroll, qr, remote
    try:
        c = enroll.new_login_code(s["account"], by=f"strona serwera, {s['email']}")
    except ValueError as ex:
        return {"ok": False, "reason": str(ex)}
    uri = remote.login_link(c["code"], s["account"], internet_only=True)
    if not uri:
        return {"ok": False, "reason": "Serwer nie ma adresu z internetu ani certyfikatu — sprawdź panel serwera."}
    log.info("kod logowania ze strony: konto %s (%s)", s["account"], s["email"])
    return {"ok": True, "pretty": c["pretty"], "expires_in": int(c["expires"] - time.time()), "uri": uri,
            "svg": qr.svg(uri, scale=5), "server": base_url().removeprefix("https://")}


def _download(h, app: str) -> None:
    from emustart import clientdist
    if not _session(h):
        return _redirect(h, "/")
    p = clientdist.path(app) if app in APP_IDS else None
    if not p:
        return _json(h, {"ok": False, "reason": "Serwer nie ma jeszcze tego programu."}, 404)
    size = p.stat().st_size
    h.send_response(200)
    ctype = "application/vnd.android.package-archive" if p.suffix == ".apk" else "application/octet-stream"
    _headers(h, ctype, size, {"Content-Disposition": f'attachment; filename="{p.name}"'})
    h.end_headers()
    with open(p, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.wfile.write(chunk)


# ── Google ──
def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _start(h, invite: str) -> None:
    if not ready():
        return _redirect(h, "/?blad=konfiguracja")
    state, verifier, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(48), secrets.token_urlsafe(16)
    now = time.time()
    with _lock:
        for k in [k for k, f in _flows.items() if f["exp"] < now]:
            _flows.pop(k, None)
        if len(_flows) >= MAX_FLOWS:
            return _redirect(h, "/?blad=zajety")
        _flows[state] = {"verifier": verifier, "nonce": nonce, "invite": invite[:100], "exp": now + FLOW_TTL}
    q = {"client_id": google_cfg()["client_id"], "redirect_uri": redirect_uri(), "response_type": "code",
         "scope": "openid email profile", "state": state, "nonce": nonce, "prompt": "select_account",
         "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"}
    _redirect(h, GOOGLE_AUTH + "?" + urllib.parse.urlencode(q),
              [_cookie(FLOW_COOKIE, state, FLOW_TTL, "/auth/")])


def exchange(code: str, verifier: str) -> dict:
    """Kod z Google → pola tokenu tożsamości (bez sprawdzenia — robi to _identity)."""
    g = google_cfg()
    data = urllib.parse.urlencode({"code": code, "client_id": g["client_id"], "client_secret": g["client_secret"],
                                   "redirect_uri": redirect_uri(), "grant_type": "authorization_code",
                                   "code_verifier": verifier}).encode()
    req = urllib.request.Request(GOOGLE_TOKEN, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        tok = json.loads(r.read())
    parts = str(tok.get("id_token") or "").split(".")
    if len(parts) != 3:
        raise ValueError("brak tokenu tożsamości")
    return json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))


def _identity(claims: dict, nonce: str) -> tuple[str, str]:
    """(sub, e-mail) z poprawnego tokenu; inaczej ValueError."""
    if claims.get("iss") not in ISSUERS:
        raise ValueError("zły wydawca tokenu")
    if claims.get("aud") != google_cfg().get("client_id"):
        raise ValueError("token dla innej aplikacji")
    if float(claims.get("exp") or 0) < time.time():
        raise ValueError("token wygasł")
    if claims.get("nonce") != nonce:
        raise ValueError("zły nonce")
    if claims.get("email_verified") not in (True, "true"):
        raise ValueError("niepotwierdzony e-mail")
    sub, email = str(claims.get("sub") or ""), str(claims.get("email") or "").strip().lower()
    if not sub or not email:
        raise ValueError("brak identyfikatora albo e-maila")
    return sub, email


def _callback(h, q: dict) -> None:
    from emustart import accounts, invites, public, remote
    ip = public.client_ip(h)
    state = (q.get("state") or [""])[0]
    clear = [_cookie(FLOW_COOKIE, "", 0, "/auth/")]
    with _lock:
        flow = _flows.pop(state, None) if state else None
    if not flow or flow["exp"] < time.time() or _cookies(h).get(FLOW_COOKIE) != state:
        public._fail(ip)
        return _redirect(h, "/?blad=sesja", clear)
    if q.get("error") or not q.get("code"):
        return _redirect(h, "/?blad=anulowane", clear)
    try:
        sub, email = _identity(exchange(q["code"][0], flow["verifier"]), flow["nonce"])
    except Exception as ex:
        log.warning("logowanie Google (%s): %s", ip, ex)
        public._fail(ip)
        return _redirect(h, "/?blad=google", clear)
    account = accounts.by_google(sub)
    if not account:
        inv, why = invites.find(email, flow["invite"])
        if not inv:
            log.info("logowanie Google bez zaproszenia: %s (%s)", email, ip)
            public._fail(ip)
            return _redirect(h, "/?blad=" + ("inny-adres" if why == "other" else "brak-zaproszenia"), clear)
        match = next((a for a in remote.profile_names() if a.lower() == inv["account"].lower()), "") or \
            (accounts.record(inv["account"]) or {}).get("name", "")
        account = match or inv["account"]
        if not match:
            remote.create_account(account)
            accounts.create(account, f"zaproszenie ({email})")
        accounts.link_google(account, sub, email)
        invites.use(inv["id"], account)
    tok = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for k in [k for k, s in _sessions.items() if s["exp"] < now]:
            _sessions.pop(k, None)
        while len(_sessions) >= MAX_SESSIONS:
            _sessions.pop(next(iter(_sessions)))
        _sessions[tok] = {"account": account, "email": email, "sub": sub, "exp": now + SESSION_TTL}
    log.info("strona serwera: zalogowano %s → konto %s (%s)", email, account, ip)
    _redirect(h, "/", clear + [_cookie(COOKIE, tok, SESSION_TTL)])


def reset() -> None:
    """Dla testów."""
    with _lock:
        _sessions.clear()
        _flows.clear()
