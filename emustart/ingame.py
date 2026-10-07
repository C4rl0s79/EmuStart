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


def _serial(meta: dict) -> str:
    """Numer seryjny w formie, jakiej używają emulatory: 'SCUS-94228'
    (baza RetroArcha bywa z sufiksem: 'SCUS-94228CE', '51131-0')."""
    m = re.search(r"[A-Z]{4}-\d{3,5}", meta.get("serial") or "")
    return m.group(0) if m else ""


def ini_edit(path: Path, changes: dict) -> dict:
    """Zmienia wartości {(sekcja, klucz): wartość} w pliku ini, zachowując resztę
    pliku bez zmian. Zwraca poprzednie wartości (do przywrócenia)."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    except OSError:
        return {}
    section, old = None, {}
    for i, line in enumerate(lines):
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            section = m.group(1)
            continue
        m = re.match(r"^(\s*)([^=;#]+?)(\s*=\s*)(.*?)(\r?\n?)$", line)
        if m and (section, m.group(2)) in changes:
            key = (section, m.group(2))
            new = changes[key]
            if new != m.group(4):
                old[key] = m.group(4)
                lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{new}{m.group(5)}"
    if old:
        tmp = path.with_name(path.name + ".emustart-tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.replace(tmp, path)
    return old


def ini_section(path: Path, section: str) -> dict:
    out, cur = {}, None
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            cur = m.group(1)
        elif cur == section:
            m = re.match(r"^\s*([^=;#]+?)\s*=\s*(.*?)\s*$", line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def ini_set(path: Path, changes: dict) -> None:
    """Jak ini_edit, ale brakujące klucze (i sekcje) dopisuje; tworzy plik."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines(keepends=True)
    except OSError:
        lines = []
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    todo = dict(changes)
    section, last_in = None, {}
    for i, line in enumerate(lines):
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            section = m.group(1)
            last_in.setdefault(section, i)
            continue
        if section is not None and line.strip():
            last_in[section] = i
        m = re.match(r"^(\s*)([^=;#]+?)(\s*=\s*)(.*?)(\r?\n?)$", line)
        if m and (section, m.group(2)) in todo:
            new = todo.pop((section, m.group(2)))
            lines[i] = f"{m.group(1)}{m.group(2)}{m.group(3)}{new}{m.group(5) or chr(10)}"
    # brakujące klucze: na końcu istniejącej sekcji albo w nowej sekcji
    inserts: dict = {}
    for (sec, key), val in todo.items():
        k = -1 if sec is None else last_in[sec] if sec in last_in else ("new", sec)
        inserts.setdefault(k, []).append(f"{key} = {val}\n")
    # klucze bez sekcji (retroarch.cfg): na początek pliku (indeks -1 + 1 = 0)
    for idx in sorted((k for k in inserts if isinstance(k, int)), reverse=True):
        lines[idx + 1:idx + 1] = inserts[idx]
    for k, add in inserts.items():
        if not isinstance(k, int):
            lines += (["\n"] if lines else []) + [f"[{k[1]}]\n"] + add
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".emustart-tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, path)


def ini_pick(path: Path, rules: list) -> dict:
    """Wartości {(sekcja, klucz): wartość} pasujące do reguł [(regex sekcji, regex klucza)];
    plik bez sekcji (retroarch.cfg) = sekcja ''."""
    out, cur = {}, None
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            cur = m.group(1)
            continue
        m = re.match(r"^\s*([^=;#\[]+?)\s*=\s*(.*?)\s*$", line)
        if not m:
            continue
        for sr, kr in rules:
            if re.fullmatch(sr, cur or "") and re.fullmatch(kr, m.group(1)):
                out[(cur, m.group(1))] = m.group(2)
                break
    return out


def _restore_file() -> Path:
    return paths.DATA / "pad_restore.json"


