"""Pady HID (nie-XInput) jako Gracz 1: układ przycisków, kolejność, RetroArch, SDL, Dolphin."""

from __future__ import annotations

from pathlib import Path

import pytest

from emustart import hidpad, ingame, pads, sdlpads, xinput

from test_core import env  # noqa: F401  (fixture)

# GameSir Nova 2 Lite w trybie Bluetooth HID (sprawdzony na żywo): 16 przycisków, X/Y/Z/Rz, hat
GAMESIR = hidpad.Pad(r"\\?\hid#{00001124-0000-1000-8000-00805f9b34fb}_vid&00023537_pid&100e&col01#8&1#{x}",
                     0x3537, 0x100E, "GameSir-Nova 2 Lite", 16, 11,
                     {0x30: (0, 255, 8), 0x31: (0, 255, 8), 0x32: (0, 255, 8), 0x35: (0, 255, 8), 0x39: (0, 7, 4)})


@pytest.fixture
def gamesir(monkeypatch):
    monkeypatch.setattr(hidpad, "pads", lambda: [GAMESIR])
    return GAMESIR


def test_layouts_by_position():
    assert GAMESIR.layout == "android" and GAMESIR.bluetooth
    assert hidpad.layout_for(0x054C, 14) == "sony" and hidpad.layout_for(0x057E, 14) == "nintendo"
    # GameSir: A, B, X, Y = 1, 2, 4, 5; LB, RB = 7, 8; Select, Start = 11, 12; L3, R3 = 14, 15
    m = hidpad.to_mask("android", [1, 2, 4, 5, 7, 8, 11, 12, 14, 15])
    assert m == (xinput.A | xinput.B | xinput.X | xinput.Y | xinput.LB | xinput.RB | xinput.BACK
                 | xinput.START | xinput.L3 | xinput.R3)
    assert hidpad.to_mask("android", [9, 10]) == hidpad.LT | hidpad.RT
    # DualShock 4: Krzyżyk (2) = A, Kwadrat (1) = X
    assert hidpad.to_mask("sony", [2]) == xinput.A and hidpad.to_mask("sony", [1]) == xinput.X
    # krzyżak (hat) i gałka
    assert hidpad.to_mask("android", [], hat=hidpad.hat_direction(2, 0, 7)) == xinput.RIGHT
    assert hidpad.hat_direction(8, 0, 7) is None and hidpad.hat_direction(15, 0, 7) is None
    assert hidpad.to_mask("android", [], x=0.0, y=-1.0) == xinput.UP


def test_pad_order_with_hid(gamesir):
    hid = gamesir.id
    lst = [{"slot": 0, "wireless": False}, {"slot": hid, "wireless": True, "hid": True}]
    assert pads.order({}, lst) == [0, hid]
    assert pads.is_identity([0, hid])                    # pad HID po padach XInput — bez przepinania
    assert not pads.is_identity([hid]) and not pads.is_identity([hid, 0])
    assert pads.order({"pad_order": {"mode": "manual", "manual": [hid]}}, lst) == [hid, 0]
    assert pads.order({"pad_order": {"mode": "wireless_first"}}, lst) == [hid, 0]
    assert pads.hid_pad(hid) is gamesir and pads.hid_pad(0) is None


def test_sdl_mapping_for_hid_pad(gamesir):
    lines = sdlpads.mapping(gamesir)
    assert [ln.split(",", 1)[0] for ln in lines] == ["03000000373500000e10000000000000",
                                                     "05000000373500000e10000000000000"]
    body = lines[0]
    for part in ("a:b0", "b:b1", "x:b3", "y:b4", "leftshoulder:b6", "rightshoulder:b7", "lefttrigger:b8",
                 "righttrigger:b9", "back:b10", "start:b11", "guide:b12", "leftstick:b13", "rightstick:b14",
                 "dpup:h0.1", "leftx:a0", "lefty:a1", "rightx:a2", "righty:a3", "platform:Windows"):
        assert f",{part}," in body
    e = sdlpads.env({"SDL_GAMECONTROLLERCONFIG": "własny"})
    assert e["SDL_GAMECONTROLLERCONFIG"].startswith("własny\n") and "GameSir-Nova 2 Lite" in e["SDL_GAMECONTROLLERCONFIG"]


def test_sdl_player_numbers_and_match(gamesir, monkeypatch):
    joys = sdlpads.player_ids([{"path": "XInput#0", "vid": 0x045E, "pid": 0x028E, "player": 0},
                               {"path": gamesir.path.upper(), "vid": 0x3537, "pid": 0x100E, "player": -1}])
    assert [j["sdl"] for j in joys] == [0, 1]
    assert sdlpads.index_of(joys, gamesir) == 1
    monkeypatch.setattr(sdlpads, "probe", lambda dll: joys)
    dll = Path(__file__)                                  # dowolny istniejący plik
    assert sdlpads.slots_for([gamesir.id, 0], dll) == [1, 0]


