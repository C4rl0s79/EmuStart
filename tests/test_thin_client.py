"""Komputer bez folderów z grami bierze wszystko z serwera (0.38): biblioteka, grafiki,
zalecany emulator; niczego nie skanuje."""

from __future__ import annotations

import json

import pytest

from emustart import api as api_mod, config, devices, libsync, library, nasfs, netsrc, paths, remote, server, srvadmin

MASTER = "m" * 32


class _Api:
    _cfg = {"server_token": MASTER, "systems": {"snes": {"emu_rec": "retroarch", "core_rec": "bsnes"}}}
    _scan: dict = {}


@pytest.fixture()
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "API", _Api())
    monkeypatch.setattr(remote, "NAS_ROOT", None)
    monkeypatch.setattr(srvadmin, "ENABLED", True)
    media = tmp_path / "serwer-media"
    (media / "snes").mkdir(parents=True)
    (media / "snes" / "Gra (USA).box.png").write_bytes(b"\x89PNG\r\n\x1a\nokladka")
    monkeypatch.setitem(server.ROUTES, "/media/", media)
    url = server.start(0, bind="127.0.0.1")
    netsrc._info.clear()
    nasfs.reset()
    host, port = url.removeprefix("http://").split(":")
    import keyhelp
    return keyhelp.pc_cfg(host, port, "Nowy PC", monkeypatch), media


SERVER_GAMES = {
    "snes": [{"rel": "Gra (USA).sfc", "name": "Gra (USA)", "title": "Gra", "tags": "(USA)",
              "files": json.dumps([["Gra (USA).sfc", 1024, 1.0]]), "size": 1024, "is_dir": 0, "multidisc": 0,
              "parent": "", "hidden": 0, "trans": "", "tbase": "", "art_box": 1, "art_snap": 2, "art_logo": 0}],
    "nes": [{"rel": "Mario.nes", "name": "Mario", "title": "Mario", "tags": "", "files": json.dumps([["Mario.nes", 40, 1.0]]),
             "size": 40, "is_dir": 0, "multidisc": 0, "parent": "", "hidden": 0, "trans": "", "tbase": "",
             "art_box": 0, "art_snap": 0, "art_logo": 0}],
}


def _server_lib(monkeypatch, scanned):
    monkeypatch.setattr(libsync, "server_systems", lambda: [
        {"es": es, "display": es.upper(), "games": len(g), "scanned": scanned[es], "emu_rec": "retroarch" if es == "snes" else "",
         "core": "bsnes" if es == "snes" else "", "logo": "/media/_systems/snes.custom.png" if es == "snes" else "",
         "logo_glow": False}
        for es, g in SERVER_GAMES.items() if es in scanned])
    monkeypatch.setattr(libsync, "server_games", lambda es: SERVER_GAMES.get(es, []))


def test_library_comes_from_server_without_scanning(srv, monkeypatch, tmp_path):
    cfg, media = srv
    _server_lib(monkeypatch, {"snes": 100.0, "nes": 100.0})
    assert libsync.thin(cfg)
    r = libsync.sync(cfg)
    assert r["ok"] and r["games"] == 2 and r["changed"] == 2
    g = library.db().execute("SELECT * FROM games WHERE rel='Gra (USA).sfc'").fetchone()
    assert g["src"] == libsync.SERVER_SRC and g["art_box"] == 1 and json.loads(g["files"])[0][1] == 1024
    # bez zmian na serwerze — nic nie pobiera ponownie
    calls = []
    monkeypatch.setattr(libsync, "server_games", lambda es: calls.append(es) or SERVER_GAMES.get(es, []))
    assert libsync.sync(cfg)["changed"] == 0 and calls == []
    # system zniknął z serwera — znika też tu
    _server_lib(monkeypatch, {"snes": 100.0})
    libsync.sync(cfg)
    assert [r["es"] for r in library.db().execute("SELECT es FROM systems")] == ["snes"]
    assert library.db().execute("SELECT COUNT(*) FROM games WHERE es='nes'").fetchone()[0] == 0
    # zalecany emulator i logo systemu z serwera
    assert libsync.recommended("snes")["core"] == "bsnes"
    assert libsync.logo_for("snes")["url"] == "/media/_systems/snes.custom.png"


def test_media_fetched_from_server_on_demand(srv, monkeypatch, tmp_path):
    cfg, media = srv
    target = tmp_path / "lokalne" / "snes" / "Gra (USA).box.png"
    assert libsync.fetch_media(cfg, "snes/Gra (USA).box.png", target)
    assert target.read_bytes().endswith(b"okladka")
    assert not libsync.fetch_media(cfg, "snes/Nie ma.box.png", tmp_path / "x.png")      # brak — bez błędu


def test_thin_client_counts_as_configured(monkeypatch):
    paths.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.CONFIG_PATH.write_text("{}", encoding="utf-8")
    assert config.is_configured({"server_url": "https://x", "server_key": "dev:abc", "rom_roots": []})
    assert not config.is_configured({"rom_roots": []})


def test_recommended_emulator_is_picked(monkeypatch):
    monkeypatch.setattr(libsync, "recommended", lambda es: {"emu": "retroarch", "core": "bsnes"})
    monkeypatch.setattr(api_mod.emulators, "options_for", lambda info, root: [
        {"label": "RetroArch: Snes9x", "exe": r"D:\emu\RetroArch\retroarch.exe", "args": r'-L "x\snes9x_libretro.dll"'},
        {"label": "RetroArch: bsnes", "exe": r"D:\emu\RetroArch\retroarch.exe", "args": r'-L "x\bsnes_libretro.dll"'}])
    assert api_mod._recommended_option("snes", {"emu_root": "D:\\emu"})["label"] == "RetroArch: bsnes"


def test_account_without_games_gets_no_library(srv, monkeypatch):
    cfg, _media = srv
    did = next(d["id"] for d in devices.listing() if d["name"] == "Nowy PC")
    devices.set_games(did, False)
    with library.db():
        pass
    from emustart import devices as dv
    dv._load()[did]["account"] = "Ania"
    r = libsync.sync(cfg)
    assert not r["ok"] and "nie pobiera gier" in r["reason"]


def test_bios_comes_from_server_when_no_local_folder(srv, monkeypatch, tmp_path):
    from emustart import bios, installer
    cfg, _media = srv
    srvbios = tmp_path / "serwer-bios"
    srvbios.mkdir()
    (srvbios / "scph5501.bin").write_bytes(b"bios-ps1")
    monkeypatch.setattr(remote, "_bios_find", lambda c, rel: srvbios / rel if (srvbios / rel).is_file() else None)
    ra = tmp_path / "RetroArch"
    monkeypatch.setattr(bios, "firmware", lambda root, core: ["scph5501.bin", "nie-ma.bin"])
    copied = bios.sync(cfg, ra, "swanstation")
    assert copied == ["scph5501.bin"]
    assert (installer.ra_system_dir(ra) / "scph5501.bin").read_bytes() == b"bios-ps1"
    assert bios.sync(cfg, ra, "swanstation") == []                       # już jest — bez ponownego pobierania
