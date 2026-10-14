"""Menu w grze: odczyt skrótów z ustawień emulatorów, quicksave i wyjdź,
jednorazowe wczytanie stanu wznowienia przy następnym starcie."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from emustart import hotkey, ingame, launcher, library, scanner

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
    text = Path(args[args.index("--appendconfig") + 1]).read_text()
    assert 'network_cmd_enable = "true"' in text and 'savestate_auto_load = "true"' in text
    # bez retroarch.cfg nie ma kopii sesji — RetroArch działa na swoim pliku
    assert "--config" not in args


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


def test_quicksave_and_exit_then_resume(env, monkeypatch, server_files):
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


def test_state_thumbnails_from_emulators(tmp_path):
    """Obrazek stanu przy wyborze zapisu: RetroArch (.png obok), PCSX2 (zrzut w .p2s),
    DuckStation (RGBA w nagłówku .sav); bez obrazka — None."""
    import io
    import struct
    import zipfile
    from PIL import Image
    from emustart import ingame

    def png(color):
        b = io.BytesIO()
        Image.new("RGB", (4, 3), color).save(b, "PNG")
        return b.getvalue()
    ra = tmp_path / "Gra.state1"
    ra.write_bytes(b"STAN")
    (tmp_path / "Gra.state1.png").write_bytes(png("red"))
    from emustart import mediastore
    t = ingame.state_thumb(ra)
    assert t and mediastore.get("_stany", t)[0] == png("red")
    assert ingame.state_thumb(ra) == t                                  # drugi raz — z pamięci
    p2s = tmp_path / "Gra.01.p2s"
    with zipfile.ZipFile(p2s, "w") as z:
        z.writestr("Screenshot.png", png("blue"))
        z.writestr("eeMemory.bin", b"x")
    assert mediastore.get("_stany", ingame.state_thumb(p2s))[0] == png("blue")
    w, h = 2, 2
    head = bytearray(200)
    head[:4] = b"DUCS"
    struct.pack_into("<4I", head, 184, w, h, w * h * 4, 200)
    sav = tmp_path / "Gra_1.sav"
    sav.write_bytes(bytes(head) + bytes([0, 255, 0, 255]) * 4 + b"DANE")
    img = Image.open(io.BytesIO(mediastore.get("_stany", ingame.state_thumb(sav))[0]))
    assert img.size == (2, 2) and img.getpixel((0, 0))[:3] == (0, 255, 0)
    bad = tmp_path / "Inna_1.sav"
    bad.write_bytes(b"DUCS" + bytes(300))                               # bez zrzutu — nic
    assert ingame.state_thumb(bad) is None
    assert ingame.state_thumb(tmp_path / "brak.state") is None
