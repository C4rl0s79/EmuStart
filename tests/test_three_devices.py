"""Test „trzech urządzeń” (EmuStart 2.0): serwer + komputer + telefon.

Serwer prawdziwy (server.py + remote.py), urządzenia — klienci HTTP tak jak EmuStart na PC
(nasfs.RemoteFs) i aplikacja na Androida (zapytania z jej kluczem). Rośnie z każdym etapem.
"""

from __future__ import annotations

import http.client
import json

import pytest

from emustart import devices, nasfs, netsrc, paths, remote, server, srvadmin

MASTER = "m" * 32


class _Api:
    _cfg = {"server_token": MASTER}
    _scan: dict = {}


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    nas = paths.SERVER / "profiles"
    nas.mkdir(parents=True)
    monkeypatch.setattr(remote, "API", _Api())
    monkeypatch.setattr(remote, "NAS_ROOT", nas)
    monkeypatch.setattr(remote, "NAS_ID", nasfs.root_id(nas, create=True))
    monkeypatch.setattr(srvadmin, "ENABLED", True)
    url = server.start(0, bind="127.0.0.1")
    netsrc._info.clear()
    nasfs.reset()
    host, port = url.removeprefix("http://").split(":")
    return host, int(port), nas


def _req(srv, method, path, key="", body=None, headers=None):
    host, port, _nas = srv
    c = http.client.HTTPConnection(host, port, timeout=10)
    h = {**({"Authorization": f"Bearer {key}"} if key else {}), **(headers or {})}
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def _pair(srv, name, cls):
    """Logowanie kodem z panelu (konto „Test”, klucz urządzenia) → (token sesji, id, None)."""
    import keyhelp
    host, port, _nas = srv
    tok, did, _key = keyhelp.pair(host, port, name, cls)
    return tok, did, None


def test_login_code_gives_each_device_its_own_key(srv):
    import keyhelp
    from emustart import enroll
    pc_key, pc_id, _ = _pair(srv, "Komputer", "pc")
    ph_key, ph_id, _ = _pair(srv, "Telefon", "phone")
    assert pc_key != ph_key and pc_id != ph_id
    # kod logowania jednorazowy
    code = enroll.new_login_code("Test")["code"]
    host, port, _n = srv
    first = keyhelp.post(host, port, "/v1/enroll/request", {
        "mode": "code", "name": "A", "class": "pc", "pubkey": keyhelp.new_key()[1], "login_code": code})[1]
    again = keyhelp.post(host, port, "/v1/enroll/request", {
        "mode": "code", "name": "B", "class": "pc", "pubkey": keyhelp.new_key()[1], "login_code": code})[1]
    assert first["status"] == "approved" and not again["ok"] and "wygasł" in again["reason"]
    # oba tokeny działają, zły nie; wspólny klucz serwera — tylko z tego komputera
    for key in (pc_key, ph_key, MASTER):
        assert _req(srv, "GET", "/v1/info", key)[0] == 200
    assert _req(srv, "GET", "/v1/info", "zly-klucz")[0] == 401
    # rejestr: nazwy, klasy, wersja z nagłówka, konto, bez kluczy w pliku
    _req(srv, "GET", "/v1/info", ph_key, headers={"X-EmuStart-Version": "2.0.1"})
    devs = {d["name"]: d for d in srvadmin.devices_list(None)["devices"]}
    assert devs["Komputer"]["class"] == "pc" and devs["Telefon"]["class"] == "phone"
    assert devs["Telefon"]["version"] == "2.0.1" and devs["Telefon"]["account"] == "Test"
    assert devs["Telefon"]["admin"]                       # pierwsze konto z kodu z panelu — administrator
    devices.flush()
    text = (paths.SERVER / "devices.json").read_text(encoding="utf-8")
    assert pc_key not in text and ph_key not in text


def test_revoked_device_is_cut_off_others_keep_working(srv):
    pc_key, _pc, _ = _pair(srv, "Komputer", "pc")
    ph_key, ph_id, _ = _pair(srv, "Telefon", "phone")
    assert srvadmin.device_revoke(None, {"id": ph_id})["ok"]
    assert _req(srv, "GET", "/v1/info", ph_key)[0] == 401
    assert _req(srv, "GET", "/v1/info", pc_key)[0] == 200
    assert srvadmin.device_rename(None, {"id": _pc, "name": "PC w salonie"})["ok"]
    assert any(d["name"] == "PC w salonie" for d in devices.listing())
    # urządzenie wylogowuje się samo
    assert _req(srv, "POST", "/v1/auth/logout", pc_key, body={})[1]["ok"]
    assert _req(srv, "GET", "/v1/info", pc_key)[0] == 401


