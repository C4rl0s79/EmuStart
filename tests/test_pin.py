"""PIN profilu: nazwy unikalne bez względu na wielkość liter, PIN raz na urządzeniu,
stare profile dostają PIN przy pierwszym użyciu, serwer wydaje pliki profilu z PIN-em
tylko z tokenem."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from emustart import nasfs, pinauth, profiles

from test_core import _write, env  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _fast_scrypt(monkeypatch):
    monkeypatch.setattr(pinauth, "N", 2 ** 8)          # testy: szybki scrypt
    pinauth._fails.clear()


def test_pin_hash_and_token():
    rec = pinauth.make("1234")
    assert pinauth.check(rec, "1234") and not pinauth.check(rec, "1235")
    assert "1234" not in json.dumps(rec)
    t = pinauth.token("sekret", "Jan", rec)
    assert pinauth.token_ok("sekret", "jan", rec, t)                      # nazwa bez wielkości liter
    assert not pinauth.token_ok("sekret", "Jan", pinauth.make("1234"), t)  # nowy PIN = nowe tokeny
    with pytest.raises(ValueError):
        pinauth.make("12a4")


def test_wrong_pins_slow_down():
    for _ in range(pinauth.FREE_TRIES):
        pinauth.failed("Jan")
    assert pinauth.wait_left("JAN") > 0
    pinauth.succeeded("jan")
    assert pinauth.wait_left("Jan") == 0


@pytest.fixture(params=["nas", "serwer"])
def penv(env, monkeypatch, request, tmp_path):
    cfg, roms, tmp = env
    monkeypatch.setattr(profiles, "LOCAL", tmp / "profiles")
    cfg["profiles_nas"] = str(tmp / "nas")
    (tmp / "nas").mkdir()
    if request.param == "serwer":
        from _srv import use_server
        use_server(cfg, tmp / "nas", monkeypatch)
    profiles._pin_cache.clear()
    yield cfg, tmp, request.param
    nasfs.TOKENS.clear()


def test_create_unique_name_and_join(penv):
    cfg, tmp, mode = penv
    jan = profiles.create("Jan", cfg=cfg, pin="1234")
    assert (tmp / "nas" / "Jan" / pinauth.AUTH).is_file()
    assert profiles.pin_state(cfg, jan["id"], fresh=True) == "ok"
    with pytest.raises(profiles.ProfileExists) as ex:
        profiles.create("JAN", cfg=cfg, pin="9999")              # zajęta bez względu na wielkość liter
    assert ex.value.name == "Jan"
    # drugie urządzenie: ta sama nazwa — tylko z PIN-em tego profilu
    profiles.pin_logout(jan["id"])
    assert profiles.pin_state(cfg, jan["id"], fresh=True) == "need_pin"
    prof, why = profiles.pin_join(cfg, "jan", "0000")
    assert prof is None and why
    prof, why = profiles.pin_join(cfg, "jan", "1234")
    assert prof and prof["id"] == jan["id"] and not why
    assert profiles.pin_state(cfg, jan["id"], fresh=True) == "ok"


def test_old_profile_gets_pin_on_first_use(penv):
    cfg, tmp, mode = penv
    _write(tmp / "nas" / "Jezus" / "emustart.json", b"{}")       # profil sprzed PIN-ów
    profiles.import_from_nas(cfg)
    pid = next(p["id"] for p in profiles.all_profiles() if p["nas_name"] == "Jezus")
    assert profiles.pin_state(cfg, pid, fresh=True) == "set_pin"
    assert profiles.pin_unlock(cfg, pid, "1234")[0] is False
    assert profiles.pin_set(cfg, pid, "4321") == (True, "")
    assert profiles.pin_state(cfg, pid, fresh=True) == "ok"
    # zmiana PIN-u wymaga dotychczasowego
    assert profiles.pin_set(cfg, pid, "5555", old="0000")[0] is False
    assert profiles.pin_set(cfg, pid, "5555", old="4321") == (True, "")


def test_server_blocks_profile_files_without_token(env, monkeypatch, tmp_path):
    cfg, roms, tmp = env
    from _srv import KEY, use_server
    nas = tmp / "nas"
    cfg["profiles_nas"] = str(nas)
    use_server(cfg, nas, monkeypatch)
    base = "http://" + cfg["server_url"]
    h = {"Authorization": f"Bearer {KEY}"}

    def req(path, method="GET", hdr=None):
        r = urllib.request.Request(base + path, method=method, data=b"" if method == "PUT" else None,
                                   headers={**h, **(hdr or {})})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as ex:
            return ex.code, json.loads(ex.read() or b"{}")

    st, r = req("/v1/profiles/create?name=Jan&pin=1234", "PUT")
    assert st == 200 and r["token"]
    tok = r["token"]
    assert req("/v1/profiles/create?name=jan&pin=1111", "PUT")[0] == 409
    assert req("/v1/profiles")[1]["profiles"] == [{"name": "Jan", "pin": True}]
    # pliki profilu: bez tokenu 403, z tokenem OK; skrót PIN-u nigdy
    assert req("/v1/nas/file?p=Jan/emustart.json")[0] == 403
    assert req("/v1/nas/scan?p=Jan")[0] == 403
    assert req("/v1/nas/file?p=Jan/emustart.json", hdr={"X-Profile-Token": tok})[0] == 200
    assert req("/v1/nas/file?p=Jan/.auth.json", hdr={"X-Profile-Token": tok})[0] == 403
    assert ".auth.json" not in req("/v1/nas/scan?p=Jan", hdr={"X-Profile-Token": tok})[1]["files"]
    assert req("/v1/nas/scan?p=")[0] == 403                          # cały folder profili naraz
    assert req("/v1/savemgr/catalog?profile=Jan&es=a&name=b")[0] == 403
    # logowanie: zły PIN 403, dobry — token
    assert req("/v1/profiles/login?name=JAN&pin=0000", "PUT")[0] == 403
    st, r = req("/v1/profiles/login?name=JAN&pin=1234", "PUT")
    assert st == 200 and r["token"] == tok and r["name"] == "Jan"
    # zapis do cudzego profilu przez kopię zapasową — też pilnowany
    _write(nas / "Ola" / "emustart.json", b"{}")
    st, _r = req("/v1/nas/file?p=Ola/x.txt&backup=Jan/_backup/x", "PUT")
    assert st == 403
