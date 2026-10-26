"""Strona serwera (0.49): zaproszenia, logowanie Google, sesja, kod logowania, programy do pobrania,
podpisy programów, wstrzymanie aktualizacji PCSX2 przy wspólnych stanach PS2."""

from __future__ import annotations

import base64
import http.client
import io
import json
import struct
import threading
import time
import urllib.parse
import zipfile
from http.server import ThreadingHTTPServer

import pytest

from emustart import (accounts, clientdist, devices, enroll, invites, paths, public, remote, server, signing,
                      webportal)

pytest.importorskip("cryptography")

SUB, EMAIL = "1234567890", "gracz@example.com"


class _Public(ThreadingHTTPServer):
    public = True
    daemon_threads = True


@pytest.fixture()
def site(monkeypatch):
    nas = paths.SERVER / "profiles"
    nas.mkdir(parents=True)
    (nas / "Jezus").mkdir()
    (nas / "Jezus" / "emustart.json").write_text("{}")
    monkeypatch.setattr(remote, "NAS_ROOT", nas)
    monkeypatch.setitem(public.TUNNEL, "url", "https://emustart.example.org")
    monkeypatch.setattr(remote, "TLS", {"port": 8741, "fp": "ab" * 32})
    monkeypatch.setattr(remote, "endpoints", lambda: [{"url": "https://192.168.1.2:8741", "kind": "lan"},
                                                      {"url": "https://emustart.example.org", "kind": "tunnel", "ca": True}])
    webportal.set_google("abc.apps.googleusercontent.com", "sekret")
    srv = _Public(("127.0.0.1", 0), server._Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], nas
    srv.shutdown()


