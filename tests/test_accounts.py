"""Hasła kont na serwerze (emustart/accounts.py) i PIN tylko na urządzeniu (0.40).

Hasło: co najmniej 10 znaków, wielka litera, cyfra, znak specjalny; na serwerze tylko skrót
scrypt (poza folderem profili); inny użytkownik na urządzeniu — token konta po haśle.
PIN: skrót w bazie tego komputera, serwer go nie zna."""

from __future__ import annotations

import json

import pytest

from emustart import accounts, nasfs, paths, pinauth, profiles

from test_core import _write, env  # noqa: F401  (fixture)

PW = "Haslo-testowe1"


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pinauth, "N", 2 ** 8)          # testy: szybki scrypt
    pinauth._fails.clear()


def test_password_policy():
    assert accounts.policy(PW) == ""
    assert "10 znaków" in accounts.policy("Krotkie1!")
    assert "wielka litera" in accounts.policy("haslo-testowe1")
    assert "cyfra" in accounts.policy("Haslo-testowe")
    assert "znak specjalny" in accounts.policy("HasloTestowe1")
    assert accounts.policy("Zażółć1234_") == ""                      # polskie litery, podkreślnik
    with pytest.raises(ValueError):
        accounts.make("slabe")


def test_hash_storage_and_tokens():
    assert accounts.set_password("Jan", PW) == ""
    raw = (paths.SERVER / "accounts.json").read_text(encoding="utf-8")
    assert PW not in raw and "scrypt" in raw                          # tylko skrót
    assert accounts.verify("jan", PW)[0] and not accounts.verify("Jan", "Zle-haslo-12")[0]
    t = accounts.token("sekret", "Jan")
    assert accounts.token_ok("sekret", "JAN", t)
    accounts.set_password("Jan", PW + "x")                           # zmiana hasła — stare tokeny nieważne
    assert not accounts.token_ok("sekret", "Jan", t)
    assert accounts.token("sekret", "Nikt") == "" and not accounts.token_ok("sekret", "Nikt", "")


def test_wrong_passwords_slow_down_per_account_and_address():
    accounts.set_password("Jan", PW)
    for _ in range(accounts.FREE_TRIES):
        assert accounts.verify("Jan", "Zle-haslo-12", "1.2.3.4")[1] == "Złe hasło."
    ok, why, wait = accounts.verify("Jan", PW, "1.2.3.4")
    assert not ok and wait > 0 and "Za dużo" in why                  # nawet dobre — przerwa
    accounts.reset()
    accounts.set_password("Ola", PW)
    for i in range(accounts.IP_MAX):
        accounts._failed(f"konto{i}", "5.6.7.8")                      # zgadywanie po wielu kontach
    assert accounts.wait_left("Ola", "5.6.7.8") > 0 and accounts.wait_left("Ola", "9.9.9.9") == 0


@pytest.fixture(params=["serwer"])
def penv(env, monkeypatch, request, tmp_path):
    cfg, roms, tmp = env
    monkeypatch.setattr(profiles, "LOCAL", tmp / "profiles")
    cfg["profiles_nas"] = str(tmp / "nas")
    (tmp / "nas").mkdir()
    if request.param == "serwer":
        from _srv import use_server
        use_server(cfg, tmp / "nas", monkeypatch)
    profiles._pin_cache.clear()
    profiles._session_unlocked.clear()
    yield cfg, tmp, request.param
    nasfs.TOKENS.clear()


def _account(tmp, name):
    """Konto założone na serwerze (zaproszenie / panel) z hasłem."""
    _write(tmp / "nas" / name / "emustart.json", b"{}")
    accounts.set_password(name, PW)


def test_new_account_not_from_the_device_and_join(penv):
    cfg, tmp, mode = penv
    # 0.49: z serwerem nowy profil nie zakłada konta — zaproszenie albo panel
    with pytest.raises(ValueError, match="zaproszenia"):
        profiles.create("Jan", cfg=cfg)
    assert not (tmp / "nas" / "Jan").exists() and not accounts.has_password("Jan")
    _account(tmp, "Jan")
    with pytest.raises(profiles.ProfileExists) as ex:
        profiles.create("JAN", cfg=cfg)                                # istniejące konto — jego hasło
    assert ex.value.name == "Jan"
    jan, why = profiles.account_join(cfg, "jan", PW)
    assert jan and not why and profiles.pin_state(cfg, jan["id"], fresh=True) == "ok"
    assert not (tmp / "nas" / "Jan" / pinauth.AUTH).exists()             # nic o haśle w folderze profilu
    # drugie urządzenie / wylogowanie: konto innego użytkownika — tylko z hasłem
    profiles.account_logout(jan["id"])
    assert profiles.pin_state(cfg, jan["id"], fresh=True) == "need_password"
    prof, why = profiles.account_join(cfg, "jan", "Zle-haslo-12")
    assert prof is None and why == "Złe hasło."
    prof, why = profiles.account_join(cfg, "jan", PW)
    assert prof and prof["id"] == jan["id"] and not why
    assert profiles.pin_state(cfg, jan["id"], fresh=True) == "ok"


