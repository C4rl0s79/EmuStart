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


def test_pairing_qr_is_svg_with_the_pairing_link():
    pytest.importorskip("segno")
    from emustart import qr
    code = srvadmin.pair_code(None)
    assert code["svg"].lstrip().startswith("<svg") and "path" in code["svg"]
    assert qr.svg("emustart://pair?u=http://100.85.254.31:8740&c=ABCD2345").count("<svg") == 1


def test_panel_works_without_segno(monkeypatch):
    import builtins
    from emustart import qr
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: (_ for _ in ()).throw(ImportError(n)) if n == "segno" else real(n, *a, **k))
    assert qr.svg("x") == ""                                   # bez biblioteki: sam kod, bez obrazka
    assert srvadmin.pair_code(None)["code"]


def test_new_pc_joins_after_phone_approves(srv):
    ph_key, _ph, _ = _pair(srv, "Telefon", "phone")
    # nowy komputer: prośba o dołączenie (bez klucza) — kod i QR na jego ekranie
    st, req = _req(srv, "POST", "/v1/pair/request", body={"name": "Laptop", "class": "pc", "version": "2.0"})
    assert st == 200 and req["ok"] and len(req["code"]) == 8
    assert _req(srv, "POST", "/v1/pair/poll", body={"rid": req["rid"]})[1]["status"] == "pending"
    # zatwierdzać może tylko sparowane urządzenie
    assert _req(srv, "POST", "/v1/pair/approve", body={"code": req["code"], "allow": True})[0] == 401
    # telefon: podgląd (nazwa nowego urządzenia), potem zatwierdzenie
    st, prev = _req(srv, "POST", "/v1/pair/approve", ph_key, body={"code": req["pretty"]})
    assert prev["ok"] and prev["request"]["name"] == "Laptop" and "rid" not in prev["request"]
    st, ok = _req(srv, "POST", "/v1/pair/approve", ph_key, body={"code": req["code"], "allow": True})
    assert ok["ok"] and ok["status"] == "approved"
    # komputer odbiera swój klucz — tylko raz
    st, got = _req(srv, "POST", "/v1/pair/poll", body={"rid": req["rid"]})
    assert got["status"] == "approved" and _req(srv, "GET", "/v1/info", got["key"])[0] == 200
    assert _req(srv, "POST", "/v1/pair/poll", body={"rid": req["rid"]})[1]["status"] == "expired"
    devs = {d["name"]: d for d in devices.listing()}
    assert devs["Laptop"]["approved_by"] == "Telefon"
    # kod zużyty
    assert not _req(srv, "POST", "/v1/pair/approve", ph_key, body={"code": req["code"], "allow": True})[1]["ok"]


def test_denied_request_and_panel_approval(srv):
    st, a = _req(srv, "POST", "/v1/pair/request", body={"name": "Obcy", "class": "pc"})
    assert srvadmin.devices_list(None)["requests"][0]["name"] == "Obcy"
    assert srvadmin.pair_approve(None, {"code": a["code"], "allow": False})["status"] == "denied"
    assert _req(srv, "POST", "/v1/pair/poll", body={"rid": a["rid"]})[1]["status"] == "denied"
    st, b = _req(srv, "POST", "/v1/pair/request", body={"name": "Mój PC", "class": "pc"})
    assert srvadmin.pair_approve(None, {"code": b["code"], "allow": True})["ok"]
    key = _req(srv, "POST", "/v1/pair/poll", body={"rid": b["rid"]})[1]["key"]
    assert _req(srv, "GET", "/v1/info", key)[0] == 200
    # zły identyfikator prośby nic nie zdradza
    assert _req(srv, "POST", "/v1/pair/poll", body={"rid": "zgadywanie"})[1]["status"] == "expired"


def test_pc_client_join_flow(srv, monkeypatch):
    from emustart import api as api_mod
    host, port, _nas = srv
    ph_key, _, _ = _pair(srv, "Telefon", "phone")
    saved = {}
    monkeypatch.setattr("emustart.config.save", lambda c: saved.update(c))
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg = {}
    r = a.server_join(f"{host}:{port}")
    assert r["ok"] and r["pretty"]
    assert a.server_join_poll()["status"] == "pending"
    code = r["pretty"]
    st, ok = _req(srv, "POST", "/v1/pair/approve", ph_key, body={"code": code, "allow": True})
    assert ok["ok"]
    assert a.server_join_poll()["status"] == "approved"
    assert saved["server_url"] == f"http://{host}:{port}" and saved["server_key"]
    # ten komputer może teraz sam zatwierdzać kolejne
    st, nxt = _req(srv, "POST", "/v1/pair/request", body={"name": "Tablet", "class": "phone"})
    assert a.server_approve(f"emustart://approve?u=x&c={nxt['code']}", None)["request"]["name"] == "Tablet"
    assert a.server_approve(nxt["pretty"], True)["status"] == "approved"


