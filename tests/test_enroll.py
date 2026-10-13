"""Nowe instalacje przez internet (0.37): prośby, zatwierdzanie wg ról, logowanie podpisem,
urządzenie ograniczone do swojego konta."""

from __future__ import annotations

import base64
import http.client
import json

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from emustart import devices, enroll, nasfs, netsrc, paths, remote, server, srvadmin  # noqa: E402

MASTER = "m" * 32


class _Api:
    _cfg = {"server_token": MASTER}
    _scan: dict = {}


@pytest.fixture()
def srv(monkeypatch):
    nas = paths.SERVER / "profiles"
    (nas / "Jezus").mkdir(parents=True)
    (nas / "Jezus" / "emustart.json").write_text("{}")
    (nas / "Ania").mkdir()
    (nas / "Ania" / "emustart.json").write_text("{}")
    (nas / "Ania" / "save").mkdir()
    (nas / "Ania" / "save" / "a.srm").write_bytes(b"zapis Ani")
    monkeypatch.setattr(remote, "API", _Api())
    monkeypatch.setattr(remote, "NAS_ROOT", nas)
    monkeypatch.setattr(remote, "NAS_ID", nasfs.root_id(nas, create=True))
    monkeypatch.setattr(srvadmin, "ENABLED", True)
    enroll.reset()
    url = server.start(0, bind="127.0.0.1")
    netsrc._info.clear()
    host, port = url.removeprefix("http://").split(":")
    return host, int(port), nas


def _req(srv, method, path, key="", body=None):
    host, port, _ = srv
    c = http.client.HTTPConnection(host, port, timeout=10)
    h = {"Authorization": f"Bearer {key}"} if key else {}
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


PW = "Haslo-testowe1"          # spełnia wymagania hasła konta (emustart/accounts.py)


class Device:
    """Nowa instalacja: własna para kluczy (klucz prywatny nie wychodzi z „urządzenia”)."""

    def __init__(self, name):
        self.name = name
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pem = self.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.did = ""

    def ask(self, srv, mode, account, password=None):
        st, r = _req(srv, "POST", "/v1/enroll/request", body={"mode": mode, "account": account, "name": self.name,
                                                               "class": "phone", "version": "0.37", "pubkey": self.pem,
                                                               "password": PW if password is None else password})
        return r

    def login(self, srv, key=None):
        st, ch = _req(srv, "POST", "/v1/auth/challenge", body={"device": self.did})
        sig = (key or self.key).sign(enroll.message(self.did, ch["nonce"]), ec.ECDSA(hashes.SHA256()))
        st, t = _req(srv, "POST", "/v1/auth/token", body={"device": self.did, "nonce": ch["nonce"],
                                                           "sig": base64.b64encode(sig).decode()})
        return t.get("token", ""), t


def _link(monkeypatch) -> str:
    """Link logowania jak z kodu QR panelu, ale bez kodu jednorazowego — prośba czeka na
    zatwierdzenie. Komputer łączy się portem szyfrowanym z przypiętym certyfikatem."""
    import base64
    from _srv import tls_server
    if not getattr(monkeypatch, "_emustart_link", ""):
        url, fp = tls_server(monkeypatch)
        d = {"v": 1, "f": fp, "c": "", "e": [{"u": url, "k": "lan"}]}
        monkeypatch._emustart_link = "emustart://login?d=" + base64.urlsafe_b64encode(json.dumps(d).encode()).decode()
    return monkeypatch._emustart_link


def _admin_key(srv):
    """Urządzenie zero — kod z panelu serwera i klucz urządzenia → (token sesji, id)."""
    import keyhelp
    host, port, _nas = srv
    tok, did, _k = keyhelp.pair(host, port, "PC admina", "pc")
    return tok, did