def test_old_account_gets_password_on_first_use(penv):
    cfg, tmp, mode = penv
    if mode == "nas":
        pytest.skip("hasła kont są na serwerze")
    _write(tmp / "nas" / "Jezus" / "emustart.json", b"{}")             # konto sprzed haseł
    _write(tmp / "nas" / "Jezus" / pinauth.AUTH, json.dumps(pinauth.make("1234")).encode())   # dawny PIN
    profiles.import_from_nas(cfg)
    pid = next(p["id"] for p in profiles.all_profiles() if p["nas_name"] == "Jezus")
    assert profiles.pin_state(cfg, pid, fresh=True) == "set_password"     # dawny PIN serwera nie działa
    assert profiles.account_login(cfg, pid, "1234")[0] is False
    assert profiles.account_password(cfg, pid, "slabe")[0] is False
    assert profiles.account_password(cfg, pid, PW) == (True, "")
    assert profiles.pin_state(cfg, pid, fresh=True) == "ok"
    # zmiana hasła wymaga dotychczasowego
    assert profiles.account_password(cfg, pid, PW + "2", old="Zle-haslo-12")[0] is False
    assert profiles.account_password(cfg, pid, PW + "2", old=PW) == (True, "")


def test_local_pin_only_on_this_device(penv):
    cfg, tmp, mode = penv
    _account(tmp, "Jan")
    jan, _why = profiles.account_join(cfg, "Jan", PW)
    assert profiles.local_pin_set(jan["id"], "12a4")[0] is False
    assert profiles.local_pin_set(jan["id"], "1234") == (True, "")
    raw = profiles.get(jan["id"])["pin_hash"]
    # PIN nie leży jawnie w żadnym polu (podciąg „1234” może się trafić w losowym skrócie hex)
    assert raw and "1234" not in json.loads(raw).values()
    assert not any(f.name == pinauth.AUTH for f in (tmp / "nas").rglob("*"))   # nic na serwerze / NAS-ie
    assert profiles.pin_state(cfg, jan["id"]) == "ok"                    # ustawiony tu — odblokowany
    profiles.local_lock(jan["id"])
    assert profiles.pin_state(cfg, jan["id"]) == "need_pin"
    assert profiles.local_pin_check(jan["id"], "0000") == (False, "Zły PIN.")
    assert profiles.local_pin_check(jan["id"], "1234") == (True, "")
    assert profiles.pin_state(cfg, jan["id"]) == "ok"
    assert profiles.local_pin_set(jan["id"], "5555", old="0000")[0] is False
    assert profiles.local_pin_clear(jan["id"], "1234") == (True, "")
    profiles.local_lock(jan["id"])
    assert profiles.pin_state(cfg, jan["id"]) == "ok"


def test_server_account_files_need_password_token(env, monkeypatch, tmp_path):
    cfg, roms, tmp = env
    from _srv import KEY, use_server
    nas = tmp / "nas"
    cfg["profiles_nas"] = str(nas)
    use_server(cfg, nas, monkeypatch)
    from emustart import netsrc
    host, port = cfg["server_url"].removeprefix("https://").split(":")
    h = {"Authorization": f"Bearer {KEY}"}

    def req(path, method="GET", hdr=None, body=None):
        data = json.dumps(body).encode() if body is not None else (b"" if method == "PUT" else None)
        c = netsrc.PinnedHTTPS(host, int(port), cfg["server_fp"])      # szyfrowane, przypięte (0.43)
        c.request(method, path, body=data, headers={**h, "Content-Type": "application/json", **(hdr or {})})
        r = c.getresponse()
        out = r.status, json.loads(r.read() or b"{}")
        c.close()
        return out

    # zakładanie kont z urządzenia nie istnieje (0.49)
    assert req("/v1/profiles/create", "PUT", body={"name": "Jan", "password": PW})[0] == 404
    _write(nas / "Jan" / "emustart.json", b"{}")
    accounts.set_password("Jan", PW)
    st, r = req("/v1/profiles/login", "PUT", body={"name": "Jan", "password": PW})
    assert st == 200 and r["token"]
    tok = r["token"]
    assert req("/v1/profiles")[1]["profiles"] == [{"name": "Jan", "password": True, "pin": True}]
    # pliki konta: bez tokenu 403, z tokenem OK
    assert req("/v1/nas/file?p=Jan/emustart.json")[0] == 403
    assert req("/v1/nas/scan?p=Jan")[0] == 403
    assert req("/v1/nas/file?p=Jan/emustart.json", hdr={"X-Profile-Token": tok})[0] == 200
    assert req("/v1/nas/scan?p=")[0] == 403                          # cały folder profili naraz
    assert req("/v1/savemgr/catalog?profile=Jan&es=a&name=b")[0] == 403
    # logowanie: złe hasło 403, dobre — token; hasło w adresie się nie liczy
    assert req("/v1/profiles/login", "PUT", body={"name": "JAN", "password": "Zle-haslo-12"})[0] == 403
    assert req(f"/v1/profiles/login?name=Jan&password={PW}", "PUT")[0] == 404
    st, r = req("/v1/profiles/login", "PUT", body={"name": "JAN", "password": PW})
    assert st == 200 and r["token"] == tok and r["name"] == "Jan"
    # zapis do cudzego konta przez kopię zapasową — też pilnowany
    _write(nas / "Ola" / "emustart.json", b"{}")
    accounts.set_password("Ola", PW)
    st, _r = req("/v1/nas/file?p=Ola/x.txt&backup=Jan/_backup/x", "PUT")
    assert st == 403
    # skrót hasła — poza folderem profili
    assert not list(nas.rglob("accounts.json")) and (paths.SERVER / "accounts.json").is_file()


