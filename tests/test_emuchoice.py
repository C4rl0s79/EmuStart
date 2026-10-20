"""Wybór emulatora i rdzenia na koncie (emustart/emuchoice.py): drugi komputer konta
dostaje te same wybory — inny rdzeń to inne paczki zapisów."""

from __future__ import annotations

from pathlib import Path

import pytest

from emustart import emuchoice, emulators, library, profiles

from test_core import _write, env  # noqa: F401  (fixture)


@pytest.fixture()
def acc(env, monkeypatch):
    cfg, _roms, tmp = env
    monkeypatch.setattr(profiles, "LOCAL", tmp / "pc1" / "profiles")
    cfg["profiles_nas"] = str(tmp / "nas")
    from _srv import use_server
    use_server(cfg, tmp / "nas", monkeypatch)
    emulators.forget_scan()
    with library.db() as c:
        c.execute("INSERT INTO games(es, name, title, rel, files, size) VALUES('snes','Chrono Trigger (USA)',"
                  "'Chrono Trigger','Chrono Trigger (USA).zip','[]',1)")
    return cfg, tmp


def machine(tmp: Path, name: str, cores=("snes9x", "bsnes")) -> str:
    root = tmp / name / "emu"
    _write(root / "RetroArch" / "retroarch.exe", b"MZ")
    for c in cores:
        _write(root / "RetroArch" / "cores" / f"{c}_libretro.dll", b"MZ")
    return str(root)


def opt(cfg, core):
    return next(o for o in emulators.options_for({"plat": "SNES", "libretro": ""}, cfg["emu_root"])
                if f"{core}_libretro" in o["args"])


def test_second_pc_gets_same_core_and_game_choice(acc, monkeypatch):
    cfg, tmp = acc
    pid = profiles.first_id()
    cfg["emu_root"] = machine(tmp, "pc1")
    emuchoice.record_system(cfg, pid, "snes", opt(cfg, "bsnes"))
    gid = library.db().execute("SELECT id FROM games").fetchone()["id"]
    g = library.game(gid)
    emuchoice.record_game(cfg, pid, g, opt(cfg, "snes9x"))

    # drugi komputer: inny folder emulatorów, domyślnie Snes9x, bez lokalnej kopii profilu
    monkeypatch.setattr(profiles, "LOCAL", tmp / "pc2" / "profiles")
    cfg2 = {**cfg, "emu_root": machine(tmp, "pc2"), "systems": {"snes": {**opt(cfg, "snes9x"), "enabled": True}}}
    cfg2["systems"]["snes"] = {**opt(cfg2, "snes9x"), "enabled": True}
    with library.db() as c:
        c.execute("DELETE FROM game_emu")
    changed, missing = emuchoice.apply(cfg2, pid)
    assert changed == 2 and not missing
    assert "bsnes_libretro" in cfg2["systems"]["snes"]["args"] and str(tmp / "pc2") in cfg2["systems"]["snes"]["exe"]
    row = library.db().execute("SELECT args FROM game_emu WHERE game_id=?", (gid,)).fetchone()
    assert "snes9x_libretro" in row["args"]

    # „tak jak system” dla gry — też na koncie
    emuchoice.record_game(cfg2, pid, g, None)
    with library.db() as c:
        c.execute("INSERT OR REPLACE INTO game_emu VALUES(?,?,?,?)", (gid, "x", "x.exe", ""))
    emuchoice.apply(cfg2, pid)
    assert not library.db().execute("SELECT 1 FROM game_emu").fetchone()


def test_missing_core_is_fetched_from_server(acc, monkeypatch):
    cfg, tmp = acc
    pid = profiles.first_id()
    cfg["emu_root"] = machine(tmp, "pc1")
    emuchoice.record_system(cfg, pid, "snes", opt(cfg, "bsnes"))
    monkeypatch.setattr(profiles, "LOCAL", tmp / "pc2" / "profiles")
    cfg2 = {**cfg, "emu_root": machine(tmp, "pc2", cores=("snes9x",)), "systems": {}}
    _changed, missing = emuchoice.apply(cfg2, pid)
    assert missing == [("snes", "core:bsnes")]


def test_choices_made_before_upgrade_go_to_account_but_defaults_do_not(acc, monkeypatch):
    cfg, tmp = acc
    pid = profiles.first_id()
    cfg["emu_root"] = machine(tmp, "pc1")
    monkeypatch.setattr(emuchoice, "_default_key", lambda c, es: "retroarch#snes9x")
    cfg["systems"] = {"snes": {**opt(cfg, "bsnes"), "enabled": True}}            # wybrany ręcznie
    assert emuchoice.seed(cfg, pid) == 1
    cfg["systems"] = {"snes": {**opt(cfg, "snes9x"), "enabled": True}}           # domyślny
    assert emuchoice.seed(cfg, pid) == 0                                         # konto ma już wybór
    assert profiles.json_get(cfg, pid, emuchoice.NAME)["systems"]["snes"]["key"] == "retroarch#bsnes"


def test_key_is_machine_independent():
    assert emuchoice.key_of({"exe": r"D:\emu\RetroArch\retroarch.exe",
                             "args": r'-L "D:\emu\RetroArch\cores\bsnes_libretro.dll" -f'}) == "retroarch#bsnes"
    assert emuchoice.key_of({"exe": r"E:\x\duckstation-qt-x64-ReleaseLTCG.exe"}) == "standalone:duckstation"
    assert emuchoice.install_item("retroarch#bsnes") == "core:bsnes"