def _http(port, method, path, body=None, cookie="", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {**({"Cookie": cookie} if cookie else {}), **(headers or {})}
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    return r.status, data, r


def _google(monkeypatch, sub=SUB, email=EMAIL, **over):
    """Google podstawiony: wymiana kodu zwraca pola tokenu tożsamości (z nonce przepływu)."""
    def fake(code, verifier):
        assert code == "kod-google" and verifier
        return {"iss": "https://accounts.google.com", "aud": "abc.apps.googleusercontent.com", "exp": time.time() + 600,
                "sub": sub, "email": email, "email_verified": True, "nonce": over.pop("nonce", None) or _nonce[0],
                **over}
    monkeypatch.setattr(webportal, "exchange", fake)


_nonce = [""]


def _login(port, invite=""):
    """Przeglądarka: /auth/google → (Google) → /auth/google/callback → ciasteczko sesji."""
    st, _d, r = _http(port, "GET", "/auth/google" + (f"?zaproszenie={invite}" if invite else ""))
    assert st == 302
    loc = urllib.parse.urlsplit(r.getheader("Location"))
    q = urllib.parse.parse_qs(loc.query)
    assert loc.netloc == "accounts.google.com" and q["redirect_uri"] == ["https://emustart.example.org/auth/google/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["abc.apps.googleusercontent.com"]
    _nonce[0] = q["nonce"][0]
    flow_cookie = r.getheader("Set-Cookie").split(";")[0]
    st, _d, r = _http(port, "GET", f"/auth/google/callback?state={q['state'][0]}&code=kod-google", cookie=flow_cookie)
    assert st == 302
    cookies = [c.split(";")[0] for c in r.msg.get_all("Set-Cookie")]
    sess = next((c for c in cookies if c.startswith(webportal.COOKIE + "=") and c != webportal.COOKIE + "="), "")
    return r.getheader("Location"), sess


def test_start_page_and_assets_without_device_token(site):
    port, _nas = site
    st, html, r = _http(port, "GET", "/")
    assert st == 200 and b"Zaloguj przez Google" in html and "script-src 'self'" in r.getheader("Content-Security-Policy")
    assert _http(port, "GET", "/portal/portal.js")[0] == 200
    assert _http(port, "GET", "/portal/../../emustart/remote.py")[0] in (403, 404)
    st, me, _ = _http(port, "GET", "/web/me")
    assert me == {"ok": True, "google": True, "logged": False, "invite": False}
    # reszta portu publicznego bez zmian: API bez tokenu — 401, panel — 404
    assert _http(port, "GET", "/v1/info")[0] == 401
    assert _http(port, "GET", "/admin/status")[0] == 404


def test_only_invited_google_account_gets_in_and_account_is_linked(site, monkeypatch):
    port, nas = site
    _google(monkeypatch)
    # bez zaproszenia — odmowa, nic nie powstaje
    loc, sess = _login(port)
    assert loc == "/?blad=brak-zaproszenia" and not sess and not accounts.by_google(SUB)
    # zaproszenie dla innego adresu z linkiem — odmowa
    other = invites.create("ktos@example.com", "Ola")
    assert _login(port, other["token"])[0] == "/?blad=inny-adres"
    # zaproszenie tego adresu → nowe konto, połączone z Google, zaproszenie zużyte
    inv = invites.create("Gracz@Example.com", "Ola")
    assert _http(port, "GET", f"/web/me?zaproszenie={inv['token']}")[1]["invite"]
    loc, sess = _login(port, inv["token"])
    assert loc == "/" and sess
    assert accounts.by_google(SUB) == "Ola" and (nas / "Ola" / "emustart.json").is_file()
    assert next(i for i in invites.listing() if i["id"] == inv["invite"]["id"])["used"]
    st, me, _ = _http(port, "GET", "/web/me", cookie=sess)
    assert me["logged"] and me["account"] == "Ola" and me["email"] == EMAIL and not me["password"]
    # kolejne logowanie — po identyfikatorze Google, bez zaproszenia
    loc, sess2 = _login(port)
    assert loc == "/" and sess2
    # odłączenie w panelu — sesje przestają działać od razu
    accounts.unlink_google("Ola")
    assert not _http(port, "GET", "/web/me", cookie=sess2)[1]["logged"]


def test_invite_to_existing_account_links_it(site, monkeypatch):
    port, _nas = site
    _google(monkeypatch)
    invites.create(EMAIL, "jezus")
    loc, sess = _login(port)
    assert loc == "/" and accounts.by_google(SUB) == "Jezus"


@pytest.mark.parametrize("over", [{"aud": "inna.apps.googleusercontent.com"}, {"iss": "https://evil"},
                                  {"exp": 1}, {"email_verified": False}, {"nonce": "zly"}])
def test_bad_identity_token_refused(site, monkeypatch, over):
    port, _nas = site
    invites.create(EMAIL, "Ola")
    _google(monkeypatch, **over)
    loc, sess = _login(port)
    assert loc == "/?blad=google" and not sess and not accounts.by_google(SUB)


def test_callback_needs_the_flow_cookie_and_state(site, monkeypatch):
    port, _nas = site
    _google(monkeypatch)
    invites.create(EMAIL, "Ola")
    st, _d, r = _http(port, "GET", "/auth/google")
    state = urllib.parse.parse_qs(urllib.parse.urlsplit(r.getheader("Location")).query)["state"][0]
    # inna przeglądarka (bez ciasteczka przepływu) — odmowa; zły state — odmowa
    assert _http(port, "GET", f"/auth/google/callback?state={state}&code=kod-google")[2].getheader("Location") == "/?blad=sesja"
    assert _http(port, "GET", "/auth/google/callback?state=zly&code=kod-google",
                 cookie=f"{webportal.FLOW_COOKIE}=zly")[2].getheader("Location") == "/?blad=sesja"
    assert not accounts.by_google(SUB)


def test_login_code_from_the_site_logs_in_a_device(site, monkeypatch):
    port, _nas = site
    _google(monkeypatch)
    invites.create(EMAIL, "Ola")
    _loc, sess = _login(port)
    # POST bez nagłówka strony (np. formularz z obcej strony) — odmowa
    assert _http(port, "POST", "/web/code", {}, cookie=sess)[0] == 403
    assert _http(port, "POST", "/web/code", {}, headers={"X-EmuStart-Web": "1"})[0] == 401
    st, c, _ = _http(port, "POST", "/web/code", {}, cookie=sess, headers={"X-EmuStart-Web": "1"})
    assert st == 200 and c["ok"] and c["server"] == "emustart.example.org"
    from emustart import netsrc
    login = netsrc.parse_login(c["uri"])
    # strona daje tylko adresy z internetu; kod przypisany do konta strony
    assert login["account"] == "Ola" and [u for u, _k, _c in login["endpoints"]] == ["https://emustart.example.org"]
    import keyhelp
    r = enroll.request("code", "Jezus", "Telefon", "phone", "1.2.3.4", "0.49", keyhelp.new_key()[1], ["Jezus", "Ola"],
                       login_code=c["pretty"])
    assert r["status"] == "approved"
    did = enroll.poll(r["rid"])["device"]
    assert devices.account_raw(did) == "Ola"


def test_site_password_and_logout(site, monkeypatch):
    port, _nas = site
    _google(monkeypatch)
    invites.create(EMAIL, "Ola")
    _loc, sess = _login(port)
    h = {"X-EmuStart-Web": "1"}
    assert not _http(port, "POST", "/web/password", {"password": "slabe"}, cookie=sess, headers=h)[1]["ok"]
    assert _http(port, "POST", "/web/password", {"password": "Haslo-testowe1"}, cookie=sess, headers=h)[1]["ok"]
    assert accounts.verify("Ola", "Haslo-testowe1")[0]
    _http(port, "POST", "/web/logout", {}, cookie=sess, headers=h)
    assert not _http(port, "GET", "/web/me", cookie=sess)[1]["logged"]


def test_downloads_only_for_logged_in(site, monkeypatch):
    port, _nas = site
    d = paths.SERVER / "clients" / "emustart-windows"
    d.mkdir(parents=True)
    (d / "EmuStart-0.49.0-win64.zip").write_bytes(b"PK zip")
    (paths.SERVER / "clients" / "index.json").write_text(json.dumps(
        {"emustart-windows": {"version": "0.49.0", "file": "EmuStart-0.49.0-win64.zip", "size": 6}}))
    st, _d, r = _http(port, "GET", "/dl/emustart-windows")
    assert st == 302 and r.getheader("Location") == "/"
    _google(monkeypatch)
    invites.create(EMAIL, "Ola")
    _loc, sess = _login(port)
    st, body, r = _http(port, "GET", "/dl/emustart-windows", cookie=sess)
    assert st == 200 and body == b"PK zip" and "EmuStart-0.49.0-win64.zip" in r.getheader("Content-Disposition")
    assert _http(port, "GET", "/dl/lumen-windows", cookie=sess)[0] == 404
    assert _http(port, "GET", "/dl/..%2Findex.json", cookie=sess)[0] == 404
    me = _http(port, "GET", "/web/me", cookie=sess)[1]
    assert {a["id"]: a["ready"] for a in me["apps"]} == {"emustart-windows": True, "emustart-android": False,
                                                         "lumen-windows": False}


def test_panel_invites_and_google_config(site):
    from emustart import srvadmin
    r = srvadmin.invite_create(None, {"email": "nowy@example.com", "account": "Nowy"})
    assert r["ok"] and r["link"].startswith("https://emustart.example.org/?zaproszenie=")
    token = r["link"].split("=", 1)[1]
    assert token not in (paths.SERVER / "invites.json").read_text(encoding="utf-8")    # tylko skrót
    assert not srvadmin.invite_create(None, {"email": "zly", "account": "X"})["ok"]
    assert not srvadmin.invite_create(None, {"email": "a@b.pl", "account": "CON"})["ok"]
    lst = srvadmin.invites_list(None)
    assert lst["google"]["ready"] and [i["email"] for i in lst["invites"]] == ["nowy@example.com"]
    assert srvadmin.invite_delete(None, {"id": r["invite"]["id"]})["ok"] and not invites.listing()
    assert not srvadmin.google_set(None, {"client_id": "zly", "client_secret": "x"})["ok"]
    # nowy sekret pusty = zostaje zapisany
    assert srvadmin.google_set(None, {"client_id": "abc.apps.googleusercontent.com", "client_secret": ""})["ready"]
    assert "sekret" not in json.dumps(srvadmin.invites_list(None))


# ── programy do pobrania: podpis wydania ──
def _release(monkeypatch, files: dict, sigs: dict):
    def fake_get(url, timeout=30):
        if url.startswith("https://api.github.com/"):
            repo = url.split("/repos/")[1].split("/releases")[0]
            assets = [{"name": n, "size": len(b), "browser_download_url": f"https://dl/{n}", "digest": ""}
                      for n, b in files.items() if ("Lumen" in n) == (repo == "C4rl0s79/Launcher")]
            assets += [{"name": n + ".sig", "browser_download_url": f"https://dl/{n}.sig"} for n in sigs
                       if ("Lumen" in n) == (repo == "C4rl0s79/Launcher")]
            ver = "1.0.13" if repo == "C4rl0s79/Launcher" else "0.49.0"
            return io.BytesIO(json.dumps({"tag_name": "v" + ver, "assets": assets}).encode())
        name = url.rsplit("/", 1)[1]
        if name.endswith(".sig"):
            return io.BytesIO(sigs[name[:-4]].encode())
        return io.BytesIO(files[name])
    monkeypatch.setattr(clientdist, "_get", lambda url, timeout=30: _Ctx(fake_get(url)))


class _Ctx:
    def __init__(self, f):
        self.f = f

    def __enter__(self):
        return self.f

    def __exit__(self, *a):
        return False


def test_client_downloads_need_a_valid_release_signature(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    import hashlib
    es_key, lu_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    pub = lambda k: base64.b64encode(k.public_key().public_bytes_raw()).decode()   # noqa: E731
    monkeypatch.setitem(clientdist.APPS["emustart-windows"], "key", pub(es_key))
    monkeypatch.setitem(clientdist.APPS["emustart-android"], "key", pub(es_key))
    monkeypatch.setitem(clientdist.APPS["lumen-windows"], "key", pub(lu_key))
    files = {"EmuStart-0.49.0-win64.zip": b"zip", "EmuStart-0.49.0-android.apk": b"apk",
             "Lumen-1.0.13-setup.exe": b"exe"}

    def sig(k, name, prefix):
        return base64.b64encode(k.sign(signing.message(name, hashlib.sha256(files[name]).hexdigest(), prefix))).decode()
    sigs = {"EmuStart-0.49.0-win64.zip": sig(es_key, "EmuStart-0.49.0-win64.zip", signing.PREFIX),
            # APK podpisany kluczem Lumen — odrzucony
            "EmuStart-0.49.0-android.apk": sig(lu_key, "EmuStart-0.49.0-android.apk", signing.PREFIX),
            "Lumen-1.0.13-setup.exe": sig(lu_key, "Lumen-1.0.13-setup.exe", signing.LUMEN_PREFIX)}
    _release(monkeypatch, files, sigs)
    clientdist.refresh()
    st = clientdist.status()["apps"]
    assert st["emustart-windows"]["ready"] and st["emustart-windows"]["version"] == "0.49.0"
    assert st["lumen-windows"]["ready"] and clientdist.path("lumen-windows").read_bytes() == b"exe"
    assert not st["emustart-android"]["ready"] and "podpis" in st["emustart-android"]["error"]


def test_old_client_file_in_use_does_not_fail_the_update(monkeypatch):
    """Windows: plik poprzedniej wersji właśnie pobierany ze strony — nowa wersja i tak trafia
    do indeksu (wcześniej błąd usuwania starego pliku psuł całą aktualizację)."""
    import hashlib
    from pathlib import Path
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    monkeypatch.setitem(clientdist.APPS["emustart-windows"], "key",
                        base64.b64encode(key.public_key().public_bytes_raw()).decode())
    d = paths.SERVER / "clients" / "emustart-windows"
    d.mkdir(parents=True)
    (d / "EmuStart-0.48.3-win64.zip").write_bytes(b"stary")
    files = {"EmuStart-0.49.0-win64.zip": b"nowy"}
    sig = base64.b64encode(key.sign(signing.message("EmuStart-0.49.0-win64.zip",
                                                    hashlib.sha256(b"nowy").hexdigest()))).decode()
    _release(monkeypatch, files, {"EmuStart-0.49.0-win64.zip": sig})
    real = Path.unlink

    def busy(self, missing_ok=False):
        if self.name.startswith("EmuStart-0.48.3"):
            raise PermissionError("w użyciu")
        return real(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", busy)
    clientdist.refresh(["emustart-windows"])
    st = clientdist.status()["apps"]["emustart-windows"]
    assert st["version"] == "0.49.0" and st["ready"] and not st["error"]


def test_profile_names_without_profiles_folder(monkeypatch):
    monkeypatch.setattr(remote, "NAS_ROOT", None)
    assert remote.profile_names() == []


# ── PCSX2: format stanów PS2 i wstrzymanie aktualizacji ──
def _p2s(version: int) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("PCSX2 Savestate Version.id", struct.pack("<I", version))
        z.writestr("eeMemory.bin", b"\0" * 16)
    return b.getvalue()


_clock = [1000]


def _states_bundle(game: str, versions_by_device: list):
    """Paczka stanów PS2 gry z historią: [(urządzenie, format)] od najstarszej."""
    from emustart import savesync
    gdir = savesync._root() / "Jezus" / "ps2" / game
    (gdir / "bundles").mkdir(parents=True)
    entries = []
    for i, (dev, ver) in enumerate(versions_by_device):
        bid = f"bnd_{game}_{i}"
        with zipfile.ZipFile(gdir / "bundles" / f"{bid}.zip", "w") as z:
            z.writestr(f"SLUS-20946 (ABCD1234).0{i}.p2s", _p2s(ver))
        _clock[0] += 1
        entries.append({"bundle_id": bid, "updated_at": _clock[0], "updated_by_device_id": dev})
    cur = dict(entries[-1], history=list(reversed(entries[:-1])))
    (gdir / "manifest.json").write_text(json.dumps({"emulators": {"pcsx2": {"bundles": {"states": cur}}}}))


def test_ps2_state_formats_and_hold(monkeypatch):
    from emustart import ps2states
    pc = devices.add_keyed("PC", "pc", "1.1.1.1", "0.49", "pem1", "Jezus")
    ph = devices.add_keyed("Telefon", "phone", "1.1.1.2", "0.49", "pem2", "Jezus")
    assert ps2states.hold_reason() is None                      # bez stanów — aktualizacje jak zawsze
    _states_bundle("sotc", [(ph, 0x9A510000), (pc, 0x9A510000)])
    f = ps2states.formats()
    assert f["pc"]["version"] == f["phone"]["version"] == 0x9A510000 and f["games"] == 1
    assert ps2states.hold_reason()["kind"] == "shared"
    _states_bundle("gt4", [(pc, 0x9A520000)])                      # PC już na nowszym formacie
    assert ps2states.hold_reason()["kind"] == "diverged"
    _states_bundle("ico", [(ph, 0x9A530000)])                      # telefon dogonił i przegonił
    assert ps2states.hold_reason()["kind"] == "ahead"
    assert ps2states.fmt(0x9A510000) == "9a51.0" and ps2states.fmt(None) == "—"


def test_pcsx2_update_waits_for_the_admin(monkeypatch):
    from emustart import emucatalog, emustore
    cfg = {"emu_store_dir": str(paths.SERVER / "emustore")}
    monkeypatch.setattr(emustore, "store_dir", lambda c: paths.SERVER / "emustore")
    monkeypatch.setattr(emustore, "ensure_archives", lambda c: None)
    monkeypatch.setattr(emustore, "_refresh_core_list", lambda c: None)
    monkeypatch.setattr(emustore, "manifest", lambda c, i, v: {"x": 1})
    monkeypatch.setattr(emucatalog, "latest", lambda item: {"version": "v2.2"})
    built = []
    monkeypatch.setattr(emustore, "_build", lambda c, item, rel, p=None: (built.append(item), {"version": rel["version"]})[1])
    monkeypatch.setattr(emustore, "_prune", lambda *a: None)
    monkeypatch.setattr(emucatalog, "item_info", lambda i: {"label": i})
    idx = emustore.load_index(cfg)
    idx["items"]["pcsx2"] = {"version": "v2.0"}
    emustore._save_index(cfg, idx)
    from emustart import ps2states
    monkeypatch.setattr(ps2states, "hold_reason", lambda f=None: {"kind": "diverged", "text": "rozbieżne"})
    r = emustore.update(cfg, ["pcsx2"])
    assert r["ok"] and built == [] and emustore.load_index(cfg)["held"]["pcsx2"]["version"] == "v2.2"
    assert emustore.update(cfg, ["pcsx2"], force=True) and built == []           # „Pobierz ponownie” nie omija
    emustore.update(cfg, ["pcsx2"], allow_held=["pcsx2"])                          # administrator zatwierdził
    assert built == ["pcsx2"] and "pcsx2" not in emustore.load_index(cfg)["held"]
