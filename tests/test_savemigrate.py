"""Wspólne zapisy w grze dla zgodnych rdzeni (savebundles.SAVE_GROUPS, savemigrate.fold_all) i sumowanie
stanów (savebundles.merge_states). Migracja zapisów sprzed 0.46 usunięta w 0.56.1."""

from __future__ import annotations

import zipfile

from emustart import savebundles, savemigrate, savesync


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
