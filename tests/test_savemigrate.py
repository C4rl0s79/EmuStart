"""Migracja zapisów sprzed 0.46 (foldery profili) do paczek synchronizacji (emustart/savemigrate.py)
i wspólne zapisy w grze dla zgodnych rdzeni (savebundles.SAVE_GROUPS)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from emustart import library, savebundles, savemigrate, savesync

from test_core import _write, env  # noqa: F401  (fixture)

CT = "Chrono Trigger (USA) (MSU1)"


@pytest.fixture()
def nas(env):
    _cfg, _roms, tmp = env
    with library.db() as c:
        for es, name, rel in (("snesmsu1", CT, f"{CT}.zip"), ("psx", "Final Fantasy VIII (USA)", "FF8.m3u"),
                              ("snes", "Inna (USA)", "Inna (USA).sfc")):
            c.execute("INSERT INTO games(es, name, title, rel, files, size) VALUES(?,?,?,?,'[]',1)", (es, name, name, rel))
    root = tmp / "profiles"
    j = root / "Jezus"
    stem = "Chrono_Trigger_(USA)_(MSU1)"
    _write(j / "save/retroarch/saves/Snes9x" / f"{stem}.srm", b"SRAM-045")
    _write(j / "save/retroarch/.emustart-stamp", b"1760000000.000-BATTLE_STATION")
    _write(j / "save/retroarch/states/bsnes" / f"{stem}.state3", b"ST3")
    _write(j / "save/retroarch/states/bsnes" / f"{stem}.state3.png", b"PNG3")
    _write(j / "states/shared/retroarch/Snes9x" / f"{stem}.state1", b"S9X-1")
    (j / "states/shared/retroarch/Snes9x/.states.json").write_text(
        json.dumps({f"{stem}.state1": {"device": "Pixel", "platform": "telefon"}}), encoding="utf-8")
    _write(j / "save/duckstation/memcards/Final Fantasy VIII_1.mcd", b"CARD")
    _write(j / "save/pcsx2/memcards/Mcd001.ps2", b"SHARED")                 # wspólna — nie do jednej gry
    _write(j / "save/retroarch/saves/Snes9x/Nieznana gra.srm", b"?")
    return root


def bundle(acc, es, name, core, kind, emu="retroarch") -> dict:
    m = savesync._load(acc, es, savesync.game_id(es, name))
    return savesync._bundle(m, emu, core, kind) or {}


def files_of(acc, es, name, b) -> dict:
    p = savesync._game_dir(acc, es, savesync.game_id(es, name)) / "bundles" / f"{b['bundle_id']}.zip"
    with zipfile.ZipFile(p) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_empty_server_gets_bundles(nas):
    rep = savemigrate.run(nas)
    assert rep["accounts"] == 1 and rep["errors"] == 0 and rep["history"] == 0
    saves = bundle("Jezus", "snesmsu1", CT, "sram-snes", "saves")                # wspólne dla Snes9x i bsnes
    assert files_of("Jezus", "snesmsu1", CT, saves) == {"game.srm": b"SRAM-045"}
    assert "BATTLE_STATION" in saves["note"] and "sprzed 0.46" in saves["note"]
    st = files_of("Jezus", "snesmsu1", CT, bundle("Jezus", "snesmsu1", CT, "bsnes", "states"))
    assert st == {"game.state3": b"ST3", "game.state3.png": b"PNG3"}             # stany per rdzeń, z miniaturą
    s9 = bundle("Jezus", "snesmsu1", CT, "snes9x", "states")
    assert files_of("Jezus", "snesmsu1", CT, s9) == {"game.state1": b"S9X-1"} and "Pixel" in s9["note"]
    card = bundle("Jezus", "psx", "Final Fantasy VIII (USA)", "", "memcards", emu="duckstation")
    assert files_of("Jezus", "psx", "Final Fantasy VIII (USA)", card) == {"game_1.mcd": b"CARD"}
    unmatched = {Path(u["file"]).name for u in rep["unmatched"]}
    assert {"Mcd001.ps2", "Nieznana gra.srm"} <= unmatched
    # ze starych folderów nic nie zniknęło
    assert (nas / "Jezus/save/retroarch/saves/Snes9x/Chrono_Trigger_(USA)_(MSU1).srm").read_bytes() == b"SRAM-045"


def test_existing_bundle_not_overwritten_old_version_in_history_and_restorable(nas):
    gid = savesync.game_id("snesmsu1", CT)
    # Lumen (0.46.1) wysłał już zapisy bsnes — paczka istnieje
    zp = nas.parent / "lumen.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("game.srm", b"SRAM-LUMEN")
    assert savesync.import_bundle("Jezus", "snesmsu1", gid, "retroarch", "bsnes", "saves", zp, "lumen-pc",
                                  "z urządzenia", 2e9) == "new"
    rep = savemigrate.run(nas)
    assert rep["history"] >= 1
    cur = bundle("Jezus", "snesmsu1", CT, "sram-snes", "saves")
    assert files_of("Jezus", "snesmsu1", CT, cur) == {"game.srm": b"SRAM-LUMEN"}  # nie nadpisana
    old = next(h for h in cur["history"] if h.get("imported"))
    assert "sprzed 0.46" in old["note"]
    legacy = savemigrate.legacy_versions("Jezus")
    assert any(x["bundle_id"] == old["bundle_id"] for x in legacy)
    r = savesync.restore("Jezus", "snesmsu1", gid, "retroarch", "sram-snes", "saves", old["bundle_id"], "panel")
    cur = bundle("Jezus", "snesmsu1", CT, "sram-snes", "saves")
    assert r["revision"] == cur["revision"] and files_of("Jezus", "snesmsu1", CT, cur) == {"game.srm": b"SRAM-045"}
    assert any(h.get("updated_by_device_id") == "lumen-pc" for h in cur["history"])   # wersja Lumena w historii


def test_rerun_changes_nothing(nas):
    first = savemigrate.run(nas)
    again = savemigrate.run(nas)
    assert again["new"] == 0 and again["history"] == 0 and again["same"] == first["new"] + first["history"]
    # nowy plik w starym folderze (urządzenie z 0.45) — dochodzi przy następnym uruchomieniu
    _write(nas / "Jezus/save/retroarch/states/bsnes/Chrono_Trigger_(USA)_(MSU1).state4", b"ST4")
    third = savemigrate.run(nas)
    assert third["merged"] == 1 and third["new"] == 0                       # stany dołączone, nie historia
    st = files_of("Jezus", "snesmsu1", CT, bundle("Jezus", "snesmsu1", CT, "bsnes", "states"))
    assert st["game.state3"] == b"ST3" and st["game.state4"] == b"ST4"


def test_states_merge_into_existing_bundle_current_wins_slots(nas):
    """Paczka stanów z urządzenia już jest (Lumen: slot 3 i autozapis) — stany sprzed 0.46
    dochodzą jako nowe sloty; bieżące zostają w swoich slotach, nic nie znika."""
    gid = savesync.game_id("snesmsu1", CT)
    zp = nas.parent / "lumen-states.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("game.state3", b"LUMEN-3")
        z.writestr("game.state.auto", b"LUMEN-AUTO")
    savesync.import_bundle("Jezus", "snesmsu1", gid, "retroarch", "bsnes", "states", zp, "lumen-pc", "", 2e9)
    rep = savemigrate.run(nas)
    assert rep["merged"] >= 1
    st = files_of("Jezus", "snesmsu1", CT, bundle("Jezus", "snesmsu1", CT, "bsnes", "states"))
    assert st["game.state3"] == b"LUMEN-3" and st["game.state.auto"] == b"LUMEN-AUTO"   # bieżące w slotach
    moved = [a for a, v in st.items() if v == b"ST3"]
    assert moved and moved[0].startswith("game.state") and moved[0] != "game.state3"     # stary stan — nowy slot
    assert st.get(moved[0] + ".png") == b"PNG3"                                          # z miniaturą


def test_merge_states_rules():
    mine = {"game.state1": ("a", 10.0), "game.state1.png": ("ap", 10.0), "game.state.auto": ("x", 50.0)}
    other = {"game.state1": ("b", 20.0), "game.state1.png": ("bp", 20.0), "game.state2": ("c", 5.0),
             "game.state.auto": ("y", 40.0)}
    out, moves = savebundles.merge_states(mine, other)
    assert out["game.state1"] == ("other", "game.state1")                 # nowszy z drugiego miejsca w slocie
    assert out["game.state1.png"] == ("other", "game.state1.png")
    assert out["game.state2"] == ("other", "game.state2")                 # brakujący slot dochodzi
    assert out["game.state.auto"] == ("mine", "game.state.auto")          # nasz autozapis nowszy
    new = {n for _s, _o, n in moves}
    assert {out[n] for n in new} == {("mine", "game.state1"), ("other", "game.state.auto")}   # starsze — nowe sloty
    assert len(out) == 7


def test_dry_run_writes_nothing(nas):
    rep = savemigrate.run(nas, dry=True)
    assert rep["files"] > 0 and all(it["result"] == "plan" for it in rep["items"])
    assert not bundle("Jezus", "snesmsu1", CT, "sram-snes", "saves")
    assert not savemigrate.last_report()


def test_per_core_saves_fold_into_shared_group():
    """Zapisy wysłane przed 0.47 osobno pod Snes9x i bsnes → jedna paczka grupy (nowsza aktualna,
    starsza w historii); klient sprzed 0.47 dalej widzi ją pod swoim rdzeniem."""
    gid = "gfold"
    m = savesync._load("Ola", "snes", gid)
    for core, t, data in (("snes9x", 100.0, b"OLD"), ("bsnes", 200.0, b"NEW")):
        src = savesync._root() / f"{core}.zip"
        src.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("game.srm", data)
        b = savesync._bundle(m, "retroarch", core, "saves", create=True)
        savesync._install("Ola", "snes", gid, m, b, src, "0" * 64, src.stat().st_size, core, move=True, updated_at=t)
    assert savemigrate.fold_all() == 1
    man = savesync.manifest("Ola", "pc", "snes", gid, urls=False)
    cores = man["emulators"]["retroarch"]["cores"]
    g = cores["sram-snes"]["bundles"]["saves"]
    assert g["updated_by_device_id"] == "bsnes"
    assert cores["snes9x"]["bundles"]["saves"]["bundle_id"] == g["bundle_id"]          # lustro dla starych klientów
    hist = savesync.history("Ola", "snes", gid)
    grp = next(h for h in hist if h["core_id"] == "sram-snes")
    assert any(h.get("core_id") == "snes9x" for h in grp["history"])


def test_save_groups_are_explicit():
    assert savebundles.save_core("bsnes") == savebundles.save_core("snes9x") == "sram-snes"
    assert savebundles.save_core("bsnes_hd_beta") == "sram-snes"
    assert savebundles.save_core("nestopia") == savebundles.save_core("fceumm") == "sram-nes"
    assert savebundles.save_core("mgba") == "mgba"                                   # spoza listy — per rdzeń
    assert savemigrate.core_of_dir("Beetle PSX HW") == "mednafen_psx_hw"
    assert savemigrate.core_of_dir("bsnes-hd beta") == "bsnes_hd_beta"
