"""EmuStart Server (emustart/srvapi.py) i EmuStart na PC (emustart/api.py) — podział ról."""

from pathlib import Path

from emustart import api, config, library, srvapi


def _write(p: Path, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_server_scans_folders_and_serves_details(tmp_path):
    roms = tmp_path / "roms"
    _write(roms / "snes" / "Super Mario World (USA).sfc")
    cfg = config.load()
    cfg["rom_roots"] = [str(roms)]
    config.save(cfg)
    s = srvapi.ServerApi(start_background=False)
    assert s.rescan()["ok"]
    import time
    end = time.monotonic() + 20
    while s.scan_status()["running"] and time.monotonic() < end:
        time.sleep(0.05)
    assert s.scan_status()["result"]["ok"]
    assert [x["es"] for x in s.list_systems()] == ["snes"]
    g = s.list_games("snes")[0]
    d = s.game_detail(g["id"])
    assert d["title"] == "Super Mario World" and d["rel"].endswith(".sfc") and d["variants"] == []


def test_pc_client_has_no_server_jobs():
    """EmuStart na PC nie skanuje folderów i nie pobiera grafik całej biblioteki — robi to
    serwer (panel serwera); PC bierze bibliotekę z serwera."""
    for name in ("art_overview", "art_start", "art_cancel", "art_status", "launchbox_update",
                 "launchbox_status", "import_pylinks_keys", "system_rescan", "_arcade_meta",
                 "_rescan_systems", "emulator_options"):
        assert not hasattr(api.Api, name), name
    for name in ("art_overview", "art_start", "rescan", "list_games", "game_detail", "_arcade_meta"):
        assert hasattr(srvapi.ServerApi, name), name


def test_pc_refresh_needs_server(tmp_path):
    a = api.Api.__new__(api.Api)
    a._cfg = config.load()
    a._scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}
    assert a.rescan()["ok"]
    import time
    end = time.monotonic() + 5
    while a._scan["running"] and time.monotonic() < end:
        time.sleep(0.02)
    assert not a._scan["result"]["ok"] and "zaloguj" in a._scan["result"]["reason"]
    assert library.systems_summary() == []


def test_server_edits_game_art_meta_and_system_look(tmp_path, monkeypatch):
    """Panel serwera: grafiki i opis jednej gry, logo/nazwa systemu — urządzenia biorą je
    z serwera (wcześniej ustawiało się je na PC)."""
    from emustart import art_sources, metadata, scanner
    roms = tmp_path / "roms"
    _write(roms / "snes" / "Super Mario World (USA).sfc")
    _write(roms / "snes" / "Zelda 100% (USA).sfc")
    scanner.scan_system("snes", roms / "snes")
    s = srvapi.ServerApi(start_background=False)
    assert [g["name"] for g in s.games_search("mario")] == ["Super Mario World (USA)"]
    assert [g["name"] for g in s.games_search("100%")] == ["Zelda 100% (USA)"]   # % dosłownie
    assert s.games_search("_") == []                                        # _ dosłownie, nie „dowolny znak”
    gid = s.games_search("mario", "snes")[0]["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    monkeypatch.setattr(art_sources, "fetch", lambda url, **k: png)
    assert s.art_choose(gid, "box", "https://x/box.png")["ok"]
    e = s.game_edit(gid)
    assert e["ok"] and e["box"].startswith("/media/snes/") and not e["snap"]
    assert s.art_clear(gid, "box")["ok"] and not s.game_edit(gid)["box"]
    assert not s.art_choose(gid, "zle", "https://x")["ok"]
    assert s.meta_save(gid, {"title": "Mario", "description": "Opis", "hack": "x"})["ok"]
    assert metadata.get(gid)["_edits"] == {"title": "Mario", "description": "Opis"}
    assert s.system_set("snes", {"name": "Super Nintendo", "glow": True, "enabled": False})["ok"]
    look = {x["es"]: x for x in s.systems_look()}["snes"]
    assert look["display"] == "Super Nintendo" and look["glow"] and not look["enabled"]
    assert s.system_set("snes", {"name": ""})["ok"]                          # pusta — domyślna nazwa
    assert {x["es"]: x for x in s.systems_look()}["snes"]["display"] == look["default_name"]
    assert not s.system_set("nieznany", {"name": "x"})["ok"]
    assert s.system_logo_choose("snes", "none")["url"] == ""                  # bez logo


def test_recommended_emulator_set_in_panel(tmp_path):
    """Zalecany emulator i rdzeń systemu ustawia panel serwera (wcześniej wysyłał je PC)."""
    from emustart import libsync, remote, scanner
    _write(tmp_path / "roms" / "snes" / "A (USA).sfc")
    scanner.scan_system("snes", tmp_path / "roms" / "snes")
    s = srvapi.ServerApi(start_background=False)
    assert s.system_set("snes", {"emu_rec": "retroarch", "core_rec": "bsnes"})["ok"]
    assert not s.system_set("snes", {"emu_rec": "inny"})["ok"]
    assert not s.system_set("snes", {"core_rec": "../x"})["ok"]
    look = {x["es"]: x for x in s.systems_look()}["snes"]
    assert look["emu_rec"] == "retroarch" and look["core_rec"] == "bsnes" and look["core_default"]
    remote.API = s
    try:
        rec = {x["es"]: x for x in libsync.server_systems()}["snes"]
        assert rec["emu_rec"] == "retroarch" and rec["core"] == "bsnes"
    finally:
        remote.API = None
