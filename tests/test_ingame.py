"""Menu w grze: odczyt skrótów z ustawień emulatorów, quicksave i wyjdź,
jednorazowe wczytanie stanu wznowienia przy następnym starcie."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from emustart import hotkey, ingame, launcher, library, paths, scanner

from test_core import _write, env  # noqa: F401  (fixture)


def test_duckstation_keys_from_ini(tmp_path):
    (tmp_path / "duckstation-qt.exe").write_bytes(b"")
    (tmp_path / "portable.txt").write_text("")
    (tmp_path / "settings.ini").write_text(
        "[Hotkeys]\nLoadSelectedSaveState = Keyboard/F5\nSaveSelectedSaveState = Keyboard/Shift & F6\n")
    a = ingame.adapter_for(str(tmp_path / "duckstation-qt.exe"))
    assert a.family == "duckstation"
    assert a.load_key() == "F5" and a.save_key() == "SHIFT+F6"
    assert a.resume_args(Path("s.sav")) == ["-statefile", "s.sav"]


def test_ppsspp_android_keycodes(tmp_path):
    ini = tmp_path / "memstick" / "PSP" / "SYSTEM" / "controls.ini"
    _write(ini, b"[ControlMapping]\nSave State = 10-4034,1-133\n")
    a = ingame.adapter_for(str(tmp_path / "PPSSPPWindows64.exe"))
    assert a.save_key() == "F3" and a.load_key() == "F4"     # F4 = domyślny


def test_retroarch_appendconfig(tmp_path):
    a = ingame.adapter_for(str(tmp_path / "retroarch.exe"))
    args = a.launch_args(tmp_path / "run", resume=True)
    text = Path(args[1]).read_text()
    assert args[0] == "--appendconfig"
    assert 'network_cmd_enable = "true"' in text and 'savestate_auto_load = "true"' in text
    assert 'config_save_on_exit = "false"' in text


class FakeAdapter(ingame.Adapter):
    family = "fake"
    state_glob = "*.st"

    def __init__(self, state_dir: Path):
        super().__init__(sys.executable)
        self.dir = state_dir
        self.saves = 0

    def save_key(self):
        return "F1"

    def load_key(self):
        return "F2"

    def state_dirs(self):
        return [self.dir]

    def resume_args(self, state):
        # atrapą jest python.exe: przyjmie to jako -X, a skrypt odczyta sys._xoptions
        return ["-X", f"resume={state}"]

    def pause(self):
        pass

    def save_state(self):
        self.saves += 1
        _write(self.dir / "slot1.st", b"STATE")

    def quit(self, proc):
        proc.terminate()


class FakeUI:
    def __init__(self):
        self.events = []

    def menu_show(self, items, msg):
        self.events.append(("show", msg))

    def menu_hide(self):
        self.events.append(("hide",))

    def menu_input(self, a):
        self.events.append(("in", a))


def test_quicksave_and_exit_then_resume(env, monkeypatch):
    cfg, roms, tmp = env
    _write(roms / "psx" / "Game (USA).chd", b"d" * 2000)
    scanner.scan_system("psx", roms / "psx")
    prof = library.default_profile()

    # 1) gra działa, menu → quicksave i wyjdź
    class S:
        game = library.game(1)
        profile_id = prof
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    fa = FakeAdapter(tmp / "states")
    ui = FakeUI()
    m = hotkey.GameMenu(S, fa, ui)
    m._open()
    m.action("quicksave")
    S.proc.wait(10)
    time.sleep(0.2)
    r = library.get_resume(prof, 1)
    assert r and r["family"] == "fake" and Path(r["path"]).read_bytes() == b"STATE"
    assert m.new_resume and fa.saves == 1

    # 2) następne uruchomienie dostaje parametr wczytania stanu, potem stan znika
    rec = tmp / "argv.txt"
    code = f"import sys,pathlib;pathlib.Path(r'{rec}').write_text(str(sys._xoptions.get('resume','')))"
    cfg["systems"]["psx"] = {"exe": sys.executable, "args": f'-c "{code}"'}
    monkeypatch.setattr(ingame, "adapter_for", lambda exe: fa)
    monkeypatch.setattr(launcher, "time", _FastClock())
    s = launcher.Session(cfg, 1, prof)
    s.start()
    s.thread.join(30)
    assert s.phase == "finished", s.message
    assert rec.read_text() == r["path"]
    assert library.get_resume(prof, 1) is None


class _FastClock:
    """Udaje, że gra trwała 10 s (stan wznowienia czyścimy tylko po prawdziwej grze)."""
    def __init__(self):
        self._t = 0.0

    def monotonic(self):
        self._t += 5.0
        return self._t

    def time(self):
        return time.time()

    def sleep(self, s):
        time.sleep(min(s, 0.01))


def test_quicksave_failure_keeps_game_running(env):
    cfg, roms, tmp = env
    _write(roms / "psx" / "G.chd")
    scanner.scan_system("psx", roms / "psx")

    class NoSave(FakeAdapter):
        def save_state(self):
            pass

        def wait_for_state(self, since, timeout=8.0):
            return None

    class S:
        game = library.game(1)
        profile_id = library.default_profile()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    ui = FakeUI()
    m = hotkey.GameMenu(S, NoSave(tmp / "st"), ui)
    m._open()
    m.action("quicksave")
    time.sleep(2.5)
    try:
        assert S.proc.poll() is None                     # gra dalej działa
        assert ui.events[-1][0] == "show" and "Nie udało się" in ui.events[-1][1]
        assert library.get_resume(S.profile_id, 1) is None
    finally:
        S.proc.kill()
