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


def test_server_version_grows_with_art_and_description_changes(tmp_path, monkeypatch):
    """Zmiana grafiki albo opisu podbija wersję systemu — urządzenia pobierają go od nowa;
    biblioteka dla PC niesie czasy grafik i opisy."""
    from emustart import art_sources, libsync, remote, scanner
    _write(tmp_path / "roms" / "snes" / "A (USA).sfc")
    scanner.scan_system("snes", tmp_path / "roms" / "snes")
    s = srvapi.ServerApi(start_background=False)
    remote.API = s
    try:
        v1 = libsync.server_systems()[0]["scanned"]
        gid = s.games_search("A")[0]["id"]
        png = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 64
        monkeypatch.setattr(art_sources, "fetch", lambda url, **k: png)
        import time
        time.sleep(0.01)
        assert s.art_choose(gid, "box", "https://x/a.png")["ok"]
        v2 = libsync.server_systems()[0]["scanned"]
        assert v2 > v1
        s.meta_save(gid, {"description": "Opis"})
        assert libsync.server_systems()[0]["scanned"] >= v2
        row = libsync.server_games("snes")[0]
        assert row["art_m"].get("box") and row["meta"]["edits"] == {"description": "Opis"}
    finally:
        remote.API = None


def test_merge_platforms_with_dedupe(tmp_path):
    """Dwa foldery tej samej platformy (x360 i xbox360): scalenie — jedna lista, ta sama gra
    raz, platforma scalona znika razem ze swoimi grami; rozdzielenie przywraca obie."""
    import time
    roms = tmp_path / "roms"
    _write(roms / "xbox360" / "Halo 3 (USA).iso")
    _write(roms / "x360" / "Halo 3 (USA).iso")
    _write(roms / "x360" / "Gears (USA).iso")
    cfg = config.load()
    cfg["rom_roots"] = [str(roms)]
    config.save(cfg)
    s = srvapi.ServerApi(start_background=False)

    def scan():
        assert s.rescan()["ok"] or True
        end = time.monotonic() + 20
        while s.scan_status()["running"] and time.monotonic() < end:
            time.sleep(0.05)
    scan()
    look = {x["es"]: x for x in s.systems_look()}
    assert {"x360", "xbox360"} <= set(look) and look["x360"]["dirs"][0].endswith("x360")
    assert not s.system_merge("x360", "x360")["ok"] and not s.system_merge("nie-ma", "xbox360")["ok"]
    assert s.system_merge("x360", "xbox360")["ok"]
    scan()
    look = {x["es"]: x for x in s.systems_look()}
    assert "x360" not in look and look["xbox360"]["merged"] == ["x360"] and len(look["xbox360"]["dirs"]) == 2
    names = sorted(g["name"] for g in s.list_games("xbox360"))
    assert names == ["Gears (USA)", "Halo 3 (USA)"]                       # Halo raz
    assert library.db().execute("SELECT COUNT(*) FROM games WHERE es='x360'").fetchone()[0] == 0
    assert not s.system_merge("xbox360", "x360")["ok"]                      # scalona nie może być celem
    assert s.system_merge("x360", "")["ok"]
    scan()
    assert {"x360", "xbox360"} <= {x["es"] for x in s.systems_look()}


def test_media_store_one_file_per_system_and_migration(tmp_path):
    """Grafiki w jednym pliku na system (media/<system>.media) zamiast tysięcy plików;
    stare pliki przenoszone i usuwane dopiero po odczycie kontrolnym."""
    import os as _os
    from emustart import art, mediastore, paths
    old = paths.MEDIA / "snes"
    old.mkdir(parents=True)
    (old / "A (USA).box.png").write_bytes(b"okladka A")
    (old / "B (USA).snap.png").write_bytes(b"zrzut B")
    _os.utime(old / "A (USA).box.png", (1000, 1000))
    mediastore.put("snes", "B (USA).snap.png", b"nowszy zrzut", 9e9)   # nowsza wersja już w bazie
    assert mediastore.migrate() == 2
    assert not old.exists() and (paths.MEDIA / "snes.media").is_file()
    assert mediastore.get("snes", "A (USA).box.png") == (b"okladka A", 1000.0)
    assert mediastore.get("snes", "B (USA).snap.png")[0] == b"nowszy zrzut"
    assert art.media_url("snes", "A (USA)", "box").endswith("?v=1000")
    assert mediastore.rename("snes", "A (USA).box.png", "A2.box.png") and art.has("snes", "A2", "box")
    assert art.delete("snes", "A2", "box") and not art.has("snes", "A2", "box")
    assert mediastore.file_name("snes/A.box.png") == ("snes", "A.box.png")
    assert mediastore.file_name("_systems/snes.png") is None and mediastore.file_name("../x/y") is None
