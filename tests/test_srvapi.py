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
