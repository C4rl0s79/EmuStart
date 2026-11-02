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


def saves_bundle(a, core: str, rom: Path, game: dict):
    return next(b for b in savebundles.for_game(a, core, rom, game) if (b.emulator_id, b.kind) == ("retroarch", "saves"))


def test_fbneo_nvram_in_fbneo_subfolder(env, tmp_path):
    """FBNeo (źródło rdzenia): NVRAM w <zapisy>/fbneo/<nazwa setu>.fs — osobny plik dla gry."""
    home = tmp_path / "RetroArch"
    a = ra(home, "fbneo", "FinalBurn Neo")
    rom = _write(tmp_path / "roms" / "mslug.zip")
    sub = home / "saves" / "FinalBurn Neo" / "fbneo"
    _write(sub / "mslug.fs", b"NVRAM")
    _write(sub / "kof98.fs", b"INNA GRA")
    b = saves_bundle(a, "fbneo", rom, {"es": "fbneo", "name": "mslug", "rel": "mslug.zip"})
    assert b.folder == sub
    assert [arc for _p, arc in b.files] == ["game.fs"]


def test_kronos_backup_ram_and_cartridge(env, tmp_path):
    """Kronos (ciągi w pliku rdzenia): <zapisy>/kronos/saturn/<gra>.ram i <gra>-ext1M.ram; z opcją
    „Share saves with beetle” — pliki Beetle wprost w folderze zapisów."""
    home = tmp_path / "RetroArch"
    a = ra(home, "kronos", "Kronos")
    rom = _write(tmp_path / "roms" / "Resident Evil (USA).cue")
    game = {"es": "saturn", "name": "Resident Evil (USA)", "rel": "Resident Evil (USA).cue"}
    sub = home / "saves" / "Kronos" / "kronos" / "saturn"
    _write(sub / "Resident Evil (USA).ram", b"BUP")
    _write(sub / "Resident Evil (USA)-ext1M.ram", b"CART")
    _write(sub / "Inna gra.ram", b"INNA")
    b = saves_bundle(a, "kronos", rom, game)
    assert b.folder == sub
    assert sorted(arc for _p, arc in b.files) == ["game-ext1M.ram", "game.ram"]
    assert b.local_path("game-ext1M.ram") == sub / "Resident Evil (USA)-ext1M.ram"
    _write(home / "retroarch-core-options.cfg", b'kronos_use_beetle_saves = "enabled"\n')
    _write(home / "retroarch.cfg", (home / "retroarch.cfg").read_bytes() + b'global_core_options = "true"\n')
    assert saves_bundle(a, "kronos", rom, game).folder == home / "saves" / "Kronos"


@pytest.mark.parametrize("name, arc", [
    ("Shenmue (USA).A1.bin", "game.A1.bin"),     # Flycast: karta VMU gry (nazwa starsza, od pliku gry)
    ("Shenmue (USA).ps2", "game.ps2"),           # LRPS2: karta gry przy wyłączonych wspólnych kartach
    ("Shenmue (USA).nvr", "game.nvr")])          # PUAE: NVRAM CD32 / CDTV
def test_more_save_file_kinds(env, tmp_path, name, arc):
    home = tmp_path / "RetroArch"
    a = ra(home, "flycast", "Flycast", sort=False)
    rom = _write(tmp_path / "roms" / "Shenmue (USA).chd")
    _write(home / "saves" / name, b"ZAPIS")
    _write(home / "saves" / "Shenmue (USA).bin", b"NIE ZAPIS")       # sam .bin bez numeru karty — nie
    b = saves_bundle(a, "flycast", rom, {"es": "dc", "name": "Shenmue (USA)", "rel": "Shenmue (USA).chd"})
    assert [x for _p, x in b.files] == [arc]


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


# ── nazwy poznawane po grze (Flycast: ID gry w nazwie karty, PUAE: podfolder WHDSaves) ──

DC = {"es": "dc", "name": "Shenmue (USA)", "rel": "Shenmue (USA).chd"}
AMIGA = {"es": "amigawhdgames", "name": "Monkey Island", "rel": "Monkey Island.lha"}


