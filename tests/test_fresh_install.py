"""Nowa instalacja niczego nie zakłada (0.37.4): bez ścieżek z pierwszej instalacji, bez
folderu profili na NAS; stary, przeniesiony na serwer folder profili nie jest używany."""

from __future__ import annotations

import json

from emustart import config, emulators, nasfs, paths, profiles


def test_new_install_has_no_paths():
    cfg = config.load()                                  # brak config.json
    assert config.rom_roots(cfg) == [] and cfg["emu_root"] == "" and cfg["profiles_nas"] == ""
    assert not nasfs.backend(cfg).online()                       # bez serwera — offline
    assert emulators._scan_root(__import__("pathlib").Path(cfg["emu_root"])) == ([], {})


def test_existing_config_keeps_old_defaults():
    paths.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.CONFIG_PATH.write_text(json.dumps({"rom_roots": [r"Z:\ROMS\No-Intro"]}), encoding="utf-8")
    cfg = config.load()
    assert cfg["emu_root"] == r"D:\emu\emulatory" and cfg["profiles_nas"] == r"Z:\emustart\Profiles"
    paths.CONFIG_PATH.write_text(json.dumps({"emu_root": r"E:\emu", "profiles_nas": ""}), encoding="utf-8")
    cfg = config.load()
    assert cfg["emu_root"] == r"E:\emu" and cfg["profiles_nas"] == ""       # ustawione — bez zmian


def test_moved_profiles_folder_is_never_used(tmp_path):
    old = tmp_path / "Profiles"
    (old / "Jezus").mkdir(parents=True)
    fs = nasfs.DirFs(old)
    assert fs.online()
    (old / nasfs.MOVED_MARK).write_text("{}")
    assert not fs.online()
    try:
        fs.write("Jezus/save/x.srm", b"zapis")
        raise AssertionError("zapis do przeniesionego folderu")
    except OSError as ex:
        assert "przeniesiony" in str(ex)
    assert not (old / "Jezus" / "save").exists()
