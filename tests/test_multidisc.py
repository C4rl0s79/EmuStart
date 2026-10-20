"""Gry wielopłytowe: płyty oznaczone literą, podtytuły płyt (Redump), systemy z grami-katalogami."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from emustart import library, paths, scanner


@pytest.fixture()
def roms(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "data" / "lib.sqlite")
    library._local.con = None
    yield tmp_path / "roms"
    library._local.con = None


def _write(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 100)


def _rows() -> dict:
    return {r["name"]: dict(r) for r in library.db().execute("SELECT * FROM games")}


def test_disc_split():
    assert scanner.disc_split("D's Diner (Japan) (Disc B)") == ("D's Diner (Japan)", "(Disc B)", "")
    assert scanner.disc_split("C&C (USA) (Disc 2) (NOD)") == ("C&C (USA)", "(Disc 2)", "(NOD)")
    assert scanner.disc_split("FF7 (USA) (Disc 2 of 3)")[0] == "FF7 (USA)"
    # oznaczenia platformy / wydania — nie płyty
    for name in ("Akira (v1.3) (CD32)", "Battle Chess (CDTV)", "Dracula (Japan) (Disk Writer)", "A.E. (USA) (Side A)"):
        assert scanner.disc_split(name) is None, name


def test_letter_discs_and_disc_subtitles_merge(roms):
    d = roms / "3do"
    for x in "ABC":
        _write(d / f"D's Diner - The Director's Cut (Japan) (Disc {x}).chd")
    _write(d / "Command & Conquer (USA) (Disc 1) (GDI).chd")
    _write(d / "Command & Conquer (USA) (Disc 2) (NOD).chd")
    _write(d / "Supreme Warrior (Japan) (Disc 1) (Fire & Earth).chd")
    _write(d / "Supreme Warrior (USA) (Disc 1) (Fire & Earth).chd")      # inny region — osobna gra
    _write(d / "Defcon 5 (Europe).chd")
    _write(d / "Defcon 5 (Japan).chd")
    assert scanner.scan_system("3do", d) == 6
    rows = _rows()
    diner = rows["D's Diner - The Director's Cut (Japan)"]
    assert diner["multidisc"] == 1 and len(json.loads(diner["files"])) == 3
    cnc = rows["Command & Conquer (USA)"]
    assert cnc["multidisc"] == 1 and [f[0] for f in json.loads(cnc["files"])] == [
        "Command & Conquer (USA) (Disc 1) (GDI).chd", "Command & Conquer (USA) (Disc 2) (NOD).chd"]
    assert "Defcon 5 (Europe)" in rows and "Defcon 5 (Japan)" in rows


def test_already_merged_games_keep_their_names(roms):
    """Te same nazwy co dotąd → te same identyfikatory gier i zapisów (savesync.game_id)."""
    d = roms / "psx"
    _write(d / "Game A (USA) (Disc 1).chd")
    _write(d / "Game A (USA) (Disc 2).chd")
    _write(d / "Game B (USA) (Disc 1) (Rev 1).chd")
    _write(d / "Game B (USA) (Disc 2) (Rev 1).chd")
    scanner.scan_system("psx", d)
    assert {"Game A (USA)", "Game B (USA) (Rev 1)"} <= set(_rows())


def test_folder_systems_group_disc_files(roms):
    """Xbox 360: gry-katalogi i obok nich płyty .iso jednej gry."""
    d = roms / "xbox360"
    for n in range(1, 5):
        _write(d / f"Lost Odyssey (USA, Europe) (Disc {n}).iso")
    _write(d / "Halo 3 (USA)" / "default.xex")
    assert scanner.scan_system("xbox360", d) == 2
    lo = _rows()["Lost Odyssey (USA, Europe)"]
    assert lo["multidisc"] == 1 and len(json.loads(lo["files"])) == 4