def test_sdl_ini_hid_player1_and_restore(env, tmp_path, gamesir):
    ini = tmp_path / "settings.ini"
    ini.write_text("[Pad1]\nCross = XInput-0/A\nUp = XInput-0/DPadUp\n", encoding="utf-8")
    restore = ingame._remap_sdl_ini(ini, "Pad{}", [1], hid={0})
    assert "Cross = SDL-1/A\nUp = SDL-1/DPadUp" in ini.read_text()
    restore()
    assert "Cross = XInput-0/A\nUp = XInput-0/DPadUp" in ini.read_text()


def test_retroarch_hid_player1_hotkeys(tmp_path, gamesir, monkeypatch):
    monkeypatch.setattr(hidpad, "dinput_devices", lambda: [(0x3537, 0x100E, "Pad DirectInput")])
    home = tmp_path / "ra"
    (home / "autoconfig" / "dinput").mkdir(parents=True)
    (home / "retroarch.cfg").write_text('input_enable_hotkey_btn = "7"\ninput_screenshot_btn = "9"\n'
                                        'input_state_slot_increase_btn = "h0up"\n', encoding="utf-8")
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    ra.apply_policy({"input_enable_hotkey_btn": "4", "input_menu_toggle_btn": "0"})
    args = ra.launch_args(tmp_path / "run", False, [gamesir.id])
    text = Path(args[args.index("--appendconfig") + 1]).read_text()
    assert 'input_player1_joypad_index = "0"' in text
    # numeracja XInput → przyciski GameSira (DirectInput = numer HID − 1): LB 7→6, A 1→0, R3 15→14
    assert 'input_enable_hotkey_btn = "6"' in text and 'input_enable_hotkey_btn = "4"' not in text
    assert 'input_menu_toggle_btn = "0"' in text and 'input_screenshot_btn = "14"' in text
    assert "input_state_slot_increase_btn" not in text              # krzyżak (hat) bez zmian
    prof = (home / "autoconfig" / "dinput" / "EmuStart GameSir-Nova 2 Lite.cfg").read_text()
    for line in ('input_vendor_id = "13623"', 'input_device = "Pad DirectInput"', 'input_b_btn = "0"',
                 'input_a_btn = "1"', 'input_y_btn = "3"', 'input_x_btn = "4"', 'input_l_btn = "6"',
                 'input_start_btn = "11"', 'input_up_btn = "h0up"', 'input_r_x_plus_axis = "+2"',
                 'input_r_y_minus_axis = "-5"'):
        assert line in prof
    # profil tylko raz; pady XInput — jak dotąd
    args = ra.launch_args(tmp_path / "run", False, [0])
    text = Path(args[args.index("--appendconfig") + 1]).read_text()
    assert 'input_enable_hotkey_btn = "4"' in text and 'input_player1_joypad_index = "0"' in text


def test_retroarch_keeps_existing_autoconfig(tmp_path, gamesir, monkeypatch):
    monkeypatch.setattr(hidpad, "dinput_devices", lambda: [(0x045E, 0x028E, "X"), (0x3537, 0x100E, "G")])
    home = tmp_path / "ra"
    d = home / "autoconfig" / "dinput"
    d.mkdir(parents=True)
    (d / "Nova.cfg").write_text('input_vendor_id = "13623"\ninput_product_id = "4110"\n', encoding="utf-8")
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    args = ra.launch_args(tmp_path / "run", False, [gamesir.id, 0])
    text = Path(args[args.index("--appendconfig") + 1]).read_text()
    assert 'input_player1_joypad_index = "1"' in text and 'input_player2_joypad_index = "0"' in text
    assert [f.name for f in d.iterdir()] == ["Nova.cfg"]


def test_dolphin_hid_player1(env, tmp_path, gamesir):
    home = tmp_path / "dolphin"
    (home / "User" / "Config").mkdir(parents=True)
    (home / "portable.txt").write_text("")
    ini = home / "User" / "Config" / "GCPadNew.ini"
    ini.write_text("[GCPad1]\nDevice = XInput/0/Gamepad\nButtons/A = `Button A`\nButtons/B = `Button B`\n"
                   "Main Stick/Up = `Left Y+`\n", encoding="utf-8")
    d = ingame.Dolphin(str(home / "Dolphin.exe"))
    restore = d.remap_pads([gamesir.id])
    text = ini.read_text()
    assert "Device = SDL/0/GameSir-Nova 2 Lite" in text
    assert "Buttons/A = `Button S`" in text and "Buttons/B = `Button E`" in text and "`Left Y+`" in text
    restore()
    assert "Device = XInput/0/Gamepad" in ini.read_text() and "`Button A`" in ini.read_text()
