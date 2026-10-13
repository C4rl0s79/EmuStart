"""Stany RetroArcha z telefonu podsuwane tam, gdzie RetroArch na komputerze ich szuka."""

from __future__ import annotations

import os
from pathlib import Path

from emustart import nasfs, profiles, rastates, savemgr


class _Ra:
    def __init__(self, home: Path):
        self.home = home

    def _cfg_dir(self, key, default):
        return self.home / default


def _setup(tmp_path, monkeypatch, sort_core=True, by_content=False):
    home = tmp_path / "RetroArch"
    (home / "info").mkdir(parents=True)
    (home / "info" / "snes9x_libretro.info").write_text('corename = "Snes9x"\n', encoding="utf-8")
    (home / "retroarch.cfg").write_text(
        f'sort_savestates_enable = "{str(sort_core).lower()}"\n'
        f'sort_savestates_by_content_enable = "{str(by_content).lower()}"\n', encoding="utf-8")
    monkeypatch.setattr(profiles, "LOCAL", tmp_path / "profiles")
    nasfs.reset()
    cfg = {"profiles_nas": str(tmp_path / "nas")}
    monkeypatch.setattr(nasfs, "backend", lambda _cfg: nasfs.DirFs(tmp_path / "nas"))   # folder serwera
    phone = tmp_path / "nas" / "Jezus" / "states" / "shared" / "retroarch"
    return cfg, {"id": 1, "nas_name": "Jezus"}, _Ra(home), phone


def _w(p: Path, data: bytes, mtime: float | None = None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))


def test_core_id_from_args():
    assert rastates.core_id(r'-L "D:\emu\RetroArch\cores\bsnes_libretro.dll" -f') == "bsnes"
    assert rastates.core_id('-L cores/snes9x_libretro.dll') == "snes9x"


def test_phone_state_lands_where_retroarch_looks(tmp_path, monkeypatch):
    cfg, prof, ra, phone = _setup(tmp_path, monkeypatch)
    rom = tmp_path / "run" / "rom" / "Chrono_Trigger_(USA)_(MSU1).sfc"
    _w(phone / "Snes9x" / "Chrono_Trigger_(USA)_(MSU1).state", b"telefon", 2000)
    _w(phone / "Snes9x" / "Chrono_Trigger_(USA)_(MSU1).state.png", b"png", 2000)
    _w(ra.home / "states" / "Snes9x" / "Chrono_Trigger_(USA)_(MSU1).state", b"pc-stary", 1000)
    msg = rastates.present(cfg, prof, ra, "snes9x", rom)
    dst = ra.home / "states" / "Snes9x" / "Chrono_Trigger_(USA)_(MSU1).state"
    assert dst.read_bytes() == b"telefon" and "innego urządzenia" in msg
    assert list((tmp_path / "profiles" / "1" / "_backup").rglob("*.state"))      # stary w kopii
    # na komputerze nowszy — nie nadpisujemy
    _w(dst, b"pc-nowy", 3000)
    assert rastates.present(cfg, prof, ra, "snes9x", rom) == "" and dst.read_bytes() == b"pc-nowy"


def test_flat_folder_and_content_sorting(tmp_path, monkeypatch):
    cfg, prof, ra, phone = _setup(tmp_path, monkeypatch, sort_core=False, by_content=True)
    rom = tmp_path / "gry" / "snes" / "Gra (USA).sfc"
    _w(phone / "Snes9x" / "Gra (USA).state1", b"x", 2000)
    rastates.present(cfg, prof, ra, "snes9x", rom)
    assert (ra.home / "states" / "snes" / "Gra (USA).state1").read_bytes() == b"x"


def test_other_core_is_reported_not_copied(tmp_path, monkeypatch):
    cfg, prof, ra, phone = _setup(tmp_path, monkeypatch)
    (ra.home / "info" / "bsnes_libretro.info").write_text('corename = "bsnes"\n', encoding="utf-8")
    _w(phone / "Snes9x" / "Gra (USA).state", b"x", 2000)
    msg = rastates.present(cfg, prof, ra, "bsnes", tmp_path / "Gra (USA).sfc")
    assert "Snes9x" in msg and "bsnes" in msg
    assert not (ra.home / "states" / "bsnes").exists()


def test_savemgr_flat_phone_state_not_guessed_when_ambiguous(tmp_path):
    root = tmp_path / "nas"
    _w(root / "Jezus/states/android/retroarch/Gra (USA).state", b"t")
    _w(root / "Jezus/save/retroarch/states/bsnes/Gra (USA).state", b"a")
    _w(root / "Jezus/save/retroarch/states/Snes9x/Gra (USA).state", b"b")
    _w(root / "Jezus/save/retroarch/states/PUAE/Inna.state", b"c")
    _w(root / "Jezus/save/retroarch/states/PUAE/Inna2.state", b"c")
    fs = nasfs.DirFs(root)
    r = savemgr.copy(fs, "Jezus", "states/android/retroarch/Gra (USA).state", "windows", "retroarch", "snes", "Gra (USA)")
    assert not r["ok"] and "rdzeniu" in r["reason"]
    _w(root / "Jezus/states/android/retroarch/Snes9x/Gra (USA).state", b"t2")
    r = savemgr.copy(fs, "Jezus", "states/android/retroarch/Snes9x/Gra (USA).state", "windows", "retroarch", "snes", "Gra (USA)")
    assert r["ok"] and r["rel"] == "save/retroarch/states/Snes9x/Gra (USA).state"


