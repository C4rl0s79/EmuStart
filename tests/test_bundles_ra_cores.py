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
    assert (b.core_id, b.kind, b.folder, b.names) == ("libretro", "saves", user, ["GKBEAF"])
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
    # kartridż poza paczką: klient sprzed 0.55 zebrałby tylko <gra>.ram i wysłał paczkę bez kartridża
    assert [arc for _p, arc in b.files] == ["game.ram"]
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


# ── Flycast i LRPS2: osobna karta dla gry zamiast wspólnej w system\ ──

def opts(home: Path, text: str, global_opts: bool = True) -> None:
    _write(home / "retroarch.cfg", (home / "retroarch.cfg").read_bytes()
           + (b'global_core_options = "true"\n' if global_opts else b'global_core_options = "false"\n'))
    if text:
        _write(home / "retroarch-core-options.cfg", text.encode())


def test_core_defaults_once_in_the_file_retroarch_reads(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "flycast", "Flycast")
    _write(home / "info" / "pcsx2_libretro.info", b'corename = "LRPS2"\n')
    opts(home, 'reicast_internal_resolution = "1280x960"\n')
    a.core_defaults()
    text = (home / "retroarch-core-options.cfg").read_text()
    assert 'reicast_per_content_vmus = "VMU A1"' in text and 'pcsx2_shared_memory_cards = "disabled"' in text
    assert 'reicast_internal_resolution = "1280x960"' in text           # pozostałe opcje gracza zostają
    (home / "retroarch-core-options.cfg").write_text('reicast_per_content_vmus = "disabled"\n')
    a.core_defaults()                                                     # raz: zmiana gracza zostaje
    assert (home / "retroarch-core-options.cfg").read_text() == 'reicast_per_content_vmus = "disabled"\n'


def test_core_defaults_per_core_file_when_it_exists(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "flycast", "Flycast")
    opts(home, "", global_opts=False)
    _write(home / "config" / "Flycast" / "Flycast.opt", b'reicast_widescreen_hack = "enabled"\n')
    a.core_defaults()
    assert 'reicast_per_content_vmus = "VMU A1"' in (home / "config" / "Flycast" / "Flycast.opt").read_text()
    # LRPS2 bez własnego pliku: RetroArch czyta wtedy wspólny plik — tam trafia opcja
    assert 'pcsx2_shared_memory_cards = "disabled"' in (home / "retroarch-core-options.cfg").read_text()
    assert savebundles.ra_core_option(a, "flycast", "reicast_per_content_vmus") == "VMU A1"


@pytest.mark.parametrize("core, corename, shared, sfx, opt", [
    ("flycast", "Flycast", ("dc", "vmu_save_A1.bin"), ".A1.bin", 'reicast_per_content_vmus = "VMU A1"'),
    ("pcsx2", "LRPS2", ("pcsx2", "memcards", "Mcd001.ps2"), ".ps2", 'pcsx2_shared_memory_cards = "disabled"')])
def test_first_card_of_a_game_copied_from_shared(env, tmp_path, core, corename, shared, sfx, opt):
    home = tmp_path / "RetroArch"
    a = ra(home, core, corename, sort=False)
    rom = _write(tmp_path / "roms" / "Gra (USA).chd")
    game = {"es": "dc", "name": "Gra (USA)", "rel": "Gra (USA).chd"}
    src = _write(home.joinpath("system", *shared), b"WSPOLNA KARTA")
    assert savebundles.seed_from_shared(a, core, rom, game) == []      # opcja wyłączona — rdzeń używa wspólnej
    opts(home, opt + "\n")
    [dst] = savebundles.seed_from_shared(a, core, rom, game)
    assert dst == home / "saves" / f"Gra (USA){sfx}" and dst.read_bytes() == b"WSPOLNA KARTA"
    assert src.read_bytes() == b"WSPOLNA KARTA"                           # wspólna karta nietknięta
    dst.write_bytes(b"KARTA GRY")
    assert savebundles.seed_from_shared(a, core, rom, game) == []      # gra ma już kartę — bez kopii
    assert dst.read_bytes() == b"KARTA GRY"


# ── Xenia Edge: zapisy gry w content\<XUID profilu>\<Title ID>\00000001 (XUID inny na każdym urządzeniu) ──

X360 = {"es": "xbox360", "name": "Zone of the Enders HD (USA)", "rel": "Zone of the Enders HD (USA).iso"}


def xenia(home: Path, tmp: Path, xuid: str = "E030000000983487"):
    a = ra(home, "xenia_edge", "Xenia Edge")
    content = home / "system" / "Xenia-Edge" / "content"
    if xuid:
        _write(content / xuid / "FFFE07D1" / "00010000" / xuid / "Account", b"KONTO")
    rom = _write(tmp / "roms" / "Zone of the Enders HD (USA).iso")
    b = [b for b in savebundles.for_game(a, "xenia_edge", rom, X360) if b.kind == "memcards"]
    return a, content, rom, b