def test_wrong_login_codes_are_rate_limited(srv):
    import keyhelp
    from emustart import enroll
    host, port, _n = srv
    body = {"mode": "code", "name": "x", "class": "pc", "pubkey": keyhelp.new_key()[1]}
    for _ in range(enroll.CODE_FAILS_MAX):
        assert not keyhelp.post(host, port, "/v1/enroll/request", {**body, "login_code": "ZZZZZZZZ"})[1]["ok"]
    good = enroll.new_login_code("Ania", create=True)["code"]
    d = keyhelp.post(host, port, "/v1/enroll/request", {**body, "login_code": good})[1]
    assert not d["ok"] and "Za dużo" in d["reason"]               # nawet dobry kod — przerwa


def test_old_unbound_registrations_are_retired(tmp_path):
    """Wpisy sprzed 0.43 bez konta (parowanie kodem) — unieważnione przy wczytaniu rejestru."""
    import json as _json
    (paths.SERVER).mkdir(parents=True, exist_ok=True)
    (paths.SERVER / "devices.json").write_text(_json.dumps({
        "a1": {"name": "Stary PC", "pubkey": "pem", "role": "admin"},
        "b2": {"name": "Telefon", "pubkey": "pem", "account": "Jezus"},
        "c3": {"name": "Klucz-hasło", "key": "abc"}}), encoding="utf-8")
    devices.reset_cache()
    lst = {d["name"]: d for d in devices.listing()}
    assert lst["Stary PC"]["revoked"] and lst["Klucz-hasło"]["revoked"] and not lst["Telefon"].get("revoked")
    assert "zaloguj" in lst["Stary PC"]["revoked_reason"]


def test_save_written_by_pc_is_read_by_phone(srv, monkeypatch):
    host, port, nas = srv
    pc_key, _, _ = _pair(srv, "Komputer", "pc")
    ph_key, _, _ = _pair(srv, "Telefon", "phone")
    # komputer: EmuStart na Windows z kluczem urządzenia (nasfs przez serwer, port szyfrowany)
    from _srv import tls_server
    url, fp = tls_server(monkeypatch)
    cfg = {"server_url": url, "server_fp": fp, "server_key": pc_key, "profiles_nas": str(paths.APP / "brak-smb")}
    fs = nasfs.backend(cfg)
    assert isinstance(fs, nasfs.RemoteFs)
    # zapis nie zakłada konta — nawet urządzenie administratora (0.49: zaproszenie albo panel)
    with pytest.raises(OSError, match="404"):
        fs.write("Ania/save/retroarch/saves/Gra (USA).srm", b"zapis z PC", 1000.0)
    assert not (nas / "Ania").exists()
    (nas / "Ania").mkdir()                                 # konto założone na serwerze
    fs.write("Ania/save/retroarch/saves/Gra (USA).srm", b"zapis z PC", 1000.0)
    # telefon: aplikacja na Androida — te same zapytania co Saves.kt, własnym kluczem
    st, d = _req(srv, "GET", "/v1/nas/scan?p=Ania/save/retroarch/saves", ph_key)
    assert st == 200 and "Gra (USA).srm" in d["files"]
    st, data = _req(srv, "GET", "/v1/nas/file?p=Ania/save/retroarch/saves/Gra%20(USA).srm", ph_key)
    assert st == 200 and data == b"zapis z PC"
    # telefon zapisuje, komputer widzi nowszą wersję (kopia poprzedniej po stronie serwera)
    st, _ = _req(srv, "PUT", "/v1/nas/file?p=Ania/save/retroarch/saves/Gra%20(USA).srm&mtime=2000"
                 "&backup=Ania/_backup/t/save/retroarch/saves/Gra%20(USA).srm", ph_key, body=b"zapis z telefonu")
    assert st == 200
    assert fs.read("Ania/save/retroarch/saves/Gra (USA).srm") == b"zapis z telefonu"
    assert (nas / "Ania/_backup/t/save/retroarch/saves/Gra (USA).srm").read_bytes() == b"zapis z PC"


