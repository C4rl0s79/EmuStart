"""Rodzina platformy (systems.family) — jawna mapa, nieznany folder → other."""

from emustart import systems


def test_known_families():
    assert systems.family("amiga") == "computer"
    assert systems.family("c64") == "computer"
    assert systems.family("zxspectrum") == "computer"
    assert systems.family("atarist") == "computer"
    assert systems.family("ps2") == "console"
    assert systems.family("gba") == "console"          # przenośne to konsole
    assert systems.family("psp") == "console"
    assert systems.family("mame") == "arcade"
    assert systems.family("fbneo") == "arcade"
    assert systems.family("neogeo") == "arcade"


def test_case_insensitive_and_unknown():
    assert systems.family("SNESMSU1") == "console"
    assert systems.family("snesmsu1") == "console"
    assert systems.family("PS2") == "console"
    assert systems.family("jakis_nowy_system") == "other"
    assert systems.family("") == "other"


def test_every_known_system_has_a_family():
    for es in systems.SYSTEMS:
        assert systems.family(es) in systems.FAMILIES
        assert systems.family(es) != "pc"               # pc = Steam / GOG, nie emulacja