def test_xenia_game_saves_learned_title_without_updates_and_profile(env, tmp_path):
    home = tmp_path / "RetroArch"
    _a, content, _rom, [b] = xenia(home, tmp_path)
    x = content / "E030000000983487"
    assert (b.core_id, b.folder, b.files) == ("xenia_edge", x, [])
    savebundles.learn_snapshot([b])
    _write(x / "4B4E0842" / "00000001" / "zoe1_360" / "savegame.txt", b"ZAPIS")
    _write(x / "4B4E0842" / "Headers" / "00000001" / "zoe1_360.header", b"NAGLOWEK")
    _write(x / "4B4E0842" / "000B0000" / "aktualizacja.bin", b"TU")           # aktualizacja gry — nie zapis
    _write(x / "FFFE07D1" / "00010000" / "E030000000983487" / "4B4E0842.gpd", b"OSIAGNIECIA")   # profil
    assert savebundles.learn_after([b]) == ["4B4E0842"]
    assert [arc for _p, arc in b.files] == ["4B4E0842/00000001/zoe1_360/savegame.txt",
                                            "4B4E0842/Headers/00000001/zoe1_360.header"]
    with pytest.raises(ValueError):
        b.local_path("4B4E0842/000B0000/aktualizacja.bin")


def test_xenia_without_profile_no_bundle(env, tmp_path):
    assert xenia(tmp_path / "RetroArch", tmp_path, xuid="")[3] == []


def test_xenia_saves_reach_other_device_with_other_profile(net):
    cfgs, tmp = net
    _a, c1, _r, [b1] = xenia(tmp / "dev1" / "RetroArch", tmp / "dev1", "E030000000983487")
    savebundles.learn_snapshot([b1])
    _write(c1 / "E030000000983487" / "4B4E0842" / "00000001" / "zoe1_360" / "savegame.txt", b"ZAPIS PC")
    savebundles.learn_after([b1])
    assert syncclient.before(cfgs[PC], PC, [b1], X360).uploaded == 1

    from emustart import library
    library.meta_set(savebundles.LEARN_KEY, "{}")
    _a, c2, _r, [b2] = xenia(tmp / "dev2" / "RetroArch", tmp / "dev2", "E0300000AAAABBBB")
    assert syncclient.before(cfgs[TEL], TEL, [b2], X360).downloaded == 1
    assert (c2 / "E0300000AAAABBBB" / "4B4E0842" / "00000001" / "zoe1_360" / "savegame.txt").read_bytes() == b"ZAPIS PC"


def test_xenia_profile_of_one_player_on_shared_device(env, tmp_path):
    """content\ Xenii nie jest rozdzielony na graczy: profil (XUID) należy do pierwszego gracza, który go użył —
    inny gracz na tym urządzeniu nie dostaje paczki (nie nadpisze cudzych zapisów), dopóki nie ma własnego profilu."""
    _a, _c, _r, b = xenia(tmp_path / "RetroArch", tmp_path)
    assert savebundles.xenia_claim(b, 1) == b
    assert savebundles.xenia_claim(b, 1) == b
    assert savebundles.xenia_claim(b, 2) == []
    other = [savebundles.Bundle("retroarch", "snes9x", "saves", tmp_path, "x")]
    assert savebundles.xenia_claim(other, 2) == other           # inne paczki bez zmian


def test_xenia_download_accepts_only_title_folders(env, tmp_path):
    _a, _c, _r, [b] = xenia(tmp_path / "RetroArch", tmp_path)
    with pytest.raises(ValueError):
        b.local_path("FFFE07D1/00010000/E030000000983487/Account")
    assert "FFFE07D1" not in b.names


def test_xenia_pinned_profile_is_the_players(env, tmp_path):
    """Profil przypięty graczowi w panelu serwera należy do niego, także gdy wcześniej grał z niego inny gracz."""
    home = tmp_path / "RetroArch"
    _a, content, _r, [b] = xenia(home, tmp_path, "E030000000983487")
    assert savebundles.xenia_claim([b], 1)                                   # gracz 1 zajął profil
    [p] = savebundles.xenia_claim([b], 2, pinned="E030000000983487")         # przypięty graczowi 2
    assert p.folder == content / "E030000000983487"
    assert savebundles.xenia_claim([b], 1) == []
    assert savebundles.xenia_xuids(content) == ["E030000000983487"]


