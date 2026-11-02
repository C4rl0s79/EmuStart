"""Rdzenie RetroArcha, które trzymają zapisy w podfolderach `saves\\` (Dolphin: dolphin-emu/User, PPSSPP:
PSP/SAVEDATA) — paczki jak dla samodzielnych emulatorów, ta sama paczka na serwerze (ten sam format zapisu),
ID gry GC/Wii z nagłówka pliku gry (bez DolphinTool samodzielnego Dolphina)."""

from __future__ import annotations

from pathlib import Path

import pytest

from emustart import ingame, savebundles, syncclient

from test_core import _write, env  # noqa: F401  (fixture)
from test_savesync_server import srv  # noqa: F401  (fixture)
from test_syncclient import PC, TEL, net  # noqa: F401  (fixture)

GC = {"es": "gc", "name": "Baten Kaitos (USA)", "rel": "Baten Kaitos (USA).iso"}
PSP = {"es": "psp", "name": "Final Fantasy Tactics (USA)", "rel": "Final Fantasy Tactics (USA).iso",
       "serial": "ULUS-10297"}


def ra(home: Path, core: str, corename: str, sort: bool = True) -> ingame.RetroArch:
    """RetroArch z zapisami w `saves\\` (sortowanie po rdzeniu jak u użytkownika)."""
    _write(home / "retroarch.cfg", (f'savefile_directory = ":\\saves"\n'
                                    f'sort_savefiles_enable = "{"true" if sort else "false"}"\n'
                                    f'sort_savefiles_by_content_enable = "false"\n').encode())
    _write(home / "info" / f"{core}_libretro.info", f'corename = "{corename}"\n'.encode())
    return ingame.RetroArch(str(home / "retroarch.exe"))


def disc(p: Path, offset: int, gid: bytes, magic: bytes = b"") -> Path:
    data = bytearray(offset + 0x100)
    data[:len(magic)] = magic
    data[offset:offset + 6] = gid
    return _write(p, bytes(data))


@pytest.mark.parametrize("name, offset, magic", [
    ("g.iso", 0, b""), ("g.gcm", 0, b""), ("g.rvz", 0x58, b"RVZ\x01"), ("g.wia", 0x58, b"WIA\x01"),
    ("g.wbfs", 0x200, b"WBFS"), ("g.ciso", 0x8000, b"CISO")])
def test_disc_id_from_header(tmp_path, name, offset, magic):
    assert savebundles.disc_id(disc(tmp_path / name, offset, b"GKBEAF", magic)) == "GKBEAF"


def test_disc_id_unknown_or_broken(tmp_path):
    assert savebundles.disc_id(_write(tmp_path / "g.gcz", b"\x01\xc0\x0b\xb1" + b"\0" * 64)) == ""
    assert savebundles.disc_id(_write(tmp_path / "g.iso", b"\0" * 64)) == ""
    assert savebundles.disc_id(tmp_path / "brak.iso") == ""


def test_ra_dolphin_core_saves_bundle(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "dolphin", "Dolphin")
    rom = disc(tmp_path / "roms" / "Baten Kaitos (USA).iso", 0, b"GKBEAF")
    user = home / "saves" / "Dolphin" / "dolphin-emu" / "User"
    _write(user / "GC" / "USA" / "Card A" / "01-GKBE-BatenKaitos.gci", b"KARTA")
    _write(user / "GC" / "USA" / "Card A" / "01-GXXE-Inna.gci", b"INNA GRA")
    _write(user / "Wii" / "title" / "00010000" / "524d4745" / "data" / "x.bin", b"NIE TA GRA")
    bs = [b for b in savebundles.for_game(a, "dolphin", rom, GC) if b.emulator_id == "dolphin"]
    assert len(bs) == 1
    b = bs[0]
    assert (b.core_id, b.kind, b.folder, b.names) == ("", "saves", user, ["GKBEAF"])
    assert [arc for _p, arc in b.files] == ["GC/USA/Card A/01-GKBE-BatenKaitos.gci"]
    # zwykłe paczki RetroArcha (zapisy nazwane jak gra, stany) zostają
    assert {(x.emulator_id, x.kind) for x in savebundles.for_game(a, "dolphin", rom, GC)} >= {
        ("retroarch", "saves"), ("retroarch", "states")}


def test_ra_core_folder_without_sorting_and_existing_wins(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "ppsspp", "PPSSPP", sort=False)
    rom = _write(tmp_path / "roms" / "Final Fantasy Tactics (USA).iso")
    b = next(b for b in savebundles.for_game(a, "ppsspp", rom, PSP) if b.emulator_id == "ppsspp")
    assert b.folder == home / "saves" / "PSP" / "SAVEDATA"
    # sortowanie po rdzeniu włączone później: zapisy już leżą bez podfolderu rdzenia — tam ich szukamy
    a = ra(home, "ppsspp", "PPSSPP", sort=True)
    _write(home / "saves" / "PSP" / "SAVEDATA" / "ULUS10297DATA00" / "DATA.BIN", b"FFT")
    b = next(b for b in savebundles.for_game(a, "ppsspp", rom, PSP) if b.emulator_id == "ppsspp")
    assert b.folder == home / "saves" / "PSP" / "SAVEDATA"
    assert [arc for _p, arc in b.files] == ["ULUS10297DATA00/DATA.BIN"]


def test_ra_dolphin_core_save_reaches_standalone_dolphin(net):
    """Ten sam format zapisu — rdzeń na PC, samodzielny Dolphin na drugim urządzeniu: jedna paczka."""
    cfgs, tmp = net
    home = tmp / "dev1" / "RetroArch"
    a = ra(home, "dolphin", "Dolphin")
    rom = disc(tmp / "dev1" / "roms" / "Baten Kaitos (USA).iso", 0, b"GKBEAF")
    _write(home / "saves" / "Dolphin" / "dolphin-emu" / "User" / "GC" / "USA" / "Card A" / "01-GKBE-BK.gci", b"Z RDZENIA")
    core = [b for b in savebundles.for_game(a, "dolphin", rom, GC) if b.emulator_id == "dolphin"]
    assert syncclient.before(cfgs[PC], PC, core, GC).uploaded == 1

    user2 = tmp / "dev2" / "Dolphin"
    standalone = [savebundles.Bundle("dolphin", "", "saves", user2, "GKBEAF", ["GKBEAF"]).collect()]
    assert syncclient.before(cfgs[TEL], TEL, standalone, GC).downloaded == 1
    assert (user2 / "GC" / "USA" / "Card A" / "01-GKBE-BK.gci").read_bytes() == b"Z RDZENIA"
