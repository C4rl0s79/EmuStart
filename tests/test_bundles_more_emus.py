"""Dolphin i PPSSPP w nowym formacie synchronizacji (savebundles): paczki z podfolderami
(zapisy PSP, karty GameCube jako pliki .gci, zapisy Wii), stany z nazwami od ID / numeru gry,
sumowanie stanów z dwóch urządzeń, edycja numerów."""

from __future__ import annotations

from pathlib import Path

import pytest

from emustart import savebundles, saveslots, syncclient

from test_core import _write, env  # noqa: F401  (fixture)
from test_savesync_server import srv  # noqa: F401  (fixture)
from test_syncclient import PC, TEL, net  # noqa: F401  (fixture)

PSP = {"es": "psp", "name": "Final Fantasy Tactics (USA)", "rel": "Final Fantasy Tactics (USA).iso"}
GC = {"es": "gc", "name": "Baten Kaitos (USA)", "rel": "Baten Kaitos (USA).rvz"}


def psp_bundles(tmp: Path, who: int) -> list:
    base = tmp / f"dev{who}" / "PSP"
    return [savebundles.Bundle("ppsspp", "", "saves", base / "SAVEDATA", "ULUS10297", ["ULUS10297"]).collect(),
            savebundles.Bundle("ppsspp", "", "states", base / "PPSSPP_STATE", "ULUS10297", ["ULUS10297"]).collect()]


def gc_bundles(tmp: Path, who: int) -> list:
    user = tmp / f"dev{who}" / "Dolphin"
    return [savebundles.Bundle("dolphin", "", "saves", user, "GKBEAF", ["GKBEAF"]).collect(),
            savebundles.Bundle("dolphin", "", "states", user / "StateSaves", "GKBEAF", ["GKBEAF"]).collect()]


def tree(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()} if d.is_dir() else {}


def test_ppsspp_saves_with_folders_and_states(net):
    cfgs, tmp = net
    psp = tmp / "dev1" / "PSP"
    _write(psp / "SAVEDATA" / "ULUS10297DATA00" / "DATA.BIN", b"FFT-1")
    _write(psp / "SAVEDATA" / "ULUS10297DATA00" / "PARAM.SFO", b"SFO")
    _write(psp / "SAVEDATA" / "ULUS99999DATA00" / "DATA.BIN", b"INNA GRA")
    _write(psp / "PPSSPP_STATE" / "ULUS10297_1.ppst", b"STAN-1")
    _write(psp / "PPSSPP_STATE" / "ULUS99999_1.ppst", b"INNY STAN")
    b = psp_bundles(tmp, PC)
    assert [a for _p, a in b[0].files] == ["ULUS10297DATA00/DATA.BIN", "ULUS10297DATA00/PARAM.SFO"]
    assert syncclient.before(cfgs[PC], PC, b, PSP).uploaded == 2

    r = syncclient.before(cfgs[TEL], TEL, psp_bundles(tmp, TEL), PSP)
    assert r.downloaded == 2 and not r.conflicts
    tel = tmp / "dev2" / "PSP"
    assert tree(tel / "SAVEDATA") == {"ULUS10297DATA00/DATA.BIN": b"FFT-1", "ULUS10297DATA00/PARAM.SFO": b"SFO"}
    assert tree(tel / "PPSSPP_STATE") == {"ULUS10297_1.ppst": b"STAN-1"}

    # zapis w grze na telefonie → PC
    _write(tel / "SAVEDATA" / "ULUS10297DATA00" / "DATA.BIN", b"FFT-2")
    syncclient.after(cfgs[TEL], TEL, psp_bundles(tmp, TEL), PSP)
    syncclient.before(cfgs[PC], PC, psp_bundles(tmp, PC), PSP)
    assert (psp / "SAVEDATA" / "ULUS10297DATA00" / "DATA.BIN").read_bytes() == b"FFT-2"
    assert (psp / "SAVEDATA" / "ULUS99999DATA00" / "DATA.BIN").read_bytes() == b"INNA GRA"


def test_dolphin_gci_wii_and_states_merge(net):
    cfgs, tmp = net
    user = tmp / "dev1" / "Dolphin"
    _write(user / "GC" / "USA" / "Card A" / "01-GKBE-Baten Kaitos.gci", b"GCI")
    _write(user / "GC" / "USA" / "Card A" / "01-GXXE-PokemonXD.gci", b"INNA")
    _write(user / "Wii" / "title" / "00010000" / "474b4245" / "data" / "save.bin", b"WII")
    _write(user / "StateSaves" / "GKBEAF.s01", b"PC-1")
    b = gc_bundles(tmp, PC)
    assert [a for _p, a in b[0].files] == ["GC/USA/Card A/01-GKBE-Baten Kaitos.gci",
                                            "Wii/title/00010000/474b4245/data/save.bin"]
    syncclient.before(cfgs[PC], PC, b, GC)
    syncclient.before(cfgs[TEL], TEL, gc_bundles(tmp, TEL), GC)
    tel = tmp / "dev2" / "Dolphin"
    assert (tel / "GC" / "USA" / "Card A" / "01-GKBE-Baten Kaitos.gci").read_bytes() == b"GCI"
    assert not (tel / "GC" / "USA" / "Card A" / "01-GXXE-PokemonXD.gci").exists()
    assert (tel / "Wii" / "title" / "00010000" / "474b4245" / "data" / "save.bin").read_bytes() == b"WII"

    # stan 1 zmieniony na obu urządzeniach: suma — nic nie ginie, starszy dostaje wolny numer
    _write(tel / "StateSaves" / "GKBEAF.s01", b"TEL-1")
    syncclient.after(cfgs[TEL], TEL, gc_bundles(tmp, TEL), GC)
    _write(user / "StateSaves" / "GKBEAF.s01", b"PC-1b")
    r = syncclient.before(cfgs[PC], PC, gc_bundles(tmp, PC), GC)
    assert not r.conflicts
    got = tree(user / "StateSaves")
    assert sorted(got.values()) == [b"PC-1b", b"TEL-1"] and set(got) == {"GKBEAF.s01", "GKBEAF.s02"}


def test_dolphin_ppsspp_numbers():
    b = savebundles.Bundle("dolphin", "", "states", Path("x"), "GKBEAF", ["GKBEAF"])
    assert saveslots.state_no("GKBEAF.s05") == 5 and saveslots.state_no("ULUS10297_3.ppst") == 3
    assert saveslots._arc_no(b, "GKBEAF.s05", 7) == "GKBEAF.s07"
    p = savebundles.Bundle("ppsspp", "", "states", Path("x"), "ULUS10297", ["ULUS10297"])
    assert saveslots._arc_no(p, "ULUS10297_1.ppst", 0) == "ULUS10297_0.ppst"
    with pytest.raises(ValueError):
        savebundles.Bundle("ppsspp", "", "saves", Path("x"), "ULUS10297", ["ULUS10297"]).local_path("../x/DATA.BIN")
    with pytest.raises(ValueError):
        savebundles.Bundle("ppsspp", "", "saves", Path("x"), "ULUS10297", ["ULUS10297"]).local_path("ULUS99999DATA00/DATA.BIN")
    assert savebundles.psp_serial({"serial": "ULUS-10297"}) == "ULUS10297"