def test_server_layout_migration_moves_config_and_data(tmp_path, monkeypatch):
    app = tmp_path / "app"
    (app / "data" / "media" / "nes").mkdir(parents=True)
    (app / "data" / "library.sqlite").write_bytes(b"baza")
    (app / "data" / "media" / "nes" / "a.png").write_bytes(b"png")
    (app / "config.json").write_text('{"rom_roots": ["E:/gry"]}', encoding="utf-8")
    monkeypatch.setattr(paths, "APP", app)
    monkeypatch.setattr(paths, "SERVER", app / "server")
    moved = paths.use_server_layout(migrate=True)
    assert "config.json" in moved and (app / "server" / "library.sqlite").read_bytes() == b"baza"
    assert (app / "server" / "media" / "nes" / "a.png").is_file() and not (app / "data").exists()
    assert paths.CONFIG_PATH == app / "server" / "config.json" and paths.MEDIA == app / "server" / "media"
    assert paths.server_layout_active()
    # drugi start: nic do przenoszenia, nic nie ginie
    assert paths.use_server_layout(migrate=True) == []
    assert (app / "server" / "config.json").read_text(encoding="utf-8").startswith('{"rom_roots"')


def test_profiles_move_into_server_folder_after_confirmation(tmp_path, monkeypatch):
    import time as _t
    share = tmp_path / "udzial" / "emustart" / "Profiles"
    (share / "Jezus" / "save" / "pcsx2" / "memcards").mkdir(parents=True)
    (share / "Jezus" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2").write_bytes(bytes([255]) * 4096)
    (share / "Jezus" / "lock").write_text("{}")
    monkeypatch.setattr(remote, "NAS_ROOT", share)
    monkeypatch.setattr(remote, "NAS_ID", nasfs.root_id(share, create=True))
    monkeypatch.setattr(srvadmin, "_migr", {"phase": "", "done": 0, "total": 0, "files": 0, "error": ""})
    saved = {}
    monkeypatch.setattr("emustart.config.save", lambda c: saved.update(c))

    class A:
        _cfg = {}
    plan = srvadmin.profiles_migrate(A(), {})                      # bez potwierdzenia: tylko plan
    assert plan["ok"] and plan["files"] == 2 and not (paths.SERVER / "profiles").exists()
    assert srvadmin.profiles_migrate(A(), {"confirm": True})["started"]
    for _ in range(100):
        if srvadmin._migr["phase"] in ("done", "error"):
            break
        _t.sleep(0.05)
    assert srvadmin._migr["phase"] == "done", srvadmin._migr
    dst = paths.SERVER / "profiles"
    assert (dst / "Jezus/save/pcsx2/memcards/Mcd001.ps2").read_bytes() == bytes([255]) * 4096
    assert remote.NAS_ROOT == dst and saved["profiles_nas"] == str(dst)
    assert (share / "Jezus/save/pcsx2/memcards/Mcd001.ps2").is_file()       # stary folder zostaje
    assert (share / ".emustart-moved").is_file()
    assert not srvadmin.profiles_migrate(A(), {"confirm": True})["ok"]        # już przeniesione


def _qr_ready(monkeypatch):
    """Port szyfrowany i adresy serwera — do kodu QR z panelu."""
    monkeypatch.setattr(remote, "TLS", {"port": 8741, "fp": "ab" * 32})
    monkeypatch.setattr(srvadmin, "_addresses", lambda: ["192.168.1.130", "100.85.254.31"])


def test_login_qr_carries_addresses_fingerprint_and_code(monkeypatch):
    pytest.importorskip("segno")
    _qr_ready(monkeypatch)
    assert not srvadmin.login_code(None, {"account": "Nikt"})["ok"]          # nie ma konta, bez „nowe”
    r = srvadmin.login_code(None, {"account": "Test", "create": True})
    assert r["svg"].lstrip().startswith("<svg") and r["uri"].startswith("emustart://login?d=")
    login = netsrc.parse_login(r["uri"])
    assert login["ok"] and login["fp"] == "ab" * 32 and login["code"] == r["code"] and login["account"] == "Test"
    assert [u for u, _k, _c in login["endpoints"]] == ["https://192.168.1.130:8741", "https://100.85.254.31:8741"]
    # adres bez kodu QR: tylko domena (prawdziwy certyfikat), nigdy zwykłe http ani adres IP
    assert netsrc.parse_login("emustart.emucloud.org")["endpoints"] == [("https://emustart.emucloud.org", "tunnel", True)]
    for bad in ("http://100.85.254.31:8740", "100.85.254.31", "EMUSTATION", "emustart://login?d=zle"):
        assert not netsrc.parse_login(bad)["ok"], bad


def test_panel_works_without_segno(monkeypatch):
    import builtins
    from emustart import qr
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: (_ for _ in ()).throw(ImportError(n)) if n == "segno" else real(n, *a, **k))
    _qr_ready(monkeypatch)
    assert qr.svg("x") == ""                                   # bez biblioteki: sam kod, bez obrazka
    assert srvadmin.login_code(None, {"account": "Test", "create": True})["code"]