def test_xenia_several_profiles_no_bundle(env, tmp_path):
    """Kilka profili Xenii na urządzeniu: nie wiadomo, który zaloguje rdzeń — bez paczki (żadnych cudzych zapisów)."""
    home = tmp_path / "RetroArch"
    a, content, rom, _b = xenia(home, tmp_path, "E030000000983487")
    _write(content / "E0300000AAAABBBB" / "FFFE07D1" / "00010000" / "E0300000AAAABBBB" / "Account", b"KONTO 2")
    assert [b for b in savebundles.for_game(a, "xenia_edge", rom, X360) if b.kind == "memcards"] == []


def test_xenia_view_does_not_claim(env, tmp_path):
    _a, _c, _r, b = xenia(tmp_path / "RetroArch", tmp_path)
    assert savebundles.xenia_claim(b, 2, claim=False) == []                  # widok: nie zajmuje
    assert savebundles.xenia_claim(b, 1) == b                                # gra gracza 1 — jego profil
    assert savebundles.xenia_claim(b, 1, claim=False) == b


def test_emuprofiles_from_device(net):
    cfgs, _tmp = net
    assert syncclient.emu_report(cfgs[PC], PC, "xenia_edge", ["E030000000983487"]) == 1
    st = syncclient.emu_profiles(cfgs[PC], PC)
    assert [p["xuid"] for p in st["xenia_edge"]["reported"]] == ["E030000000983487"]
    assert st["xenia_edge"]["pinned"] == ""


def test_xenia_claim_leaves_other_objects(env):
    """Paczki z innych miejsc (np. zastępcze obiekty w testach Lumena) bez pola core_id — bez zmian."""
    class States:
        kind = "states"
    s = States()
    assert savebundles.xenia_claim([s], 1) == [s]



# ── ustalenia przeglądu kodu (code-review, poziom high) ──

def test_core_and_standalone_on_one_device_keep_each_others_saves(net):
    """Rdzeń Dolphin i samodzielny Dolphin na jednym urządzeniu: osobne stany (inne foldery), jedna paczka na
    serwerze — zapis przechodzi w obie strony, nic się nie nadpisuje bez wiedzy."""
    cfgs, tmp = net
    home = tmp / "dev1" / "RetroArch"
    a = ra(home, "dolphin", "Dolphin")
    rom = disc(tmp / "dev1" / "roms" / "Baten Kaitos (USA).iso", 0, b"GKBEAF")
    core_card = home / "saves" / "Dolphin" / "dolphin-emu" / "User" / "GC" / "USA" / "Card A" / "01-GKBE-BK.gci"
    _write(core_card, b"X")

    def core():
        return [b for b in savebundles.for_game(a, "dolphin", rom, GC) if b.emulator_id == "dolphin"]

    user = tmp / "dev1" / "Dolphin"

    def alone():
        return [savebundles.Bundle("dolphin", "", "saves", user, "GKBEAF", ["GKBEAF"]).collect()]

    assert syncclient.before(cfgs[PC], PC, core(), GC).uploaded == 1
    assert syncclient.before(cfgs[PC], PC, alone(), GC).downloaded == 1      # samodzielny dostaje zapis rdzenia
    sa_card = user / "GC" / "USA" / "Card A" / "01-GKBE-BK.gci"
    sa_card.write_bytes(b"S")
    syncclient.after(cfgs[PC], PC, alone(), GC)
    assert syncclient.before(cfgs[PC], PC, core(), GC).downloaded == 1       # rdzeń dostaje nowszy zapis
    assert core_card.read_bytes() == b"S"


def test_slot_store_keeps_learned_folders(env, tmp_path):
    from emustart import saveslots
    b = whd(ra(tmp_path / "RetroArch", "puae", "PUAE"), tmp_path)
    s = saveslots.store_bundle(1, "amiga", "g1", 2, b)
    assert s.local_path("MonkeyIsland/savegame.1") == s.folder / "MonkeyIsland" / "savegame.1"


def test_package_folder_of_another_game_rejected(env, tmp_path):
    a = ra(tmp_path / "RetroArch", "puae", "PUAE")
    savebundles._remember("puae", "amigawhdgames/Turrican", ["Turrican"])
    b = whd(a, tmp_path)
    with pytest.raises(ValueError):
        b.local_path("Turrican/hiscore")                                        # folder innej gry
    assert "Turrican" not in b.names


def test_bad_path_in_new_folder_not_learned(env, tmp_path):
    _a, _c, _r, [b] = xenia(tmp_path / "RetroArch", tmp_path)
    with pytest.raises(ValueError):
        b.local_path("4B4E0842/00000002/dlc.bin")                               # DLC, nie zapis
    assert "4B4E0842" not in b.names