def test_new_account_needs_admin_and_scopes_device(srv):
    admin_key, admin_id = _admin_key(srv)
    assert devices.is_admin(admin_id)
    phone = Device("Telefon Oli")
    r = phone.ask(srv, "new", "Ola")
    assert r["ok"] and len(r["code"]) == 8 and r["code"] == enroll.verify_code(phone.pem)
    # do zatwierdzenia nic nie przechodzi
    assert _req(srv, "POST", "/v1/enroll/poll", body={"rid": r["rid"]})[1]["status"] == "pending"
    # administrator widzi prośbę z kodem, adresem i nazwą
    st, p = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})
    q = p["requests"][0]
    assert q["name"] == "Telefon Oli" and q["account"] == "Ola" and q["code"] == enroll.pretty(r["code"]) and q["ip"]
    st, ok = _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": True, "games": False})
    assert ok["ok"] and ok["status"] == "approved"
    phone.did = _req(srv, "POST", "/v1/enroll/poll", body={"rid": r["rid"]})[1]["device"]
    assert (srv[2] / "Ola" / "emustart.json").is_file()                  # konto założone
    tok, _ = phone.login(srv)
    assert tok and _req(srv, "GET", "/v1/info", tok)[0] == 200
    # tylko swoje konto
    names = [p["name"] for p in _req(srv, "GET", "/v1/profiles", tok)[1]["profiles"]]
    assert names == ["Ola"]
    assert _req(srv, "GET", "/v1/nas/file?p=Ania/save/a.srm", tok)[0] == 403
    assert _req(srv, "PUT", "/v1/nas/file?p=Ola/save/b.srm&mtime=1", tok, body={"x": 1})[0] == 200
    assert _req(srv, "GET", "/v1/nas/file?p=Ola/save/b.srm", tok)[0] == 200
    # bez uprawnienia do gier
    assert _req(srv, "GET", "/v1/find?es=snes&rel=x.sfc", tok)[0] == 403
    # nie zmienia ustawień serwera
    assert _req(srv, "PUT", "/v1/roots", tok, body={"roots": []})[0] == 404     # foldery — tylko panel


