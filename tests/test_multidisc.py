"""Łączenie gier wielopłytowych w jedną pozycję (emustart/scanner.py): płyty oznaczone
literami, podtytuł płyty po numerze, płyty .iso Xboksa 360 obok gier-folderów, wyjątki
(Amiga CD32 / CDTV, „Disk Writer”, dema Amigi) i playlista tylko z plików płyt."""

from __future__ import annotations

import json

from emustart import library, scanner

from test_core import _write, env  # noqa: F401  (fixture)


def rows(es: str) -> dict:
    return {r["name"]: dict(r, files=json.loads(r["files"]))
            for r in library.db().execute("SELECT * FROM games WHERE es=?", (es,))}


def names(files) -> list:
    return [f[0] for f in files]


def test_discs_marked_with_letters(env):
    _cfg, roms, _ = env
    psx = roms / "psx"
    for d in "CAB":                                    # kolejność płyt A, B, C — nie kolejność plików
        _write(psx / f"Gra (USA) (Disc {d}).chd")
    assert scanner.scan_system("psx", psx) == 1
    g = rows("psx")["Gra (USA)"]
    assert g["multidisc"] == 1 and g["rel"] == "Gra (USA) (Disc A).chd"
    assert names(g["files"]) == [f"Gra (USA) (Disc {d}).chd" for d in "ABC"]


def test_disc_subtitle_does_not_split_set(env):
    _cfg, roms, _ = env
    psx = roms / "psx"
    _write(psx / "Gra (Disc 1) (Part One).cue", b'FILE "Gra (Disc 1) (Part One) (Track 1).bin" BINARY\n')
    _write(psx / "Gra (Disc 1) (Part One) (Track 1).bin")
    _write(psx / "Gra (Disc 2) - Second Act.cue", b'FILE "Gra (Disc 2) - Second Act.bin" BINARY\n')
    _write(psx / "Gra (Disc 2) - Second Act.bin")
    _write(psx / "Gra (Disc 2) - Second Act (Track 2).wav")
    assert scanner.scan_system("psx", psx) == 1
    g = rows("psx")["Gra"]                             # tytuł = część przed „(Disc …)”
    assert g["multidisc"] == 1 and g["rel"] == "Gra (Disc 1) (Part One).cue" and len(g["files"]) == 4
    # playlista: tylko płyty, bez .bin / .wav
    assert scanner.playlist_entries(names(g["files"])) == ["Gra (Disc 1) (Part One).cue", "Gra (Disc 2) - Second Act.cue"]


def test_existing_first_disc_keeps_its_id(env):
    """Nowa pozycja zestawu = dotychczasowa pierwsza płyta (id zostaje); druga płyta znika
    jako osobna gra i jest plikiem zestawu."""
    _cfg, roms, _ = env
    psx = roms / "psx"
    _write(psx / "Gra (Disc A).chd")
    assert scanner.scan_system("psx", psx) == 1
    first = rows("psx")["Gra (Disc A)"]["id"]           # sama płyta A — osobna gra, nazwa bez zmian
    _write(psx / "Gra (Disc B).chd")
    assert scanner.scan_system("psx", psx) == 1
    g = rows("psx")["Gra"]
    assert g["id"] == first and names(g["files"]) == ["Gra (Disc A).chd", "Gra (Disc B).chd"]


def test_names_of_already_merged_sets_unchanged(env):
    """Zestawy łączone dotąd („(Disc N)” z tym samym dopiskiem) — ta sama nazwa co wcześniej."""
    _cfg, roms, _ = env
    psx = roms / "psx"
    _write(psx / "Final Fantasy VII (USA) (Disc 1).chd")
    _write(psx / "Final Fantasy VII (USA) (Disc 2).chd")
    _write(psx / "Riven (Disc 1) (USA).chd")
    _write(psx / "Riven (Disc 2) (USA).chd")
    _write(psx / "Riven (Disc 1) (Europe).chd")         # ten sam numer płyty — inna wersja, osobno
    _write(psx / "Riven (Disc 2) (Europe).chd")
    scanner.scan_system("psx", psx)
    r = rows("psx")
    assert set(r) == {"Final Fantasy VII (USA)", "Riven (USA)", "Riven (Europe)"}
    assert all(g["multidisc"] == 1 and len(g["files"]) == 2 for g in r.values())


def test_x360_iso_discs_next_to_folder_games(env):
    _cfg, roms, _ = env
    x = roms / "xbox360"
    _write(x / "Halo 3 (GOD)" / "4D5307E6" / "00007000" / "data")
    _write(x / "Lost Odyssey (Disc 2).iso")
    _write(x / "Lost Odyssey (Disc 1).iso")
    _write(x / "Gears (Disc 1)" / "default.xex")         # folder z „(Disc 1)” — zostaje folderem
    _write(x / "Single.iso")
    assert scanner.scan_system("xbox360", x) == 4
    r = rows("xbox360")
    lo = r["Lost Odyssey"]
    assert lo["multidisc"] == 1 and names(lo["files"]) == ["Lost Odyssey (Disc 1).iso", "Lost Odyssey (Disc 2).iso"]
    assert r["Halo 3 (GOD)"]["is_dir"] == 1 and r["Gears (Disc 1)"]["is_dir"] == 1
    assert r["Single"]["multidisc"] == 0


def test_amiga_exceptions_not_merged(env):
    _cfg, roms, _ = env
    am = roms / "amiga"
    _write(am / "Gra (Disk 1).adf")                     # dyskietki jednej gry — łączą się
    _write(am / "Gra (Disk 2).adf")
    _write(am / "Kompilacja (CD32) (Disk 1).adf")       # CD32 / CDTV — osobne programy
    _write(am / "Kompilacja (CD32) (Disk 2).adf")
    _write(am / "Musik (CDTV) (Disk 1).adf")
    _write(am / "Musik (CDTV) (Disk 2).adf")
    _write(am / "Disk Writer (Disk 1).adf")
    _write(am / "Disk Writer (Disk 2).adf")
    scanner.scan_system("amiga", am)
    r = rows("amiga")
    assert r["Gra"]["multidisc"] == 1
    for n in ("Kompilacja (CD32) (Disk 1)", "Kompilacja (CD32) (Disk 2)", "Musik (CDTV) (Disk 1)",
              "Disk Writer (Disk 1)", "Disk Writer (Disk 2)"):
        assert r[n]["multidisc"] == 0
    demos = roms / "amigawhddemos"
    _write(demos / "Demo (Disk 1).lha")
    _write(demos / "Demo (Disk 2).lha")
    assert scanner.scan_system("amigawhddemos", demos) == 2


def test_playlist_entries():
    assert scanner.playlist_entries(["A (Disc 1).ccd", "A (Disc 1).img", "A (Disc 1).sub",
                                     "A (Disc 2).chd", "A (Disc 3).iso"]) == \
        ["A (Disc 1).ccd", "A (Disc 2).chd", "A (Disc 3).iso"]
    assert scanner.playlist_entries(["B (Disc 1).bin", "B (Disc 2).bin"]) == ["B (Disc 1).bin", "B (Disc 2).bin"]
    assert scanner.disc_of("Gra (USA) (Disc B) (Rev 1)") == ("Gra (USA)", 2, "(Rev 1)")
    assert scanner.disc_of("Discworld (USA)") is None and scanner.disc_of("(Disc 1)") is None