def test_flycast_card_named_by_game_id_learned_after_game(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "flycast", "Flycast", sort=False)
    rom = _write(tmp_path / "roms" / "Shenmue (USA).chd")
    saves = home / "saves"
    _write(saves / "MK-51099.A1.bin", b"INNA GRA")                     # karta innej gry — już była
    b = saves_bundle(a, "flycast", rom, DC)
    savebundles.learn_snapshot([b])
    _write(saves / "MK-51059.A1.bin", b"KARTA GRY")                     # rdzeń zapisał kartę pod ID gry
    assert savebundles.learn_after([b]) == ["MK-51059"]
    assert b.stem == "MK-51059" and [(p.name, arc) for p, arc in b.files] == [("MK-51059.A1.bin", "game.A1.bin")]
    # zapamiętane: następna gra od razu zna nazwę, pobranie z serwera trafia w kartę, którą czyta rdzeń
    b = saves_bundle(a, "flycast", rom, DC)
    assert b.stem == "MK-51059" and b.local_path("game.A1.bin") == saves / "MK-51059.A1.bin"


def test_learning_skips_names_of_other_games(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "flycast", "Flycast", sort=False)
    rom1, rom2 = _write(tmp_path / "roms" / "Shenmue (USA).chd"), _write(tmp_path / "roms" / "Inna (USA).chd")
    other = {"es": "dc", "name": "Inna (USA)", "rel": "Inna (USA).chd"}
    b = saves_bundle(a, "flycast", rom2, other)
    savebundles.learn_snapshot([b])
    _write(home / "saves" / "MK-51099.A1.bin", b"INNA")
    assert savebundles.learn_after([b]) == ["MK-51099"]
    b = saves_bundle(a, "flycast", rom1, DC)
    savebundles.learn_snapshot([b])
    _write(home / "saves" / "MK-51099.A1.bin", b"INNA, zmieniona")    # zmiana karty innej gry — nie ta gra
    assert savebundles.learn_after([b]) == [] and b.files == []


def whd(a, tmp: Path) -> savebundles.Bundle:
    rom = _write(tmp / "roms" / "Monkey Island.lha")
    return next(b for b in savebundles.for_game(a, "puae", rom, AMIGA) if b.kind == "memcards")


def test_puae_whdsaves_folder_of_the_game_learned(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "puae", "PUAE")
    root = home / "saves" / "PUAE" / "WHDSaves"
    _write(root / "Turrican" / "hiscore", b"INNA GRA")
    b = whd(a, tmp_path)
    assert (b.emulator_id, b.core_id, b.folder, b.files) == ("retroarch", "puae", root, [])
    savebundles.learn_snapshot([b])
    _write(root / "MonkeyIsland" / "savegame.1", b"ZAPIS")
    assert savebundles.learn_after([b]) == ["MonkeyIsland"]
    assert [arc for _p, arc in b.files] == ["MonkeyIsland/savegame.1"]
    assert [arc for _p, arc in whd(a, tmp_path).files] == ["MonkeyIsland/savegame.1"]
    with pytest.raises(ValueError):
        b.local_path("../poza/plik")


def test_puae_whdsaves_reach_second_device(net):
    """Drugie urządzenie nie zna jeszcze podfolderu gry — przyjmuje go z paczki tej gry i zapamiętuje."""
    cfgs, tmp = net
    pc, tel = tmp / "dev1" / "RetroArch", tmp / "dev2" / "RetroArch"
    a1, a2 = ra(pc, "puae", "PUAE"), ra(tel, "puae", "PUAE")
    b = whd(a1, tmp / "dev1")
    savebundles.learn_snapshot([b])
    _write(pc / "saves" / "PUAE" / "WHDSaves" / "MonkeyIsland" / "savegame.1", b"ZAPIS")
    savebundles.learn_after([b])
    assert syncclient.before(cfgs[PC], PC, [b], AMIGA).uploaded == 1

    from emustart import library
    library.meta_set(savebundles.LEARN_KEY, "{}")     # jedna baza w teście — drugie urządzenie nie zna nazw
    b2 = whd(a2, tmp / "dev2")
    assert b2.names == []
    assert syncclient.before(cfgs[TEL], TEL, [b2], AMIGA).downloaded == 1
    savebundles.remember_downloaded([b2])
    assert (tel / "saves" / "PUAE" / "WHDSaves" / "MonkeyIsland" / "savegame.1").read_bytes() == b"ZAPIS"
    assert [arc for _p, arc in whd(a2, tmp / "dev2").files] == ["MonkeyIsland/savegame.1"]
