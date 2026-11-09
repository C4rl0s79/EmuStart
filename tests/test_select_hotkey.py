"""Skróty pada w grze: przytrzymany Select zamiast LB."""

from emustart import emupolicy, hotkey, ingame, xinput


def _run(monkeypatch, frames):
    seq = iter(frames)
    acts = []

    class Proc:
        def poll(self):
            return None if seq.__length_hint__() else 0

    m = hotkey.GameMenu.__new__(hotkey.GameMenu)
    m.s = type("S", (), {"proc": Proc()})()
    m.adapter = type("A", (), {"menu_key": staticmethod(lambda: "ESCAPE")})()
    m.open, m.busy = False, False
    m._shortcut = acts.append
    monkeypatch.setattr(hotkey, "_buttons", lambda: next(seq))
    monkeypatch.setattr(hotkey.time, "sleep", lambda s: None)
    m._loop()
    return acts


def test_select_is_the_modifier_and_lb_no_longer_is(monkeypatch):
    sel = xinput.BACK
    assert _run(monkeypatch, [sel, sel | xinput.A, sel, sel | xinput.RB, 0]) == ["menu", "ff"]
    assert _run(monkeypatch, [xinput.LB, xinput.LB | xinput.A, xinput.LB, xinput.LB | xinput.Y, 0]) == []


def test_retroarch_old_lb_setup_migrated_once(tmp_path):
    """Znacznik poprzedniej wersji (LB) nie blokuje wpisania Select; potem zmiana gracza zostaje."""
    home = tmp_path / "RetroArch"
    home.mkdir()
    (home / "retroarch.cfg").write_text('input_enable_hotkey_btn = "4"\n', encoding="utf-8")
    (home / "emustart-skroty-lb-2.txt").write_text("x", encoding="utf-8")
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    ra.launch_args(tmp_path / "run", resume=False)
    assert ingame._cfg_map(home / "retroarch.cfg")["input_enable_hotkey_btn"] == '"7"'
    ingame.ini_set(home / "retroarch.cfg", {(None, "input_enable_hotkey_btn"): '"5"'})
    ra.launch_args(tmp_path / "run2", resume=False)
    assert ingame._cfg_map(home / "retroarch.cfg")["input_enable_hotkey_btn"] == '"5"'


def test_panel_policy_saved_with_lb_becomes_select():
    saved = {"emu_policy": {"retroarch": {"input_enable_hotkey_btn": "4", "input_save_state_btn": "3"}}}
    got = emupolicy.effective(saved)["retroarch"]
    assert got == {"input_enable_hotkey_btn": "7", "input_save_state_btn": "3"}
    assert saved["emu_policy"]["retroarch"]["input_enable_hotkey_btn"] == "4"
    assert emupolicy.effective({})["retroarch"]["input_enable_hotkey_btn"] == "7"


def test_dolphin_conflicts_follow_select():
    assert ingame._dolphin_hotkey_conflict("@(`Back`+`Button Y`)")
    assert ingame._dolphin_hotkey_conflict("`XInput/0/Gamepad:Back`")
    assert not ingame._dolphin_hotkey_conflict("@(`Shoulder L`+`Button Y`)")
    assert not ingame._dolphin_hotkey_conflict("@(`Back`+`Start`)")