def test_existing_account_approved_by_own_device_not_by_other_user(srv):
    admin_key, _ = _admin_key(srv)
    first = Device("Telefon Ani")
    r = first.ask(srv, "existing", "ania")                     # wielkość liter bez znaczenia
    assert r["ok"]
    # konto bez urządzeń — tylko administrator (zwykły użytkownik nie widzi)
    q = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"][0]
    assert _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": True, "games": True})[1]["ok"]
    first.did = _req(srv, "POST", "/v1/enroll/poll", body={"rid": r["rid"]})[1]["device"]
    tok_a, _ = first.login(srv)
    # drugie urządzenie Ani — zatwierdza pierwsze urządzenie Ani
    second = Device("Laptop Ani")
    r2 = second.ask(srv, "existing", "Ania")
    reqs = _req(srv, "POST", "/v1/enroll/pending", tok_a, body={})[1]["requests"]
    assert [x["name"] for x in reqs] == ["Laptop Ani"]
    # urządzenie innego konta tego nie widzi i nie zatwierdzi
    ola = Device("Telefon Oli")
    ro = ola.ask(srv, "new", "Ola")
    qo = next(x for x in _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"] if x["account"] == "Ola")
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": qo["id"], "allow": True, "games": False})
    ola.did = _req(srv, "POST", "/v1/enroll/poll", body={"rid": ro["rid"]})[1]["device"]
    tok_o, _ = ola.login(srv)
    assert _req(srv, "POST", "/v1/enroll/pending", tok_o, body={})[1]["requests"] == []
    assert not _req(srv, "POST", "/v1/enroll/decide", tok_o, body={"id": reqs[0]["id"], "allow": True})[1]["ok"]
    # zwykły użytkownik nie zatwierdza nowych kont
    nowe = Device("Obcy").ask(srv, "new", "Obcy")
    assert nowe["ok"]
    assert all(x["mode"] == "existing" for x in _req(srv, "POST", "/v1/enroll/pending", tok_a, body={})[1]["requests"])
    assert _req(srv, "POST", "/v1/enroll/decide", tok_a, body={"id": reqs[0]["id"], "allow": True, "games": True})[1]["ok"]
    second.did = _req(srv, "POST", "/v1/enroll/poll", body={"rid": r2["rid"]})[1]["device"]
    assert second.login(srv)[0]


def test_spoofing_needs_the_private_key(srv):
    admin_key, _ = _admin_key(srv)
    dev = Device("Telefon")
    r = dev.ask(srv, "new", "Ola")
    q = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"][0]
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": True})
    dev.did = _req(srv, "POST", "/v1/enroll/poll", body={"rid": r["rid"]})[1]["device"]
    # ktoś zna id urządzenia i klucz publiczny, ale nie ma klucza prywatnego
    obcy = ec.generate_private_key(ec.SECP256R1())
    tok, res = dev.login(srv, key=obcy)
    assert not tok and not res["ok"]
    # wyzwanie jest jednorazowe
    st, ch = _req(srv, "POST", "/v1/auth/challenge", body={"device": dev.did})
    sig = base64.b64encode(dev.key.sign(enroll.message(dev.did, ch["nonce"]), ec.ECDSA(hashes.SHA256()))).decode()
    assert _req(srv, "POST", "/v1/auth/token", body={"device": dev.did, "nonce": ch["nonce"], "sig": sig})[1]["ok"]
    assert not _req(srv, "POST", "/v1/auth/token", body={"device": dev.did, "nonce": ch["nonce"], "sig": sig})[1]["ok"]
    # unieważnione urządzenie traci też sesje
    tok, _ = dev.login(srv)
    assert _req(srv, "GET", "/v1/info", tok)[0] == 200
    devices.revoke(dev.did)
    assert _req(srv, "GET", "/v1/info", tok)[0] == 401


def test_requests_survive_restart_and_deny(srv):
    admin_key, _ = _admin_key(srv)
    dev = Device("Telefon")
    r = dev.ask(srv, "new", "Ola")
    assert dev.ask(srv, "new", "Ola")["rid"] == r["rid"]                  # ta sama prośba po restarcie aplikacji
    enroll._reqs = None                                                    # restart serwera — z dysku
    q = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"][0]
    assert q["name"] == "Telefon" and q["expires"] - q["created"] == enroll.ENROLL_TTL
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": False})
    assert _req(srv, "POST", "/v1/enroll/poll", body={"rid": r["rid"]})[1]["status"] == "denied"
    assert not (srv[2] / "Ola").exists()
    # istniejące konto nie da się założyć drugi raz
    assert "już istnieje" in Device("x").ask(srv, "new", "jezus")["reason"]


def test_new_pc_with_only_server_address(srv, monkeypatch):
    """Nowy komputer zna tylko adres serwera: prośba, zatwierdzenie na innym urządzeniu,
    logowanie podpisem kluczem z magazynu kluczy, praca z tokenem sesji."""
    from emustart import api as api_mod
    host, port, _nas = srv
    admin_key, _ = _admin_key(srv)
    saved = {}
    monkeypatch.setattr("emustart.config.save", lambda c: saved.update(c))
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg = {}
    r = a.server_enroll(_link(monkeypatch), "existing", "jezus", PW)
    assert r["ok"] and r["key"] in ("dpapi", "plik") and saved["enroll_pending"]["code"] == r["code"]
    assert a.server_enroll_poll()["status"] == "pending"
    # administrator (inne urządzenie) widzi prośbę z tym samym kodem
    q = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"][0]
    assert q["code"] == r["code"] and q["account"] == "Jezus"
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": True, "games": True})
    assert a.server_enroll_poll()["status"] == "approved"
    assert a._cfg["server_key"].startswith("dev:") and a._cfg["server_account"] == "Jezus"
    # logowanie podpisem i praca: info, folder profili — tylko swoje konto
    info = netsrc.info(a._cfg, fresh=True)
    assert info["ok"], info
    fs = nasfs.backend(a._cfg)
    fs.write("Jezus/save/x.srm", b"z nowego PC")
    assert fs.read("Jezus/save/x.srm") == b"z nowego PC"
    with pytest.raises(OSError):
        fs.read("Ania/save/a.srm")
    # restart serwera (tokeny w pamięci znikają) — klient loguje się ponownie sam
    enroll._tokens.clear()
    assert fs.read("Jezus/save/x.srm") == b"z nowego PC"


def test_concurrent_logins_share_one_session(srv, monkeypatch):
    """Kilka wątków naraz (pobieranie, zapisy, adresy) — jedno logowanie, ten sam token."""
    import threading
    from emustart import api as api_mod
    host, port, _nas = srv
    admin_key, _ = _admin_key(srv)
    monkeypatch.setattr("emustart.config.save", lambda c: None)
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg = {}
    a.server_enroll(_link(monkeypatch), "existing", "Jezus", PW)
    q = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"][0]
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": True})
    a.server_enroll_poll()
    netsrc.use_device(a._cfg)
    did = a._cfg["server_key"][4:]
    toks = []
    th = [threading.Thread(target=lambda: toks.append(netsrc.session_token(did))) for _ in range(8)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert len(set(toks)) == 1 and toks[0]


def test_after_approval_pc_uses_the_account_not_placeholder(srv, monkeypatch):
    from emustart import api as api_mod, library, profiles
    host, port, _nas = srv
    admin_key, _ = _admin_key(srv)
    monkeypatch.setattr("emustart.config.save", lambda c: None)
    assert [p["name"] for p in profiles.all_profiles()] == ["Gracz"]          # pierwsze uruchomienie
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._profile = {}, profiles.first_id()
    monkeypatch.setattr(a, "_user_load", lambda pid: None, raising=False)
    a.server_enroll(_link(monkeypatch), "existing", "Jezus", PW)
    q = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"][0]
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": True})
    assert a.server_enroll_poll()["status"] == "approved"
    names = [p["name"] for p in profiles.all_profiles()]
    assert names == ["Jezus"]                                                # pusty „Gracz” zniknął
    assert profiles.get(a._profile)["name"] == "Jezus" and not profiles.setup_needed()
    assert library.meta_get("last_profile", "") == str(a._profile)


def test_account_profile_needs_no_password_on_its_device(srv, monkeypatch):
    """Konto z hasłem — na urządzeniu z kluczem tego konta bez podawania hasła (klucz urządzenia)."""
    from emustart import accounts, api as api_mod, profiles
    host, port, nas = srv
    admin_key, _ = _admin_key(srv)
    accounts.set_password("Jezus", PW)
    monkeypatch.setattr("emustart.config.save", lambda c: None)
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._profile = {}, profiles.first_id()
    monkeypatch.setattr(a, "_user_load", lambda pid: None, raising=False)
    assert not a.server_enroll(_link(monkeypatch), "existing", "Jezus", "Zle-haslo-123")["ok"]
    a.server_enroll(_link(monkeypatch), "existing", "Jezus", PW)
    q = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"][0]
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": q["id"], "allow": True})
    a.server_enroll_poll()
    assert profiles.pin_state(a._cfg, a._profile, fresh=True) == "ok"
    assert profiles.account_of_device(a._cfg) == "Jezus"
    fs = nasfs.backend(a._cfg)
    fs.write("Jezus/save/y.srm", b"bez hasla")
    assert fs.read("Jezus/save/y.srm") == b"bez hasla"


def test_one_registration_per_hardware(srv):
    """Ten sam telefon dołączony ponownie (stare parowanie + nowe dołączenie) — jedna rejestracja;
    dwa różne telefony tego samego modelu (ta sama nazwa) — dwie."""
    hw = "a" * 32
    admin_key, _ = _admin_key(srv)
    # stare parowanie kodem — identyfikator sprzętu przychodzi z pierwszym zapytaniem
    import keyhelp
    host, port, _n = srv
    old_tok, old_id, _k = keyhelp.pair(host, port, "Telefon 25102PCBEG", "phone")
    old = {"key": old_tok, "id": old_id}
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("GET", "/v1/info", headers={"Authorization": f"Bearer {old['key']}", "X-EmuStart-Hw": hw})
    assert c.getresponse().status == 200
    assert devices._load()[old["id"]]["hw"] == hw
    # inny telefon tego samego modelu — osobna rejestracja
    other = Device("Telefon 25102PCBEG")
    _req(srv, "POST", "/v1/enroll/request", body={"mode": "new", "account": "Ola", "password": PW, "name": other.name,
                                                   "class": "phone", "pubkey": other.pem, "hw": "b" * 32})
    # ten sam telefon dołącza przez internet — prośba mówi, co zastąpi; po zatwierdzeniu stara znika
    dev = Device("Telefon 25102PCBEG")
    _req(srv, "POST", "/v1/enroll/request", body={"mode": "existing", "account": "Jezus", "password": PW, "name": dev.name,
                                                   "class": "phone", "pubkey": dev.pem, "hw": hw})
    reqs = _req(srv, "POST", "/v1/enroll/pending", admin_key, body={})[1]["requests"]
    mine = next(q for q in reqs if q["account"] == "Jezus")
    assert mine["replaces"] == ["Telefon 25102PCBEG"]
    assert next(q for q in reqs if q["account"] == "Ola")["replaces"] == []
    _req(srv, "POST", "/v1/enroll/decide", admin_key, body={"id": mine["id"], "allow": True})
    active = [d for d in devices.listing() if not d.get("revoked") and d["name"] == "Telefon 25102PCBEG"]
    assert len(active) == 1 and active[0]["hw"] == hw
    assert _req(srv, "GET", "/v1/info", old["key"])[0] == 401              # stara rejestracja nieważna


def test_account_profile_takes_the_account_name(monkeypatch):
    """Profil sprzed kont: nazwa „Gracz”, folder przemianowany na „Jezus”. Po zalogowaniu
    kontem Jezus lista pokazuje „Jezus” (wcześniej „Gracz” — wyglądało, jakby konta nie było)."""
    from emustart import library, profiles
    pid = profiles.first_id()
    with library.db() as c:
        c.execute("UPDATE profiles SET name='Gracz', nas_name='Jezus' WHERE id=?", (pid,))
        c.execute("INSERT INTO profiles(name, nas_name, color) VALUES('Jezus', 'Jezus-stary', '')")
    cfg = {"server_key": "dev:abc", "server_account": "Jezus"}
    profiles.align_account_name(cfg)
    names = {p["id"]: p["name"] for p in profiles.all_profiles()}
    assert names[pid] == "Jezus" and "Jezus (lokalny)" in names.values()
    profiles.align_account_name({})                       # bez konta — nic
    assert profiles.get(pid)["name"] == "Jezus"