def test_login_waits_for_approval_by_device_of_the_account(srv):
    import keyhelp
    host, port, _n = srv
    ph_key, _ph, _ = _pair(srv, "Telefon", "phone")                 # konto „Test” (administrator)
    _o, other_tok = keyhelp.device("Ola", name="Telefon Oli")       # zwykłe konto
    lk, lpem = keyhelp.new_key()
    d = keyhelp.enroll_device(host, port, "Laptop", "pc", pem=lpem, code=False)
    assert d["status"] == "pending"
    # obce konto prośby nie widzi i nie zatwierdzi; urządzenie konta — tak
    assert _req(srv, "POST", "/v1/enroll/pending", other_tok, body={})[1]["requests"] == []
    q = _req(srv, "POST", "/v1/enroll/pending", ph_key, body={})[1]["requests"][0]
    assert q["name"] == "Laptop" and q["account"] == "Test"
    assert not _req(srv, "POST", "/v1/enroll/decide", other_tok, body={"id": q["id"], "allow": True})[1]["ok"]
    assert _req(srv, "POST", "/v1/enroll/decide", ph_key, body={"id": q["id"], "allow": True, "games": True})[1]["ok"]
    got = keyhelp.post(host, port, "/v1/enroll/poll", {"rid": d["rid"]})[1]
    assert got["status"] == "approved" and "endpoints" in got      # adresy serwera po zatwierdzeniu
    assert _req(srv, "GET", "/v1/info", keyhelp.login(host, port, got["device"], lk))[0] == 200
    assert {d["name"]: d for d in devices.listing()}["Laptop"]["approved_by"] == "Telefon"


def test_denied_request_and_panel_approval(srv):
    import keyhelp
    host, port, _n = srv
    a = keyhelp.enroll_device(host, port, "Obcy", "pc", account="Obcy", pem=keyhelp.new_key()[1], code=False)
    req = srvadmin.devices_list(None)["enroll"][0]
    assert req["name"] == "Obcy" and req["mode"] == "existing"
    assert srvadmin.enroll_decide(None, {"id": req["id"], "allow": False})["status"] == "denied"
    assert keyhelp.post(host, port, "/v1/enroll/poll", {"rid": a["rid"]})[1]["status"] == "denied"
    bk, bpem = keyhelp.new_key()
    b = keyhelp.enroll_device(host, port, "Mój PC", "pc", account="Ania", pem=bpem, code=False)
    req = srvadmin.devices_list(None)["enroll"][0]
    assert srvadmin.enroll_decide(None, {"id": req["id"], "allow": True})["ok"]
    did = keyhelp.post(host, port, "/v1/enroll/poll", {"rid": b["rid"]})[1]["device"]
    assert _req(srv, "GET", "/v1/info", keyhelp.login(host, port, did, bk))[0] == 200
    # zły identyfikator prośby nic nie zdradza
    assert keyhelp.post(host, port, "/v1/enroll/poll", {"rid": "zgadywanie"})[1]["status"] == "expired"