def test_spec_keeps_learning(env, tmp_path):
    b = whd(ra(tmp_path / "RetroArch", "puae", "PUAE"), tmp_path)
    c = savebundles.from_spec(b.spec())
    assert (c.learn, c.learn_game) == ("dirs", "amigawhdgames/Monkey Island")
    assert c.local_path("MonkeyIsland/savegame.1").name == "savegame.1"


def test_shared_card_only_for_device_owner(env, tmp_path):
    home = tmp_path / "RetroArch"
    a = ra(home, "flycast", "Flycast", sort=False)
    rom = _write(tmp_path / "roms" / "Gra (USA).chd")
    _write(home / "system" / "dc" / "vmu_save_A1.bin", b"WSPOLNA KARTA")
    opts(home, 'reicast_per_content_vmus = "VMU A1"\n')
    game = {"es": "dc", "name": "Gra (USA)", "rel": "Gra (USA).chd"}
    assert savebundles.seed_from_shared(a, "flycast", rom, game, owner=False) == []
    assert savebundles.seed_from_shared(a, "flycast", rom, game) != []


def test_backup_keeps_subfolders(net):
    """Kopia zapasowa przed pobraniem: pliki o tej samej nazwie w różnych podfolderach — obie w kopii."""
    cfgs, tmp = net
    root = tmp / "dev1" / "PUAE" / "WHDSaves"
    _write(root / "A" / "highs", b"A1")
    _write(root / "B" / "highs", b"B1")
    b = savebundles.Bundle("retroarch", "puae", "memcards", root, "", ["A", "B"], learn="dirs", learn_game="g")
    assert syncclient.before(cfgs[PC], PC, [b.collect()], AMIGA).uploaded == 1
    tel = tmp / "dev2" / "PUAE" / "WHDSaves"
    _write(tel / "A" / "highs", b"A-stare")
    _write(tel / "B" / "highs", b"B-stare")
    b2 = savebundles.Bundle("retroarch", "puae", "memcards", tel, "", ["A", "B"], learn="dirs", learn_game="g").collect()
    r = syncclient.before(cfgs[TEL], TEL, [b2], AMIGA)
    if r.conflicts:
        syncclient.resolve(cfgs[TEL], TEL, AMIGA, r.conflicts, "remote")
    baks = list(syncclient._backup_dir(TEL).rglob("highs"))
    assert sorted(p.read_bytes() for p in baks) == [b"A-stare", b"B-stare"]



# ── drugi przegląd kodu (code-review, poziom medium) ──

def test_folder_from_rejected_package_not_learned(env, tmp_path):
    """Paczka odrzucona w połowie (zła ścieżka dalej): jej podfoldery nie stają się podfolderami gry."""
    b = whd(ra(tmp_path / "RetroArch", "puae", "PUAE"), tmp_path)
    assert b.local_path("GameX/save").name == "save"                        # sprawdzona, jeszcze nieprzyjęta
    with pytest.raises(ValueError):
        b.local_path("../poza")
    assert "GameX" not in b.names and savebundles.learned("puae", "amigawhdgames/Monkey Island") == []
    b.accept_pending()                                                       # dopiero zapis całej paczki
    assert "GameX" in b.names and savebundles.learned("puae", "amigawhdgames/Monkey Island") == ["GameX"]


def test_slot_backup_keeps_subfolders(env, tmp_path):
    from emustart import saveslots, syncclient
    root = tmp_path / "PUAE" / "WHDSaves"
    _write(root / "A" / "highs", b"A")
    _write(root / "B" / "highs", b"B")
    b = savebundles.Bundle("retroarch", "puae", "memcards", root, "", ["A", "B"], learn="dirs", learn_game="g").collect()
    saveslots._backup(1, b, "slot")
    assert sorted(p.read_bytes() for p in syncclient._backup_dir(1).rglob("highs")) == [b"A", b"B"]


def test_slot_store_knows_folders_learned_elsewhere(env, tmp_path):
    from emustart import saveslots
    b = whd(ra(tmp_path / "RetroArch", "puae", "PUAE"), tmp_path)
    savebundles._remember("puae", "amigawhdgames/Monkey Island", ["ZeSlotu2"])  # poznany przy pobraniu slotu
    s = saveslots.store_bundle(1, "amiga", "g1", 2, b)
    assert "ZeSlotu2" in s.names


def test_xenia_note_when_several_profiles(env, tmp_path):
    home = tmp_path / "RetroArch"
    a, content, _rom, _b = xenia(home, tmp_path, "E030000000983487")
    assert savebundles.xenia_note(a, "xenia_edge") == ""
    _write(content / "E0300000AAAABBBB" / "FFFE07D1" / "00010000" / "E0300000AAAABBBB" / "Account", b"KONTO 2")
    assert "bez synchronizacji" in savebundles.xenia_note(a, "xenia_edge")
    assert savebundles.xenia_note(a, "snes9x") == ""
