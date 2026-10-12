"""Test „trzech urządzeń” (EmuStart 2.0): serwer + komputer + telefon.

Serwer prawdziwy (server.py + remote.py), urządzenia — klienci HTTP tak jak EmuStart na PC
(nasfs.RemoteFs) i aplikacja na Androida (zapytania z jej kluczem). Rośnie z każdym etapem.
"""

from __future__ import annotations

import http.client
import json

import pytest

from emustart import devices, nasfs, netsrc, paths, profiles, remote, server, srvadmin

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
    code = srvadmin.pair_code(None)
    assert code["uri"].startswith("emustart://pair?") and code["code"] in code["uri"]
    st, d = _req(srv, "POST", "/v1/pair", body={"code": code["pretty"], "name": name, "class": cls,
                                               "version": "2.0-test"})
    assert st == 200 and d["ok"], d
    return d["key"], d["id"], code["code"]


def test_pairing_gives_each_device_its_own_key(srv):
    pc_key, pc_id, used = _pair(srv, "Komputer", "pc")
    ph_key, ph_id, _ = _pair(srv, "Telefon", "phone")
    assert pc_key != ph_key and pc_id != ph_id
    # kod jednorazowy
    st, d = _req(srv, "POST", "/v1/pair", body={"code": used, "name": "x", "class": "pc"})
    assert st == 403 and not d["ok"]
    # oba klucze działają, zły nie
    for key in (pc_key, ph_key, MASTER):
        assert _req(srv, "GET", "/v1/info", key)[0] == 200
    assert _req(srv, "GET", "/v1/info", "zly-klucz")[0] == 401
    # rejestr: nazwy, klasy, wersja z nagłówka, bez kluczy w pliku
    _req(srv, "GET", "/v1/info", ph_key, headers={"X-EmuStart-Version": "2.0.1"})
    devs = {d["name"]: d for d in srvadmin.devices_list(None)["devices"]}
    assert devs["Komputer"]["class"] == "pc" and devs["Telefon"]["class"] == "phone"
    assert devs["Telefon"]["version"] == "2.0.1" and "key" not in devs["Telefon"]
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


def test_wrong_codes_are_rate_limited(srv):
    for _ in range(devices.MAX_FAILS):
        assert _req(srv, "POST", "/v1/pair", body={"code": "ZZZZZZZZ", "name": "x", "class": "pc"})[0] == 403
    good = srvadmin.pair_code(None)["code"]
    st, d = _req(srv, "POST", "/v1/pair", body={"code": good, "name": "x", "class": "pc"})
    assert st == 403 and "Za dużo" in d["reason"]                 # nawet dobry kod — przerwa


def test_save_written_by_pc_is_read_by_phone(srv, monkeypatch):
    host, port, nas = srv
    pc_key, _, _ = _pair(srv, "Komputer", "pc")
    ph_key, _, _ = _pair(srv, "Telefon", "phone")
    # komputer: EmuStart na Windows z kluczem urządzenia (nasfs przez serwer)
    cfg = {"server_url": f"{host}:{port}", "server_key": pc_key, "profiles_nas": str(paths.APP / "brak-smb")}
    fs = nasfs.backend(cfg)
    assert isinstance(fs, nasfs.RemoteFs)
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
