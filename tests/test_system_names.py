"""Nazwy systemów: kod ES, ES-DE, No-Intro / Redump, libretro i nazwy zwyczajowe — folder
z grami rozpoznany po każdej, motywy logo sprawdzane pod nazwami w stylu ES."""

import pytest

from emustart import logos, systems


@pytest.mark.parametrize("folder,es", [
    ("lynx", "lynx"), ("atarilynx", "lynx"), ("Atari Lynx", "lynx"), ("Atari - Lynx", "lynx"),
    ("Atari - Atari Lynx (LYX)", "lynx"), ("gc", "gamecube"), ("Nintendo - GameCube", "gamecube"),
    ("Super Famicom", "snes"), ("sfc", "snes"), ("Nintendo - Super Nintendo Entertainment System", "snes"),
    ("genesis", "megadrive"), ("Sega - Mega Drive - Genesis", "megadrive"), ("PS1", "psx"),
    ("Sony - PlayStation [zstd-19-128k]", "psx"), ("wonderswan", "wswan"),
    ("Bandai - WonderSwan Color", "wswanc"), ("megacd", "segacd"), ("fba", "fbneo"),
    ("Commodore 64", "c64"), ("Microsoft - Xbox 360", "x360"),
])
def test_folder_recognized_by_any_name(folder, es):
    assert systems.match_folder(folder) == es


def test_unknown_folders_stay_unknown():
    for name in ("Nintendo", "Sony", "Gry", "Archiwum"):
        assert systems.match_folder(name) is None


def test_theme_names_include_es_de_spelling():
    assert logos.theme_names("lynx")[:2] == ["lynx", "atarilynx"]
    assert "gc" in logos.theme_names("gamecube") and "genesis" in logos.theme_names("megadrive")
    assert "amiga" in logos.theme_names("amigawhdgames")                      # system „rodzica”