def _remember_restore(path: Path, old: dict) -> None:
    """Zapis oryginałów na dysku — gdyby EmuStart padł w trakcie gry, przy
    następnym starcie przywrócimy ustawienia padów (restore_pending)."""
    import json
    try:
        data = json.loads(_restore_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault(str(path), []).extend([k[0], k[1], v] for k, v in old.items())
    _restore_file().parent.mkdir(parents=True, exist_ok=True)
    _restore_file().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def restore_pending() -> None:
    import json
    try:
        data = json.loads(_restore_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for path, items in data.items():
        changes = {}
        for sec, key, val in reversed(items):        # najstarsza wartość wygrywa
            changes[(sec, key)] = val
        ini_edit(Path(path), changes)
        log.info("przywrócono ustawienia padów: %s", path)
    _restore_file().unlink(missing_ok=True)


def _remap_sdl_ini(path: Path, section_fmt: str, order: list, players: int = 4):
    """[Pad1]… DuckStation/PCSX2: SDL-n / XInput-n → slot gracza z `order`."""
    changes = {}
    for p, slot in enumerate(order[:players]):
        sec = section_fmt.format(p + 1)
        for k, v in ini_section(path, sec).items():
            nv = re.sub(r"\b(SDL|XInput)-\d+/", lambda m: f"{m.group(1)}-{slot}/", v)
            if nv != v:
                changes[(sec, k)] = nv
    if not changes:
        return None
    old = ini_edit(path, changes)
    if not old:
        return None
    _remember_restore(path, old)

    def restore():
        ini_edit(path, old)
        _restore_file().unlink(missing_ok=True)
    return restore


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

    def launch_args(self, run_dir: Path, resume: bool, pad_order: list | None = None) -> list:
        return []

    def save_dirs(self) -> list:
        """Foldery save'ów i stanów, które profil podpina jako własne."""
        return []

    def state_patterns(self, game: dict, meta: dict) -> list:
        """Wzorce nazw plików stanów tej gry (glob, w state_dirs)."""
        return []

    def prefix_of(self, filename: str) -> str:
        """Wspólny początek nazw stanów gry, wyliczony z nazwy jednego pliku."""
        return ""

    def start_state_args(self, run_dir: Path, state: Path, pad_order: list | None = None) -> list:
        """Parametry startu z wybranym plikiem stanu (zastępują launch_args)."""
        return self.launch_args(run_dir, False, pad_order) + self.resume_args(state)

    def remap_pads(self, order: list):
        """Przepina pady wg `order`; zwraca funkcję przywracającą albo None."""
        return None

    # ustawienia profilu (profiles.settings_load / settings_save)
    def settings_base(self) -> Path:
        return self.home

    def settings_files(self) -> list:
        """Pliki/foldery ustawień (względem settings_base), które należą do profilu."""
        return []

    def machine_keys(self) -> dict:
        """{plik: [(regex sekcji, regex klucza)]} — wartości zależne od komputera
        (ścieżki, karta grafiki, urządzenie audio), których profil nie nadpisuje."""
        return {}

    # RetroAchievements: konto profilu na tę sesję ({"user", "token", "hardcore"})
    cheevos: dict | None = None

    def read_cheevos(self) -> dict:
        """Konto zalogowane w samym emulatorze: {"user", "token"} albo {}."""
        return {}

    def apply_cheevos(self, ra: dict | None) -> None:
        """Ustawia konto profilu w konfiguracji emulatora (puste = wyłącz)."""

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
        return [self._cfg_dir("savestate_directory", "states")]

    def launch_args(self, run_dir: Path, resume: bool, pad_order: list | None = None) -> list:
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = run_dir / "emustart_ra.cfg"
        # config_save_on_exit=false: RetroArch nie zapisze tych ustawień sesji
        # do Twojego retroarch.cfg
        lines = [
            'network_cmd_enable = "true"',
            f'network_cmd_port = "{RA_PORT}"',
            'config_save_on_exit = "false"',
            'savestate_auto_save = "true"',
            f'savestate_auto_load = "{"true" if resume else "false"}"',
            # save'y zawsze w podpiętym folderze — także gdy retroarch.cfg każe
            # trzymać je obok gry (gra bywa w RAM-ie albo w pamięci podręcznej)
            f'savefile_directory = "{self._cfg_dir("savefile_directory", "saves")}"',
            f'savestate_directory = "{self._cfg_dir("savestate_directory", "states")}"',
        ]
        ra = self.cheevos
        if ra is not None:
            on = bool(ra.get("user") and ra.get("token"))
            lines += [f'cheevos_enable = "{"true" if on else "false"}"',
                      f'cheevos_username = "{ra["user"] if on else ""}"',
                      f'cheevos_token = "{ra["token"] if on else ""}"',
                      'cheevos_password = ""',
                      f'cheevos_hardcore_mode_enable = "{"true" if on and ra.get("hardcore") else "false"}"']
        # sterownik xinput: indeks pada = slot XInput
        for p, slot in enumerate((pad_order or [])[:4]):
            lines.append(f'input_player{p + 1}_joypad_index = "{slot}"')
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ["--appendconfig", str(cfg)]

    def _cfg_dir(self, key: str, default: str) -> Path:
        d = _ini_value(self.home / "retroarch.cfg", key)
        if d.startswith(":"):
            d = str(self.home) + d[1:]
        return Path(d) if d and d != "default" else self.home / default

    def settings_files(self) -> list:
        return ["retroarch.cfg", "config"]

    def machine_keys(self) -> dict:
        return {"retroarch.cfg": [("", r".*(_directory|_path|_dir)|video_driver|video_adapter_index|"
                                       r"audio_driver|audio_device|video_monitor_index|cheevos_.*")]}

    def read_cheevos(self) -> dict:
        cfg = self.home / "retroarch.cfg"
        user, token = _ini_value(cfg, "cheevos_username"), _ini_value(cfg, "cheevos_token")
        return {"user": user, "token": token} if user and token else {}

    def save_dirs(self) -> list:
        return [self._cfg_dir("savefile_directory", "saves"),
                self._cfg_dir("savestate_directory", "states")]

    def state_patterns(self, game: dict, meta: dict) -> list:
        stems = {Path(game["rel"]).stem, game["name"]}
        return [f"{s}.state*" for s in stems]

    def start_state_args(self, run_dir: Path, state: Path, pad_order: list | None = None) -> list:
        name = state.name
        if name.endswith(".auto"):
            return self.launch_args(run_dir, True, pad_order)
        m = re.search(r"\.state(\d*)$", name)
        slot = int(m.group(1)) if m and m.group(1) else 0
        return self.launch_args(run_dir, False, pad_order) + [f"--entryslot={slot}"]

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

    def _dir(self, section: str, key: str, default: str) -> Path:
        p = Path(ini_section(self._ini(), section).get(key, "") or default)
        return p if p.is_absolute() else self._ini().parent / p

    def state_dirs(self) -> list:
        return [self._dir("Folders", "SaveStates", "savestates")]

    def settings_base(self) -> Path:
        return self._ini().parent

    def settings_files(self) -> list:
        return ["settings.ini", "gamesettings", "inputprofiles"]

    def machine_keys(self) -> dict:
        return {"settings.ini": [("Folders|BIOS|GameList|UI|GameListTableView|AutoUpdater", ".*"),
                                 ("MemoryCards", "Directory"), ("GPU", "Adapter"),
                                 ("Audio", "Backend|Driver|OutputDevice"), ("Main", "SettingsVersion"),
                                 ("Cheevos", "Enabled|Username|Token|LoginTimestamp|ChallengeMode")]}

    def read_cheevos(self) -> dict:
        c = ini_section(self._ini(), "Cheevos")
        return {"user": c["Username"], "token": c["Token"]} if c.get("Username") and c.get("Token") else {}

    def apply_cheevos(self, ra: dict | None) -> None:
        on = bool(ra and ra.get("user") and ra.get("token"))
        ini_set(self._ini(), {("Cheevos", "Enabled"): "true" if on else "false",
                              ("Cheevos", "Username"): ra["user"] if on else "",
                              ("Cheevos", "Token"): ra["token"] if on else "",
                              ("Cheevos", "LoginTimestamp"): str(int(time.time())) if on else "0",
                              ("Cheevos", "ChallengeMode"): "true" if on and ra.get("hardcore") else "false"})

    def resume_args(self, state: Path) -> list:
        return ["-statefile", str(state)]

    def pause(self) -> None:
        winutil.send_keys(_qt_key(_ini_value(self._ini(), "TogglePause")) or "SPACE")

    def save_dirs(self) -> list:
        return [self._dir("MemoryCards", "Directory", "memcards"), self._dir("Folders", "SaveStates", "savestates")]

    def state_patterns(self, game: dict, meta: dict) -> list:
        serial = _serial(meta)
        return [f"{serial}_*.sav"] if serial else []

    def prefix_of(self, filename: str) -> str:
        return filename.rsplit("_", 1)[0] + "_" if "_" in filename else ""

    def remap_pads(self, order: list):
        return _remap_sdl_ini(self._ini(), "Pad{}", order)


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

    def _dir(self, key: str, default: str) -> Path:
        p = Path(ini_section(self._ini(), "Folders").get(key, "") or default)
        return p if p.is_absolute() else self._ini().parent.parent / p

    def state_dirs(self) -> list:
        return [self._dir("Savestates", "sstates")]

    def settings_base(self) -> Path:
        return self._ini().parent.parent

    def settings_files(self) -> list:
        return ["inis/PCSX2.ini", "gamesettings", "inputprofiles"]

    def machine_keys(self) -> dict:
        return {"inis/PCSX2.ini": [("Folders|Filenames|GameList|UI|GameListTableView|AutoUpdater", ".*"),
                                   ("EmuCore/GS", "Adapter"), ("SPU2/Output", "Backend|Driver|DeviceName|OutputModule"),
                                   ("Achievements", ".*SoundName|Enabled|Username|LoginTimestamp|ChallengeMode")]}

    def _secrets(self) -> Path:
        return self._ini().parent / "secrets.ini"

    def read_cheevos(self) -> dict:
        user = ini_section(self._ini(), "Achievements").get("Username", "")
        token = (ini_section(self._secrets(), "Achievements").get("Token", "")
                 or ini_section(self._ini(), "Achievements").get("Token", ""))
        return {"user": user, "token": token} if user and token else {}

    def apply_cheevos(self, ra: dict | None) -> None:
        on = bool(ra and ra.get("user") and ra.get("token"))
        ini_set(self._ini(), {("Achievements", "Enabled"): "true" if on else "false",
                              ("Achievements", "Username"): ra["user"] if on else "",
                              ("Achievements", "LoginTimestamp"): str(int(time.time())) if on else "0",
                              ("Achievements", "ChallengeMode"): "true" if on and ra.get("hardcore") else "false"})
        ini_set(self._secrets(), {("Achievements", "Token"): ra["token"] if on else ""})

    def resume_args(self, state: Path) -> list:
        return ["-statefile", str(state)]

    def pause(self) -> None:
        winutil.send_keys(_qt_key(_ini_value(self._ini(), "TogglePause")) or "SPACE")

    def save_dirs(self) -> list:
        return [self._dir("MemoryCards", "memcards"), self._dir("Savestates", "sstates")]

    def state_patterns(self, game: dict, meta: dict) -> list:
        serial = _serial(meta)
        return [f"{serial} (*).*.p2s"] if serial else []

    def prefix_of(self, filename: str) -> str:
        parts = filename.rsplit(".", 2)
        return parts[0] + "." if len(parts) == 3 else ""

    def remap_pads(self, order: list):
        return _remap_sdl_ini(self._ini(), "Pad{}", order)


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

    def save_dirs(self) -> list:
        u = self._user()
        return [u / "GC", u / "Wii", u / "StateSaves"]

    def prefix_of(self, filename: str) -> str:
        return filename.rsplit(".", 1)[0] + "." if "." in filename else ""

    def remap_pads(self, order: list):
        path = self._user() / "Config" / "GCPadNew.ini"
        changes = {}
        for p, slot in enumerate(order[:4]):
            sec = f"GCPad{p + 1}"
            dev = ini_section(path, sec).get("Device", "")
            nd = re.sub(r"^(XInput|SDL)/\d+/", lambda m: f"{m.group(1)}/{slot}/", dev)
            if nd != dev:
                changes[(sec, "Device")] = nd
        if not changes:
            return None
        old = ini_edit(path, changes)
        if not old:
            return None
        _remember_restore(path, old)

        def restore():
            ini_edit(path, old)
            _restore_file().unlink(missing_ok=True)
        return restore


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

    def save_dirs(self) -> list:
        psp = self.home / "memstick" / "PSP"
        return [psp / "SAVEDATA", psp / "PPSSPP_STATE"]

    def state_patterns(self, game: dict, meta: dict) -> list:
        serial = _serial(meta).replace("-", "")
        return [f"{serial}_*.ppst"] if serial else []

    def prefix_of(self, filename: str) -> str:
        return filename.rsplit("_", 1)[0] + "_" if "_" in filename else ""


class RPCS3(Adapter):
    """Tylko save'y (do profili) — RPCS3 nie ma stanów ani skrótów do sterowania."""
    family = "rpcs3"

    def save_dirs(self) -> list:
        return [self.home / "dev_hdd0" / "home" / "00000001" / "savedata"]


_ADAPTERS = {a.family: a for a in (RetroArch, DuckStation, PCSX2, Dolphin, PPSSPP, RPCS3)}


def list_states(adapter: Adapter, game: dict, meta: dict, learned: list) -> list:
    """Pliki stanów tej gry, najnowsze pierwsze: [{path, name, time}]."""
    pats = adapter.state_patterns(game, meta) + [f"{glob_escape(p)}*" for p in learned]
    seen, out = set(), []
    for d in adapter.state_dirs():
        if not d.is_dir():
            continue
        for pat in pats:
            for f in d.rglob(pat):
                if f in seen or not f.is_file() or f.suffix.lower() in (".png", ".jpg", ".bak"):
                    continue
                seen.add(f)
                out.append({"path": str(f), "name": f.name, "time": f.stat().st_mtime})
    out.sort(key=lambda x: x["time"], reverse=True)
    return out


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


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