def _keyed_server(monkeypatch):
    """Serwer z folderem profili w katalogu serwera (jak test_three_devices)."""
    from emustart import remote, server, srvadmin
    from test_three_devices import _Api
    nas = paths.SERVER / "profiles"
    nas.mkdir(parents=True)
    monkeypatch.setattr(remote, "API", _Api())
    monkeypatch.setattr(remote, "NAS_ROOT", nas)
    monkeypatch.setattr(remote, "NAS_ID", nasfs.root_id(nas, create=True))
    monkeypatch.setattr(srvadmin, "ENABLED", True)
    url = server.start(0, bind="127.0.0.1")
    host, port = url.removeprefix("http://").split(":")
    return host, int(port), nas


def test_account_device_other_user_needs_their_password(monkeypatch):
    """Urządzenie konta „Jezus”: konto „Ola” dopiero po haśle Oli (token konta)."""
    import keyhelp
    from emustart import enroll
    from test_three_devices import _req
    srv = _keyed_server(monkeypatch)
    nas = srv[2]
    for n in ("Jezus", "Ola"):
        _write(nas / n / "emustart.json", b"{}")
    accounts.set_password("Ola", PW)
    _did, tok = keyhelp.device("Jezus", name="Telefon")
    enroll._tokens["sesja"] = enroll._tokens.pop(tok)
    assert [p["name"] for p in _req(srv, "GET", "/v1/profiles", "sesja")[1]["profiles"]] == ["Jezus"]
    assert _req(srv, "GET", "/v1/nas/file?p=Jezus/emustart.json", "sesja")[0] == 200
    st, r = _req(srv, "GET", "/v1/nas/file?p=Ola/emustart.json", "sesja")
    assert st == 403 and r["pin"]
    # zakładanie kont z urządzenia — nie istnieje (0.49)
    assert _req(srv, "PUT", "/v1/profiles/create", "sesja", body={"name": "Nowy", "password": PW})[0] == 404
    # hasło Oli → token konta → dostęp
    st, r = _req(srv, "PUT", "/v1/profiles/login", "sesja", body={"name": "ola", "password": PW})
    assert st == 200 and r["token"]
    assert _req(srv, "GET", "/v1/nas/file?p=Ola/emustart.json", "sesja",
                headers={"X-Profile-Token": r["token"]})[0] == 200
    # urządzenie konta ustawia hasło swojego konta (bez hasła); cudzego bez dotychczasowego — nie
    st, r = _req(srv, "PUT", "/v1/profiles/password", "sesja", body={"name": "Jezus", "password": PW})
    assert st == 200 and accounts.has_password("Jezus")
    st, r = _req(srv, "PUT", "/v1/profiles/password", "sesja", body={"name": "Ola", "password": PW + "x", "old": ""})
    assert st == 403 and accounts.verify("Ola", PW)[0]


