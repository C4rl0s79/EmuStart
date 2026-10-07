"""ingame — menu w grze: zapis/wczytanie stanu, „quicksave i wyjdź”, wyjście.

Każda rodzina emulatora ma adapter:
  * jak zapisać / wczytać stan w trakcie gry (komenda sieciowa RetroArcha albo
    skrót klawiszowy czytany z ustawień emulatora),
  * gdzie leżą pliki stanów (żeby znaleźć ten właśnie zapisany),
  * jak przy następnym starcie wczytać stan „wznowienia” (parametr wiersza poleceń
    sprawdzony w pliku exe danego emulatora),
  * czy trzeba go pauzować na czas menu (RetroArch i PPSSPP pauzują się same po
    utracie fokusu, DuckStation i PCSX2 u Ciebie nie).

Stan „wznowienia” kopiujemy do data/resume/<profil>/, więc późniejsze zapisy
w tym samym slocie emulatora go nie nadpiszą.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import socket
import time
from pathlib import Path

from emustart import emulators, paths, winutil

log = logging.getLogger("emustart.ingame")

RA_PORT = 55355


def _ini_value(path: Path, key: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", text, re.M)
    return m.group(1).strip().strip('"') if m else ""


def _qt_key(binding: str) -> str:
    """'Keyboard/F2' (DuckStation/PCSX2) → 'F2'; 'Keyboard/Shift & F1' → 'SHIFT+F1'."""
    if not binding.lower().startswith("keyboard/"):
        return ""
    keys = [k.strip().upper() for k in re.split(r"[&+]", binding.split("/", 1)[1])]
    return "+".join(k for k in keys if k in winutil.VK)


def _newest(dirs, pattern: str, since: float) -> Path | None:
    best = None
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob(pattern):
            try:
                mt = p.stat().st_mtime
            except OSError:
                continue
            if mt >= since and (best is None or mt > best[0]):
                best = (mt, p)
    return best[1] if best else None


class Adapter:
    family = ""
    pause_key = ""            # pusty = emulator pauzuje się sam po utracie fokusu
    state_glob = ""

    def __init__(self, exe: str):
        self.exe = Path(exe)
        self.home = self.exe.parent

    # możliwości
    def can_states(self) -> bool:
        return bool(self.save_key() and self.load_key() and self.state_glob)

    def can_resume(self) -> bool:
        return self.can_states() and bool(self.resume_args(Path("x")))

    # do nadpisania
    def save_key(self) -> str:
        return ""

    def load_key(self) -> str:
        return ""

    def state_dirs(self) -> list:
        return []

    def resume_args(self, state: Path) -> list:
        return []

    def launch_args(self, run_dir: Path, resume: bool) -> list:
        return []

    # akcje (emulator ma fokus)
    def pause(self) -> None:
        if self.pause_key:
            winutil.send_keys(self.pause_key)

    def save_state(self) -> None:
        winutil.send_keys(self.save_key())

    def load_state(self) -> None:
        winutil.send_keys(self.load_key())

    def wait_for_state(self, since: float, timeout: float = 8.0) -> Path | None:
        """Plik stanu zapisany po `since`; czekamy, aż przestanie rosnąć."""
        end = time.monotonic() + timeout
        last = None
        while time.monotonic() < end:
            p = _newest(self.state_dirs(), self.state_glob, since)
            if p:
                size = p.stat().st_size
                if last == (p, size) and size > 0:
                    return p
                last = (p, size)
            time.sleep(0.25)
        return None

    def quit(self, proc) -> None:
        winutil.close_windows(proc.pid)


class RetroArch(Adapter):
    """Komendy sieciowe UDP (włączane na czas sesji przez --appendconfig).
    Wznowienie: autozapis RetroArcha przy wyjściu + autowczytanie przy starcie,
    które włączamy tylko po „quicksave i wyjdź”."""
    family = "retroarch"
    state_glob = "*.state*"

    def can_states(self) -> bool:
        return True

    def can_resume(self) -> bool:
        return True

    def _cmd(self, text: str) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(text.encode(), ("127.0.0.1", RA_PORT))

    def pause(self) -> None:
        pass                  # pause_nonactive: pauzuje się sam

    def save_state(self) -> None:
        self._cmd("SAVE_STATE")

    def load_state(self) -> None:
        self._cmd("LOAD_STATE")

    def state_dirs(self) -> list:
        d = _ini_value(self.home / "retroarch.cfg", "savestate_directory")
        d = d.replace(":\\", str(self.home) + "\\", 1) if d.startswith(":") else d
        return [Path(d)] if d else [self.home / "states"]

    def launch_args(self, run_dir: Path, resume: bool) -> list:
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = run_dir / "emustart_ra.cfg"
        # config_save_on_exit=false: RetroArch nie zapisze tych ustawień sesji
        # do Twojego retroarch.cfg
        cfg.write_text("\n".join([
            'network_cmd_enable = "true"',
            f'network_cmd_port = "{RA_PORT}"',
            'config_save_on_exit = "false"',
            'savestate_auto_save = "true"',
            f'savestate_auto_load = "{"true" if resume else "false"}"',
        ]) + "\n", encoding="utf-8")
        return ["--appendconfig", str(cfg)]

    def quit(self, proc) -> None:
        self._cmd("QUIT")
        time.sleep(0.4)
        self._cmd("QUIT")     # quit_press_twice


class DuckStation(Adapter):
    family = "duckstation"
    pause_key = "SPACE"
    state_glob = "*.sav"

    def _ini(self) -> Path:
        portable = (self.home / "portable.txt").exists() or (self.home / "settings.ini").exists()
        return (self.home if portable else Path.home() / "Documents" / "DuckStation") / "settings.ini"

    def save_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "SaveSelectedSaveState")) or "F2"

    def load_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "LoadSelectedSaveState")) or "F1"

    def state_dirs(self) -> list:
        return [self._ini().parent / "savestates"]

    def resume_args(self, state: Path) -> list:
        return ["-statefile", str(state)]

    def pause(self) -> None:
        winutil.send_keys(_qt_key(_ini_value(self._ini(), "TogglePause")) or "SPACE")


class PCSX2(Adapter):
    family = "pcsx2"
    state_glob = "*.p2s"

    def _ini(self) -> Path:
        if (self.home / "portable.ini").exists() or (self.home / "portable.txt").exists():
            return self.home / "inis" / "PCSX2.ini"
        return Path.home() / "Documents" / "PCSX2" / "inis" / "PCSX2.ini"

    def save_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "SaveStateToSlot")) or "F1"

    def load_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "LoadStateFromSlot")) or "F3"

    def state_dirs(self) -> list:
        return [self._ini().parent.parent / "sstates"]

    def resume_args(self, state: Path) -> list:
        return ["-statefile", str(state)]

    def pause(self) -> None:
        winutil.send_keys(_qt_key(_ini_value(self._ini(), "TogglePause")) or "SPACE")


class Dolphin(Adapter):
    family = "dolphin"
    state_glob = "*.s[0-9][0-9]"

    def _user(self) -> Path:
        if (self.home / "portable.txt").exists():
            return self.home / "User"
        for d in (Path(os.environ.get("APPDATA", "")) / "Dolphin Emulator",
                  Path.home() / "Documents" / "Dolphin Emulator"):
            if d.is_dir():
                return d
        return self.home / "User"

    def _hotkey(self, name: str, default: str) -> str:
        raw = _ini_value(self._user() / "Config" / "Hotkeys.ini", name)
        raw = raw.strip("@()`").replace("`", "")
        keys = [k.strip().upper() for k in raw.split("+") if k.strip()]
        return "+".join(keys) if keys and all(k in winutil.VK for k in keys) else default

    def save_key(self) -> str:
        return self._hotkey("Save State/Save State Slot 1", "SHIFT+F1")

    def load_key(self) -> str:
        return self._hotkey("Load State/Load State Slot 1", "F1")

    def state_dirs(self) -> list:
        return [self._user() / "StateSaves"]

    def resume_args(self, state: Path) -> list:
        return ["-s", str(state)]

    def pause(self) -> None:
        winutil.send_keys(self._hotkey("General/Toggle Pause", "F10"))


class PPSSPP(Adapter):
    family = "ppsspp"
    state_glob = "*.ppst"
    _ANDROID_F1 = 131         # PPSSPP zapisuje klawisze kodami Androida (F1=131)

    def _key(self, name: str, default: str) -> str:
        raw = _ini_value(self.home / "memstick" / "PSP" / "SYSTEM" / "controls.ini", name)
        for part in raw.split(","):
            dev, _, code = part.partition("-")
            if dev == "1" and code.isdigit() and 0 <= int(code) - self._ANDROID_F1 < 12:
                return f"F{int(code) - self._ANDROID_F1 + 1}"
        return default

    def save_key(self) -> str:
        return self._key("Save State", "F2")

    def load_key(self) -> str:
        return self._key("Load State", "F4")

    def state_dirs(self) -> list:
        return [self.home / "memstick" / "PSP" / "PPSSPP_STATE",
                Path.home() / "Documents" / "PPSSPP" / "PSP" / "PPSSPP_STATE"]

    def resume_args(self, state: Path) -> list:
        return [f"--state={state}"]

    def pause(self) -> None:
        pass                  # PPSSPP pauzuje się sam po utracie fokusu


_ADAPTERS = {a.family: a for a in (RetroArch, DuckStation, PCSX2, Dolphin, PPSSPP)}


def adapter_for(exe: str) -> Adapter:
    return _ADAPTERS.get(emulators.family(exe), Adapter)(exe)


def resume_dir(profile_id: int) -> Path:
    return paths.DATA / "resume" / str(profile_id)


def keep_resume(profile_id: int, game_id: int, state: Path) -> Path:
    """Kopia stanu do data/resume — poza slotami emulatora."""
    d = resume_dir(profile_id)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{game_id}.*"):
        old.unlink(missing_ok=True)
    dst = d / f"{game_id}{state.suffix}"
    shutil.copy2(state, dst)
    return dst