def test_pc_logs_in_with_qr_link_and_logs_out(srv, monkeypatch):
    """Komputer: link z kodu QR panelu (adresy + odcisk + kod) — szyfrowane, przypięte,
    bez czekania; wylogowanie unieważnia urządzenie na serwerze."""
    import keyhelp
    from _srv import tls_server
    from emustart import api as api_mod
    url, fp = tls_server(monkeypatch)
    port = int(url.rsplit(":", 1)[1])
    monkeypatch.setattr(srvadmin, "_addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(remote, "TLS", {"port": port, "fp": fp})
    saved = {}
    monkeypatch.setattr("emustart.config.save", lambda c: saved.update(c))
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._profile = {}, 0
    monkeypatch.setattr(a, "_adopt_account", lambda acc: None, raising=False)
    link = srvadmin.login_code(None, {"account": "Ania", "create": True})["uri"]
    assert not a.server_enroll("100.85.254.31", "code", code="ABCDEFGH")["ok"]          # IP bez kodu QR
    assert "zaproszenia" in a.server_enroll(link, "new", "Ania", keyhelp.PW)["reason"]  # nowe konto — nie z aplikacji
    r = a.server_enroll(link, "code")
    assert r["ok"] and r["status"] == "approved" and r["account"] == "Ania", r
    assert saved["server_url"] == url and saved["server_key"].startswith("dev:") and saved["server_keyname"]
    assert netsrc.server_fp(a._cfg) == fp
    assert netsrc.info(a._cfg, fresh=True)["ok"]
    did = saved["server_key"][4:]
    assert a.server_logout()["ok"] and not a._cfg.get("server_key")
    assert {d["id"]: d for d in devices.listing()}[did]["revoked"]


def test_game_list_in_pages_and_gzip(srv, monkeypatch):
    """Duży system (FBA): lista stronami z jednego liczenia, duże odpowiedzi gzip."""
    import gzip
    rows = [{"id": i, "name": f"gra{i:05d}", "title": f"Gra {i}", "tags": "", "size": 1} for i in range(2500)]
    calls = []
    monkeypatch.setattr(remote.API, "list_games", lambda es: (calls.append(es), rows)[1], raising=False)
    remote._games_cache.clear()
    tok, _did, _ = _pair(srv, "Telefon", "phone")
    st, d = _req(srv, "GET", "/v1/systems/fba/games?offset=0&limit=100", tok)
    assert st == 200 and d["total"] == 2500 and [g["id"] for g in d["games"]] == list(range(100))
    st, d = _req(srv, "GET", "/v1/systems/fba/games?offset=2400&limit=1000", tok)
    assert [g["id"] for g in d["games"]][-1] == 2499 and len(d["games"]) == 100
    assert calls == ["fba"]                                   # strony z jednego liczenia
    st, full = _req(srv, "GET", "/v1/systems/fba/games", tok)
    assert len(full) == 2500                                   # dawni klienci — cała lista
    host, port, _n = srv
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("GET", "/v1/systems/fba/games", headers={"Authorization": f"Bearer {tok}", "Accept-Encoding": "gzip"})
    r = c.getresponse()
    raw = r.read()
    assert r.getheader("Content-Encoding") == "gzip" and len(json.loads(gzip.decompress(raw))) == 2500


def test_admin_device_edits_art_and_look_for_everyone(srv, monkeypatch, tmp_path):
    """Administrator zmienia grafiki, opisy i wygląd systemu z PC albo telefonu — zapis na
    serwerze (źródło prawdy), wersja systemu rośnie, inne urządzenia pobiorą zmianę.
    Zwykłe urządzenie — 403."""
    from emustart import accounts, art_sources, libsync, library, metadata, scanner, srvapi
    (tmp_path / "roms" / "snes").mkdir(parents=True)
    (tmp_path / "roms" / "snes" / "Gra (USA).sfc").write_bytes(b"x")
    scanner.scan_system("snes", tmp_path / "roms" / "snes")
    api = srvapi.ServerApi(start_background=False)
    monkeypatch.setattr(remote, "API", api)
    monkeypatch.setattr("emustart.config.save", lambda c: None)
    tok, _did, _ = _pair(srv, "Telefon", "phone")
    accounts.set_admin("Test", False)
    gid = library.db().execute("SELECT id FROM games").fetchone()["id"]
    assert _req(srv, "GET", "/v1/info", tok)[1]["admin"] is False
    assert _req(srv, "PUT", f"/v1/admin/games/{gid}/meta", tok, body={"edits": {"description": "x"}})[0] == 403
    accounts.set_admin("Test", True)
    assert _req(srv, "GET", "/v1/info", tok)[1]["admin"] is True
    v1 = libsync.server_systems()[0]["scanned"]
    st, r = _req(srv, "PUT", f"/v1/admin/games/{gid}/meta", tok, body={"edits": {"description": "Opis admina"}})
    assert st == 200 and r["ok"] and metadata.get(gid)["description"] == "Opis admina"
    assert libsync.server_systems()[0]["scanned"] > v1                     # urządzenia pobiorą od nowa
    monkeypatch.setattr(art_sources, "candidates", lambda *a, **k: [{"url": "https://x/b.png", "thumb": "https://x/t.png"}])
    st, r = _req(srv, "GET", f"/v1/admin/games/{gid}/art?kind=box&q=", tok)
    assert st == 200 and r["list"][0]["url"] == "https://x/b.png"
    png = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 64
    monkeypatch.setattr(art_sources, "fetch", lambda url, **k: png)
    st, r = _req(srv, "PUT", f"/v1/admin/games/{gid}/art?kind=box&url=https%3A%2F%2Fx%2Fb.png", tok, body=b"")
    assert st == 200 and r["ok"] and libsync.server_games("snes")[0]["art_m"].get("box")
    assert _req(srv, "DELETE", f"/v1/admin/games/{gid}/art?kind=box", tok)[1]["ok"]
    st, r = _req(srv, "PUT", "/v1/admin/systems/snes", tok, body={"name": "Moje SNES"})
    assert st == 200 and r["ok"] and libsync.server_systems()[0]["display"] == "Moje SNES"
    assert _req(srv, "PUT", "/v1/admin/nieznane", tok, body={})[0] == 404