def test_enroll_existing_needs_the_account_password(monkeypatch):
    from emustart import enroll
    from test_enroll import Device
    srv = _keyed_server(monkeypatch)
    _write(srv[2] / "Ania" / "emustart.json", b"{}")
    # konto bez hasła (np. z zaproszenia) — „Mam konto” odmawia, zostaje kod logowania
    assert "nie ma hasła" in Device("A").ask(srv, "existing", "Ania", password=PW)["reason"]
    accounts.set_password("Ania", PW)
    # „mam konto”: złe hasło — bez prośby; dobre — prośba czeka na zatwierdzenie
    assert Device("C").ask(srv, "existing", "ania", password="Zle-haslo-12")["reason"] == "Złe hasło."
    r = Device("D").ask(srv, "existing", "ania", password=PW)
    assert r["ok"] and enroll.poll(r["rid"])["status"] == "pending"
    assert PW not in (paths.SERVER / "enroll.json").read_text(encoding="utf-8")


def test_panel_lists_accounts_and_resets_password(env, monkeypatch):
    cfg, roms, tmp = env
    from _srv import use_server
    from emustart import srvadmin
    use_server(cfg, tmp / "nas", monkeypatch)
    _write(tmp / "nas" / "Jezus" / "emustart.json", b"{}")
    accounts.set_password("Ola", PW)
    lst = {a["name"]: a for a in srvadmin.accounts_list(None)["accounts"]}
    assert not lst["Jezus"]["password"] and lst["Ola"]["password"] and "hash" not in lst["Ola"]
    assert not srvadmin.account_password(None, {"name": "jezus", "password": "slabe"})["ok"]
    assert srvadmin.account_password(None, {"name": "jezus", "password": PW})["ok"]
    assert accounts.verify("Jezus", PW)[0]
    assert not srvadmin.account_password(None, {"name": "Nikt", "password": PW})["ok"]


def test_logged_device_cannot_create_accounts(monkeypatch):
    """0.49: zalogowane urządzenie nie zakłada nowych kont (wcześniej prośba „Inny użytkownik →
    nowa nazwa” do administratora) — nowe konto tylko z zaproszenia albo w panelu."""
    import keyhelp
    from test_three_devices import _req
    srv = _keyed_server(monkeypatch)
    _write(srv[2] / "Jezus" / "emustart.json", b"{}")
    _a, admin = keyhelp.device("Admin", admin=True, name="PC admina")
    for path in ("/v1/enroll/account", "/v1/enroll/account_poll"):
        assert _req(srv, "POST", path, admin, body={"account": "Ola", "password": PW})[0] == 404
    assert accounts.record("Ola") is None and not (srv[2] / "Ola").exists()


def test_account_device_lists_exactly_what_the_server_gives(penv):
    """PC zalogowany kontem: lista profili = konto + konta zalogowane hasłem (jak telefon);
    stare lokalne profile zostają w bazie, ale nie na liście."""
    cfg, tmp, mode = penv
    if mode == "nas":
        pytest.skip("konta są na serwerze")
    from emustart import library
    profiles.all_profiles()                                          # migracja kolumn jak w programie
    with library.db() as c:
        c.execute("INSERT INTO profiles(name, nas_name, color) VALUES('Ania', 'Ania', '')")
        c.execute("INSERT INTO profiles(name, nas_name, color) VALUES('Jezus', 'Jezus', '')")
    acc = {**cfg, "server_key": "dev:abc", "server_account": "Jezus"}
    names = [p["name"] for p in profiles.visible(acc, profiles.all_profiles())]
    assert names == ["Jezus"]
    prof = profiles.account_profile("Ola", "token-oli")              # inny użytkownik po haśle
    assert [p["name"] for p in profiles.visible(acc, profiles.all_profiles())] == ["Jezus", "Ola"]
    assert prof["name"] == "Ola"
    assert len(profiles.visible(cfg, profiles.all_profiles())) == len(profiles.all_profiles())   # bez konta — wszystkie


def test_admin_account_device_reaches_its_own_account_without_token(monkeypatch):
    """Konto administratora z hasłem: jego urządzenia czytają i zapisują własne konto bez tokenu
    (0.43–0.44: 403 „konto wymaga hasła” — menedżer zapisów i synchronizacja zapisów);
    cudze konto z hasłem nadal tylko z tokenem."""
    import keyhelp
    from test_three_devices import _req
    srv = _keyed_server(monkeypatch)
    for n in ("Jezus", "Ola"):
        _write(srv[2] / n / "emustart.json", b"{}")
        accounts.set_password(n, PW)
    _d, tok = keyhelp.device("Jezus", admin=True, name="Telefon")
    assert _req(srv, "GET", "/v1/nas/file?p=Jezus/emustart.json", tok)[0] == 200
    assert _req(srv, "PUT", "/v1/nas/file?p=Jezus/save/x.srm&mtime=1", tok, body=b"zapis")[0] == 200
    assert _req(srv, "GET", "/v1/savemgr/catalog?profile=Jezus&es=ps2&name=Gra", tok)[0] == 200
    assert _req(srv, "GET", "/v1/nas/file?p=Ola/emustart.json", tok)[0] == 403