def test_legacy_phone_folder_still_read(tmp_path, monkeypatch):
    cfg, prof, ra, _shared = _setup(tmp_path, monkeypatch)
    legacy = tmp_path / "nas" / "Jezus" / "states" / "android" / "retroarch" / "snes9x"
    _w(legacy / "Gra (USA).state", b"stary-telefon", 2000)
    rastates.present(cfg, prof, ra, "snes9x", tmp_path / "Gra (USA).sfc")
    assert (ra.home / "states" / "Snes9x" / "Gra (USA).state").read_bytes() == b"stary-telefon"


def test_publish_and_layout_registry(tmp_path, monkeypatch):
    import json
    import time
    cfg, prof, ra, shared = _setup(tmp_path, monkeypatch)
    rom = tmp_path / "Gra (USA).sfc"
    start = time.time() - 5
    _w(ra.home / "states" / "Snes9x" / "Gra (USA).state", b"pc")
    _w(ra.home / "states" / "Snes9x" / "Inna (USA).state", b"nie ta gra")
    assert rastates.publish(cfg, prof, ra, "snes9x", rom, start) == 1
    assert (shared / "Snes9x" / "Gra (USA).state").read_bytes() == b"pc"
    assert not (shared / "Snes9x" / "Inna (USA).state").exists()
    assert rastates.publish(cfg, prof, ra, "snes9x", rom, start) == 0          # bez zmian
    rastates.present(cfg, prof, ra, "snes9x", rom)
    lay = json.loads((shared / ".layouts.json").read_text(encoding="utf-8"))
    me = next(iter(lay.values()))
    assert me["core"] is True and me["content"] is False and me["device"] == "komputer"


def test_phone_autosave_is_loaded_on_pc_and_not_overwritten(tmp_path, monkeypatch):
    """Autozapis z telefonu: PC go wczytuje, a autozapis z sesji, która nie wystartowała
    z wersji z serwera, nie nadpisuje wspólnego (idzie do kopii jako konflikt)."""
    import time
    monkeypatch.setattr(rastates, "CONFLICTS", [])
    cfg, prof, ra, shared = _setup(tmp_path, monkeypatch)
    rom = tmp_path / "Gra (USA).sfc"
    auto = ra.home / "states" / "Snes9x" / "Gra (USA).state.auto"
    _w(auto, b"pc-stary", 1000)
    _w(shared / "Snes9x" / "Gra (USA).state.auto", b"telefon", 2000)
    # start na PC: autozapis z telefonu na miejscu i do wczytania
    info: dict = {}
    msg = rastates.present(cfg, prof, ra, "snes9x", rom, info)
    assert auto.read_bytes() == b"telefon" and info["autoload"] and "Autozapis" in msg
    # wyjście: autozapis z tej sesji (wystartowała z wersji z serwera) → serwer
    start = time.time() - 5
    _w(auto, b"pc-po-grze")
    assert rastates.publish(cfg, prof, ra, "snes9x", rom, start, info) == 1
    assert (shared / "Snes9x" / "Gra (USA).state.auto").read_bytes() == b"pc-po-grze"

    # telefon zapisał w trakcie gry na PC → wersja z PC nie nadpisuje, trafia do kopii
    info = {}
    rastates.present(cfg, prof, ra, "snes9x", rom, info)
    _w(shared / "Snes9x" / "Gra (USA).state.auto", b"telefon-2", time.time() + 100)
    _w(auto, b"pc-3")
    assert rastates.publish(cfg, prof, ra, "snes9x", rom, start, info) == 0
    assert (shared / "Snes9x" / "Gra (USA).state.auto").read_bytes() == b"telefon-2"
    kopie = list((tmp_path / "nas" / "Jezus" / "_backup").rglob("konflikt/**/Gra (USA).state.auto"))
    assert kopie and kopie[0].read_bytes() == b"pc-3"
    assert "konflikt" in rastates.present(cfg, prof, ra, "snes9x", rom, {})   # komunikat przy następnej grze

    # sesja bez serwera na starcie: autozapis z serwera nie był wczytany — nie nadpisujemy go
    _w(auto, b"pc-offline")
    assert rastates.publish(cfg, prof, ra, "snes9x", rom, start, None) == 0
    assert (shared / "Snes9x" / "Gra (USA).state.auto").read_bytes() == b"telefon-2"


def test_no_shared_autosave_no_autoload(tmp_path, monkeypatch):
    cfg, prof, ra, shared = _setup(tmp_path, monkeypatch)
    info: dict = {}
    rastates.present(cfg, prof, ra, "snes9x", tmp_path / "Gra (USA).sfc", info)
    assert info["autoload"] is False and info["base"] == {}