def test_pc_pushes_missing_game_art_to_server(srv, tmp_path, monkeypatch):
    from emustart import art, artsync, library
    host, port, _nas = srv
    pc_key, _, _ = _pair(srv, "Komputer", "pc")
    monkeypatch.setattr(artsync, "SERVER_MEDIA", tmp_path / "serwer-media")
    with library.db() as c:
        for name in ("Gra (USA)", "Inna (Europe)"):
            c.execute("INSERT INTO games(es, rel, name, title, tags, files, size, seen) "
                      "VALUES('snes', ?, ?, ?, '', '[]', 0, 0)", (name + ".sfc", name, name))
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 50
    pc = paths.MEDIA / "snes"
    pc.mkdir(parents=True)
    (pc / "Gra (USA).logo.png").write_bytes(png)
    (pc / "Gra (USA).box.png").write_bytes(png)
    (pc / "Nieznana (USA).logo.png").write_bytes(png)             # serwer nie ma takiej gry
    srvd = tmp_path / "serwer-media" / "snes"
    srvd.mkdir(parents=True)
    (srvd / "Gra (USA).box.png").write_bytes(b"\x89PNG\r\n\x1a\nwlasna-serwera")
    cfg = {"server_url": f"http://{host}:{port}", "server_key": pc_key}
    assert artsync.push_missing(cfg) == 1                           # tylko brakujące logo
    assert (srvd / "Gra (USA).logo.png").read_bytes() == png
    assert (srvd / "Gra (USA).box.png").read_bytes().endswith(b"wlasna-serwera")   # okładka serwera zostaje
    gid = library.db().execute("SELECT id FROM games WHERE name='Gra (USA)'").fetchone()[0]
    assert library.game(gid)["art_logo"] == art.HAS
    assert artsync.push_missing(cfg) == 0                           # drugi raz nic
    # telefon dostaje logo w liście gier (grafiki przez serwer)
    assert artsync.server_put("snes", "../zle.logo.png", png)[0] == 400
    assert artsync.server_put("snes", "Gra (USA).logo.png", b"nie png")[0] == 400
    webp = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"x" * 20                # logo WebP zapisane jako .png
    assert artsync.server_put("snes", "Gra (USA).box.png", webp)[0] == 200


def test_pc_core_becomes_server_recommendation(srv, monkeypatch):
    from emustart import logos
    host, port, _nas = srv
    pc_key, _, _ = _pair(srv, "Komputer", "pc")
    monkeypatch.setattr(remote.API, "_cfg", {"server_token": MASTER})
    monkeypatch.setattr("emustart.config.save", lambda c: None)
    st, _ = _req(srv, "PUT", "/v1/system?es=snes&logo=keep&glow=0&emu=retroarch&core=bsnes", pc_key, body=b"")
    assert st == 200
    sc = remote.API._cfg["systems"]["snes"]
    assert sc["emu_rec"] == "retroarch" and sc["core_rec"] == "bsnes"
    st, _ = _req(srv, "PUT", "/v1/system?es=ps2&logo=keep&emu=standalone&core=", pc_key, body=b"")
    assert remote.API._cfg["systems"]["ps2"]["emu_rec"] == "standalone"
    # komputer: rodzina i rdzeń z ustawień systemu
    cfg = {"systems": {"snes": {"exe": r"D:\emu\RetroArch\retroarch.exe",
                                "args": r'-L "D:\emu\RetroArch\cores\bsnes_libretro.dll" -f'},
                       "ps2": {"exe": r"D:\emu\PCSX2\pcsx2-qt.exe", "args": "-nogui"}}}
    assert logos._system_emu(cfg, "snes") == ("retroarch", "bsnes")
    assert logos._system_emu(cfg, "ps2") == ("standalone", "")
    assert logos._system_emu(cfg, "nes") == ("", "")
