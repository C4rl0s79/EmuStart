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

from emustart import dstoken, emulators, hidpad, pads, paths, winutil, xinput

log = logging.getLogger("emustart.ingame")

RA_PORT = 55355


def _ini_value(path: Path, key: str) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", text, re.M)
    return m.group(1).strip().strip('"') if m else ""


def _cfg_map(path: Path) -> dict:
    """retroarch.cfg → {klucz: surowa wartość (z cudzysłowami)}; ostatnie wystąpienie wygrywa."""
    out = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r"^\s*([^=#;\s\[]+)\s*=\s*(.*?)\s*$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _qt_key(binding: str) -> str:
    """'Keyboard/F2' (DuckStation/PCSX2) → 'F2'; 'Keyboard/Shift & F1' → 'SHIFT+F1'."""
    if not binding.lower().startswith("keyboard/"):
        return ""
    alias = {"CONTROL": "CTRL", "RETURN": "ENTER"}
    keys = [k.strip().upper().removeprefix("KEYBOARD/") for k in re.split(r"[&+]", binding.split("/", 1)[1])]
    keys = [alias.get(k, k) for k in keys]
    return "+".join(k for k in keys if k in winutil.VK) if all(k in winutil.VK for k in keys) else ""


def _bind_missing(path: Path, section: str, wanted: dict) -> None:
    """Skróty potrzebne do LB + … (hotkey.py), których emulator nie ma przypisanych — dopisane do jego
    ustawień; przypisane przez gracza zostają."""
    if not path.parent.is_dir():
        return
    have = ini_section(path, section) if path.is_file() else {}
    changes = {(section, k): v for k, v in wanted.items() if not (have.get(k) or "").strip()}
    if changes:
        try:
            ini_set(path, changes)
            log.info("skróty pada: %s ← %s", path.name, ", ".join(sorted(k for _s, k in changes)))
        except OSError:
            log.exception("skróty pada: %s", path)


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
        m = re.match(r"^(\s*)([^=;#]+?)([ \t]*=[ \t]*)(.*?)(\r?\n?)$", line)
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
        m = re.match(r"^(\s*)([^=;#]+?)([ \t]*=[ \t]*)(.*?)(\r?\n?)$", line)
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


def blank_secrets(text: str, rules: list) -> str:
    """Tekst ini/cfg z pustymi wartościami kluczy pasujących do reguł."""
    out, cur = [], None
    for line in text.splitlines(keepends=True):
        m = re.match(r"^\s*\[(.+?)\]\s*$", line)
        if m:
            cur = m.group(1)
        else:
            m = re.match(r"^(\s*)([^=;#\[]+?)([ \t]*=[ \t]*)(.*?)(\r?\n?)$", line)
            if m and m.group(4) not in ("", '""') and any(
                    re.fullmatch(sr, cur or "") and re.fullmatch(kr, m.group(2)) for sr, kr in rules):
                quote = '""' if m.group(4).startswith('"') else ""
                line = f"{m.group(1)}{m.group(2)}{m.group(3)}{quote}{m.group(5)}"
        out.append(line)
    return "".join(out)


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
        log.info("przywrócono ustawienia sprzed gry (pady, foldery zapisów): %s", path)
    _restore_file().unlink(missing_ok=True)


def _remap_sdl_ini(path: Path, section_fmt: str, order: list, players: int = 4, hid: set | None = None):
    """[Pad1]… DuckStation/PCSX2: SDL-n / XInput-n → numer pada gracza z `order` (None = bez zmian).
    Gracze z `hid` mają pad HID — ten jest tylko w SDL, więc XInput-n zamieniamy na SDL-n
    (nazwy przycisków w obu źródłach są te same: A, DPadUp, LeftX…)."""
    changes = {}
    for p, slot in enumerate(order[:players]):
        if slot is None:
            continue
        sec = section_fmt.format(p + 1)
        to_sdl = p in (hid or set())
        for k, v in ini_section(path, sec).items():
            nv = re.sub(r"\b(SDL|XInput)-\d+/",
                        lambda m: f"{'SDL' if to_sdl else m.group(1)}-{slot}/", v)
            if nv != v:
                changes[(sec, k)] = nv
    if not changes:
        return None
    old = ini_edit(path, changes)
    if not old:
        return None
    _remember_restore(path, old)

    def restore():
        # tylko numer pada wraca; przypisania zmienione w trakcie gry (np. inny przycisk) zostają —
        # wcześniej całe wpisy sprzed gry nadpisywały zmiany gracza
        back = {}
        for (sec, key), was in old.items():
            cur = ini_section(path, sec).get(key)
            if cur is None or cur == changes[(sec, key)]:
                back[(sec, key)] = was
                continue
            idx = re.search(r"\b(?:SDL|XInput)-\d+/", was)
            if idx:
                back[(sec, key)] = re.sub(r"\b(SDL|XInput)-\d+/", lambda m: idx.group(0), cur)
        ini_edit(path, back)
        _restore_file().unlink(missing_ok=True)
    return restore


def _remap_sdl_pads(adapter, order: list):
    """DuckStation / PCSX2: kolejność padów (sloty XInput / „hid:…”) → numery SDL-n z SDL3.dll emulatora."""
    from emustart import sdlpads
    nums = sdlpads.slots_for(order, Path(adapter.exe).parent / "SDL3.dll")
    hid = {i for i, s in enumerate(order) if pads.is_hid(s)}
    return _remap_sdl_ini(adapter._ini(), "Pad{}", nums, hid=hid)


def _session_ini(path: Path, changes: dict, defaults: dict | None = None):
    """Ustawienia ini tylko na czas gry: zapisuje `changes` (brakujące klucze dopisuje),
    pamięta poprzednie wartości na dysku (restore_pending po awarii) i zwraca funkcję
    przywracającą. Klucz, którego nie było, wraca do wartości z `defaults`."""
    old = {}
    for (sec, key), val in changes.items():
        cur = ini_section(path, sec).get(key)
        if cur is None:
            cur = (defaults or {}).get((sec, key), "")
        if cur != val:
            old[(sec, key)] = cur
    if not old:
        return None
    ini_set(path, {k: changes[k] for k in old})
    _remember_restore(path, old)

    def restore():
        ini_set(path, old)
        _restore_file().unlink(missing_ok=True)
    return restore


def _seed_card(dst: Path, candidates: list) -> None:
    """Karta pamięci gry jeszcze nie istnieje: kopia pierwszej istniejącej z `candidates`
    (dotychczasowa wspólna karta zawiera też zapisy tej gry). Bez kandydata emulator
    założy pustą kartę sam."""
    if dst.exists():
        return
    for c in candidates:
        if c and c.is_file() and c != dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(c, dst)
            log.info("karta pamięci gry %s z %s", dst.name, c.name)
            return


def _bare(name: str) -> str:
    """Nazwa bez dopisków w nawiasach i bez wielkości liter — do porównań kart pamięci."""
    return re.sub(r"\s+", " ", re.sub(r"[([].*?[)\]]", " ", name.replace("_", " "))).strip().lower()


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
    policy: dict = {}         # wymagane ustawienia z serwera (apply_policy; tylko podmieniane, nie zmieniane)
    shader_off = False        # RetroArch: gra bez shadera (ustawienie systemu w EmuStart)
    redirect: dict = {}       # foldery zapisów emulatora (nazwa) → foldery profilu (profiles.bind)

    def toggle_shader(self) -> None:
        """Shader ekranu włącz / wyłącz w trakcie gry (tylko RetroArch)."""

    def can_reset(self) -> bool:
        return False

    def first_run(self, cfg: dict) -> None:
        """Przed grą: emulator gotowy bez kreatora pierwszego uruchomienia (świeżo zainstalowany
        z serwera) — BIOS-y z serwera, wyłączony kreator i własne aktualizacje (robi je serwer)."""

    def _bios_from_server(self, cfg: dict, query: dict, dst: Path) -> list:
        """BIOS-y z serwera ({"dir": …} / {"core": …}) do `dst` (bez podfolderów); nazwy plików."""
        from emustart import libsync
        out = []
        for rel in libsync.bios_list(cfg, query):
            name = rel.rsplit("/", 1)[-1]
            target = dst / name
            if target.is_file() or libsync.bios_from_server(cfg, rel, target):
                out.append(name)
        return out

    def reset_game(self) -> None:
        """Gra od początku bez zamykania emulatora."""

    def __init__(self, exe: str):
        self.exe = Path(exe)
        self.home = self.exe.parent
        self.policy: dict = {}
        self.redirect: dict = {}

    def can_redirect(self) -> bool:
        """Foldery zapisów da się wskazać w ustawieniach emulatora — profil gracza bez
        dowiązań (junction) w folderze emulatora."""
        return False

    def _r(self, p: Path) -> Path:
        return self.redirect.get(p.name, p) if self.redirect else p

    def redirect_session(self, card: str = ""):
        """Na czas gry emulator dostaje foldery profilu (self.redirect) w swoich ustawieniach,
        a `card` — nazwę karty pamięci tej gry. Zwraca funkcję przywracającą albo None."""
        return None

    def memcard_dir(self) -> Path | None:
        return None

    def policy_ini(self) -> Path | None:
        """Plik ini, do którego trafiają wymagane ustawienia z serwera (emustart/emupolicy.py)."""
        return None

    def apply_policy(self, values: dict) -> None:
        """Wymagane ustawienia z serwera — tuż przed grą, po ustawieniach profilu (wygrywają).
        Pliku, którego jeszcze nie ma (emulator nigdy nie uruchomiony), nie zakładamy —
        pierwsze uruchomienie emulatora tworzy go sam, z kreatorem."""
        from emustart import emupolicy
        self.policy = dict(values or {})
        p = self.policy_ini()
        changes = emupolicy.ini_changes(self.policy)
        if p and changes and p.is_file():
            ini_set(p, changes)

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

    # ── skróty pada LB + … (hotkey.py): klawisze emulatora; "" — emulator nie ma takiego skrótu ──
    def menu_key(self) -> str:
        """Własne menu emulatora na pada (pauza z menu); "" — menu EmuStart."""
        return ""

    def slot_keys(self) -> tuple:
        """(następny slot zapisu, poprzedni slot)."""
        return ("", "")

    def slot_save_key(self, n: int) -> str:
        """Zapis do slotu n — emulator bez wybieranego slotu (slot wybiera EmuStart)."""
        return ""

    def screenshot_key(self) -> str:
        return ""

    def record_key(self) -> str:
        return ""

    def ff_key(self) -> str:
        """Przełącznik przyspieszenia."""
        return ""

    def ff_hold(self) -> bool:
        """Przyspieszenie działa, dopóki klawisz jest wciśnięty (LB + RB: wciśnij / puść)."""
        return False

    def prepare_hotkeys(self) -> None:
        """Przed grą: brakujące skróty emulatora do LB + … dopisane do jego ustawień."""

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

    def config_args(self, run_dir: Path) -> list:
        """Parametry uruchomienia samego emulatora (bez gry) — do zmiany ustawień."""
        return []

    def after_session(self) -> int:
        """Po zamknięciu emulatora (przed zapisem ustawień do profilu): dokończenie
        ustawień zmienionych w trakcie. Zwraca liczbę przeniesionych wartości."""
        return 0

    def machine_keys(self) -> dict:
        """{plik: [(regex sekcji, regex klucza)]} — wartości zależne od komputera
        (ścieżki, karta grafiki, urządzenie audio), których profil nie nadpisuje."""
        return {}

    def secret_keys(self) -> dict:
        """{plik: [(regex sekcji, regex klucza)]} — hasła i tokeny: w kopii
        ustawień profilu (lokalnie i na NAS) zapisywane jako puste."""
        return {}

    # RetroAchievements: konto profilu na tę sesję ({"user", "token", "hardcore"})
    cheevos: dict | None = None
    # system komputerowy: klawiatura w całości dla gry (RetroArch: game focus)
    keyboard_game: bool = False

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

    def toggle_shader(self) -> None:
        self._cmd("SHADER_TOGGLE")
        self._shader_toggled = True        # tylko do końca gry — po grze nie trafia do retroarch.cfg

    def can_reset(self) -> bool:
        return True

    def reset_game(self) -> None:
        self._cmd("RESET")

    def load_state(self) -> None:
        self._cmd("LOAD_STATE")

    def state_dirs(self) -> list:
        return [self._cfg_dir("savestate_directory", "states")]

    def can_redirect(self) -> bool:
        return True                         # foldery w ustawieniach sesji (--appendconfig)

    def apply_policy(self, values: dict) -> None:
        self.policy = {k: v for k, v in (values or {}).items() if "|" not in k}

    def launch_args(self, run_dir: Path, resume: bool, pad_order: list | None = None) -> list:
        run_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = run_dir / "retroarch.log"
        self._log_file.unlink(missing_ok=True)
        cfg = run_dir / "emustart_ra.cfg"
        # najpierw wymagane ustawienia z serwera — niżej ustawienia sesji EmuStart (wygrywają);
        # „bez shadera” tylko na czas gry: własne ustawienia shadera RetroArcha zostają
        session = (['video_shader_enable = "false"'] if self.shader_off else []) + [
            'network_cmd_enable = "true"',
            f'network_cmd_port = "{RA_PORT}"',
            'savestate_auto_save = "true"',
            f'savestate_auto_load = "{"true" if resume else "false"}"',
            'savestate_thumbnail_enable = "true"',      # obrazek stanu (podgląd w EmuStart)
            # log sesji do pliku: po grze EmuStart sprawdza, czy stan wczytał się przy starcie
            # (bez trybu szczegółowego i poziomu INFO RetroArch nie tworzy pliku logu)
            'log_to_file = "true"',
            'log_to_file_timestamp = "false"',
            'log_verbosity = "true"',
            'frontend_log_level = "1"',
            f'log_dir = "{run_dir}"',
            # save'y zawsze w folderze profilu — także gdy retroarch.cfg każe trzymać
            # je obok gry (gra bywa w RAM-ie albo w pamięci podręcznej); tylko na czas
            # sesji, retroarch.cfg wskazuje dalej własne foldery RetroArcha
            f'savefile_directory = "{self._cfg_dir("savefile_directory", "saves")}"',
            f'savestate_directory = "{self._cfg_dir("savestate_directory", "states")}"',
        ]
        self._pad_hotkeys()
        mine = {ln.split("=", 1)[0].strip() for ln in session}
        lines = [f'{k} = "{v}"' for k, v in self.policy.items() if k not in mine] + session
        if self.keyboard_game:
            # Gry na komputery (Amiga, C64…) używają klawiatury: Shift, F1–F10, Esc,
            # strzałki. Domyślnie RetroArch zamienia część klawiszy na przyciski pada
            # (prawy Shift = Select → w PUAE klawiatura ekranowa, strzałki = joystick)
            # i przechwytuje F1 (menu) oraz Esc (wyjście). Na czas sesji: game focus
            # (klawiatura dla rdzenia, skróty wyłączone) i bez przypisań klawiszy do pada.
            # Wyjście z gry: menu EmuStart (LB + A), przełączanie: Scroll Lock.
            lines.append('input_auto_game_focus = "1"')
            for p in (1, 2):
                for b in ("a", "b", "x", "y", "l", "r", "l2", "r2", "l3", "r3", "start", "select",
                          "up", "down", "left", "right"):
                    lines.append(f'input_player{p}_{b} = "nul"')
        ra = self.cheevos
        if ra is not None:
            on = bool(ra.get("user") and ra.get("token"))
            lines += [f'cheevos_enable = "{"true" if on else "false"}"',
                      f'cheevos_username = "{ra["user"] if on else ""}"',
                      f'cheevos_token = "{ra["token"] if on else ""}"',
                      'cheevos_password = ""',
                      f'cheevos_hardcore_mode_enable = "{"true" if on and ra.get("hardcore") else "false"}"']
        # sterownik xinput: indeks pada = slot XInput; pad HID (sterownik xinput w RetroArchu to
        # XInput + DirectInput) — jego miejsce na liście urządzeń DirectInput
        order = (pad_order or [])[:4]
        di = hidpad.dinput_devices() if any(pads.is_hid(s) for s in order) else []
        for p, slot in enumerate(order):
            idx = self._di_index(di, pads.hid_pad(slot)) if pads.is_hid(slot) else slot
            if idx is not None:
                lines.append(f'input_player{p + 1}_joypad_index = "{idx}"')
        hid1 = pads.hid_pad(order[0]) if order and pads.is_hid(order[0]) else None
        if hid1:
            lines = self._hid_session(hid1, di, lines)
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self._session_config(run_dir, lines) + ["--appendconfig", str(cfg)]

    # numery przycisków sterownika xinput RetroArcha (A=0 … R3=9) → przycisk XInput
    XI_BUTTONS = (xinput.A, xinput.B, xinput.X, xinput.Y, xinput.LB, xinput.RB, xinput.BACK, xinput.START,
                  xinput.L3, xinput.R3)

    @staticmethod
    def _di_index(di: list, pad) -> int | None:
        """Numer pada HID w RetroArchu = miejsce na liście DirectInput (wg VID:PID)."""
        if pad is None:
            return None
        idx = next((i for i, (v, p, _n) in enumerate(di) if (v, p) == (pad.vid, pad.pid)), None)
        return idx if idx is not None else (0 if not di else None)

    def _hid_session(self, pad, di: list, lines: list) -> list:
        """Gracz 1 to pad HID (PS, Switch, GameSir w trybie HID): skróty RetroArcha (zapisane w numeracji
        XInput — retroarch.cfg i wymagane ustawienia) przeliczone na numery tego pada, tylko na czas gry;
        brak profilu przycisków tego pada w autoconfig/dinput — dopisujemy go."""
        try:
            self._hid_autoconfig(pad, next((n for v, p, n in di if (v, p) == (pad.vid, pad.pid)), pad.name))
        except OSError:
            log.exception("RetroArch: profil pada HID")
        cur = {k: v.strip('"') for k, v in _cfg_map(self.home / "retroarch.cfg").items()}
        cur.update({k: v for k, v in self.policy.items()})
        for ln in lines:
            m = re.match(r"\s*([^=\s]+)\s*=\s*\"?(.*?)\"?\s*$", ln)
            if m:
                cur[m.group(1)] = m.group(2)
        hk = {}
        for k, v in cur.items():
            if not k.endswith("_btn") or k.startswith("input_player") or not v.isdigit():
                continue
            n = int(v)
            num = hidpad.number_of(pad.layout, self.XI_BUTTONS[n]) if n < len(self.XI_BUTTONS) else 0
            hk[k] = str(num - 1) if num else "nul"
        if not hk:
            return lines
        log.info("RetroArch: Gracz 1 = pad HID %s (%s) — skróty na czas gry: %s", pad.name, pad.layout,
                 ", ".join(f"{k}={v}" for k, v in sorted(hk.items())))
        keep = [ln for ln in lines if ln.split("=", 1)[0].strip() not in hk]
        return keep + [f'{k} = "{v}"' for k, v in hk.items()]

    def _hid_autoconfig(self, pad, di_name: str) -> None:
        """Profil przycisków pada HID dla RetroArcha (autoconfig/dinput), gdy RetroArch go nie ma —
        układ jak w hidpad.LAYOUTS (dolny przycisk = B RetroPada = A Xboxa)."""
        d = self._cfg_dir("joypad_autoconfig_dir", "autoconfig") / "dinput"
        if not d.is_dir():
            return
        for f in d.glob("*.cfg"):
            m = _cfg_map(f)
            if (m.get("input_vendor_id", "").strip('"') == str(pad.vid)
                    and m.get("input_product_id", "").strip('"') == str(pad.pid)):
                return
        num = lambda mask: hidpad.number_of(pad.layout, mask)
        binds = {"b": xinput.A, "a": xinput.B, "y": xinput.X, "x": xinput.Y, "l": xinput.LB, "r": xinput.RB,
                 "l2": hidpad.LT, "r2": hidpad.RT, "l3": xinput.L3, "r3": xinput.R3,
                 "select": xinput.BACK, "start": xinput.START}
        lines = ['input_driver = "dinput"', f'input_device = "{di_name}"',
                 f'input_device_display_name = "{pad.name}"',
                 f'input_vendor_id = "{pad.vid}"', f'input_product_id = "{pad.pid}"']
        lines += [f'input_{k}_btn = "{num(m) - 1}"' for k, m in binds.items() if num(m)]
        lines += [f'input_{d_}_btn = "h0{d_}"' for d_ in ("up", "down", "left", "right")]
        # osie DirectInput w RetroArchu: 0 X, 1 Y, 2 Z, 3 Rx, 4 Ry, 5 Rz
        rx, ry = (2, 5) if {0x32, 0x35} <= set(pad.values) else (3, 4)
        for side, ax in (("l_x", 0), ("l_y", 1), ("r_x", rx), ("r_y", ry)):
            lines += [f'input_{side}_plus_axis = "+{ax}"', f'input_{side}_minus_axis = "-{ax}"']
        name = re.sub(r'[<>:"/\\|?*]', "_", pad.name)
        (d / f"EmuStart {name}.cfg").write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("RetroArch: profil przycisków pada HID %s → %s", pad.name, d / f"EmuStart {name}.cfg")

    # skróty pada jak w pozostałych emulatorach (hotkey.py): przytrzymany LB + A menu, Y zapis
    # (kolejny numer), ↑/↓ slot (↑ = niższy numer, jak wyżej na liście), RB przyspieszenie,
    # R3 zrzut, L3 nagrywanie (XInput w RetroArchu: A=0, Y=3, LB=4, RB=5, L3=8, R3=9)
    PAD_HOTKEYS = {"input_enable_hotkey_btn": "4", "input_menu_toggle_btn": "0", "input_save_state_btn": "3",
                   "input_state_slot_increase_btn": "h0down", "input_state_slot_decrease_btn": "h0up",
                   "input_toggle_fast_forward_btn": "5", "input_screenshot_btn": "9",
                   "input_recording_toggle_btn": "8", "savestate_auto_index": "true"}
    _HOTKEYS_MARK = "emustart-skroty-lb-2.txt"     # -2: odwrócone ↑/↓ (0.54.0)

    def _pad_hotkeys(self) -> None:
        """Domyślna konfiguracja EmuStart w retroarch.cfg — raz (znacznik obok retroarch.cfg); potem
        gracz może ją zmienić w RetroArchu. Inne skróty RetroArcha na tych samych przyciskach (np.
        input_exit_emulator_btn = "0" — LB + A wychodziło z gry) — wyłączone."""
        cfg, mark = self.home / "retroarch.cfg", self.home / self._HOTKEYS_MARK
        if mark.exists() or not cfg.is_file():
            return
        used = set(self.PAD_HOTKEYS.values()) - {"4", "true"}
        changes = {(None, k): f'"{v}"' for k, v in self.PAD_HOTKEYS.items()}
        for k, v in _cfg_map(cfg).items():
            if (k.endswith("_btn") and not k.startswith("input_player") and k not in self.PAD_HOTKEYS
                    and v.strip('"') in used):
                changes[(None, k)] = '"nul"'
        try:
            ini_set(cfg, changes)
            mark.write_text("skróty pada LB + … wpisane do retroarch.cfg\n", encoding="utf-8")
            log.info("RetroArch: skróty pada LB + … w retroarch.cfg (%s)", ", ".join(sorted(k for _s, k in changes)))
        except OSError:
            log.exception("RetroArch: skróty pada")

    def config_args(self, run_dir: Path) -> list:
        """RetroArch bez gry, w menu — ustawienia (obraz, dźwięk, sterowanie) zmienione
        tu trafiają do profilu tak jak zmiany w trakcie gry."""
        self._pad_hotkeys()
        run_dir.mkdir(parents=True, exist_ok=True)
        cfg = run_dir / "emustart_ra.cfg"
        lines = [f'network_cmd_port = "{RA_PORT}"']
        cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return self._session_config(run_dir, lines) + ["--appendconfig", str(cfg), "--menu"]

    # Ustawienia zmienione w menu RetroArcha w trakcie gry. Na czas sesji RetroArch
    # czyta kopię retroarch.cfg (--config) i zapisuje ją przy wyjściu; po wyjściu
    # zmienione wartości wracają do prawdziwego retroarch.cfg — bez ustawień sesji
    # EmuStart (komendy sieciowe, foldery profilu, konto RA, kolejność padów).
    # Wcześniej (config_save_on_exit=false) zmiany ginęły przy każdym wyjściu.
    # state_slot: numer slotu wybrany w menu RetroArcha (np. „Auto” = −1 do wczytania autozapisu)
    # dotyczy tylko tej sesji — w retroarch.cfg następna gra zaczynałaby od tego slotu
    _SESSION_KEYS = re.compile(r"config_save_on_exit|cheevos_.*|input_player\d+_joypad_index|state_slot")

    def _session_config(self, run_dir: Path, lines: list) -> list:
        self._session = None
        main = self.home / "retroarch.cfg"
        if not main.is_file():
            return []
        start, sess = run_dir / "retroarch_start.cfg", run_dir / "retroarch_session.cfg"
        shutil.copyfile(main, start)
        shutil.copyfile(main, sess)
        # foldery względem retroarch.exe, nie względem kopii w folderze tymczasowym
        dirs = [f'rgui_config_directory = "{self._cfg_dir("rgui_config_directory", "config")}"',
                'config_save_on_exit = "true"']
        if _ini_value(main, "global_core_options") == "true" and not _ini_value(main, "core_options_path"):
            # wspólne opcje rdzeni leżą obok retroarch.cfg — bez tego trafiłyby do kopii
            dirs.append(f'core_options_path = "{self.home / "retroarch-core-options.cfg"}"')
        with open(sess, "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(dirs) + "\n")
        appended = {m.group(1) for m in (re.match(r"\s*([^=\s]+)\s*=", ln) for ln in lines + dirs) if m}
        self._session = (start, sess, appended)
        return ["--config", str(sess)]

    def after_session(self) -> int:
        self._state_log()
        st = getattr(self, "_session", None)
        self._session = None
        if not st:
            return 0
        start, sess, appended = st
        before, after = _cfg_map(start), _cfg_map(sess)
        changes = {(None, k): v for k, v in after.items()
                   if k not in appended and not self._SESSION_KEYS.fullmatch(k) and before.get(k) != v
                   and not (k == "video_shader_enable" and getattr(self, "_shader_toggled", False))}
        if changes:
            ini_set(self.home / "retroarch.cfg", changes)
            log.info("RetroArch: %d ustawień zmienionych w trakcie sesji → retroarch.cfg (%s)",
                     len(changes), ", ".join(sorted(k for _s, k in changes))[:300])
        return len(changes)

    _STATE_LOG = re.compile(r"state|savestate|stan", re.I)
    _STATE_SKIP = re.compile(r"thumbnail|\[Config\]|_state_|state_slot =", re.I)

    def _state_log(self) -> None:
        """Wczytanie / zapis stanów w tej sesji — z logu RetroArcha do logu EmuStart (diagnoza:
        stan wybrany w „Wczytaj zapis” nie wczytał się przy starcie)."""
        f, self._log_file = getattr(self, "_log_file", None), None
        if not f:
            return
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            log.info("RetroArch: brak logu sesji (%s)", f.name)
            return
        hits = [ln.strip() for ln in lines if self._STATE_LOG.search(ln) and not self._STATE_SKIP.search(ln)]
        for ln in hits[:30]:
            log.info("RetroArch (stany): %s", ln[:300])
        if not hits:
            log.info("RetroArch: w logu sesji brak wpisów o stanach (%d linii)", len(lines))

    def _cfg_dir(self, key: str, default: str) -> Path:
        d = _ini_value(self.home / "retroarch.cfg", key)
        if d.startswith(":"):
            d = str(self.home) + d[1:]
        p = Path(d) if d and d != "default" else self.home / default
        return self._r(p) if key in ("savefile_directory", "savestate_directory") else p

    def settings_files(self) -> list:
        return ["retroarch.cfg", "config"]

    def machine_keys(self) -> dict:
        return {"retroarch.cfg": [("", r".*(_directory|_path|_dir)|video_driver|video_adapter_index|"
                                       r"audio_driver|audio_device|video_monitor_index|cheevos_.*")]}

    def secret_keys(self) -> dict:
        return {"retroarch.cfg": [("", r"cheevos_password|cheevos_token|netplay_password|"
                                       r"netplay_spectate_password|.*_api_key|.*_auth_token")]}

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

    def prefix_of(self, filename: str) -> str:
        """„Chrono_Trigger_(USA)_(MSU1).state1” → „Chrono_Trigger_(USA)_(MSU1).state” — nazwa
        od ROM-u w środku ZIP-a bywa inna niż gry; poznana po grze, lista stanów ją zna."""
        m = re.match(r"^(.+\.state)\d*(\.auto)?$", filename)
        return m.group(1) if m else ""

    def start_state_args(self, run_dir: Path, state: Path, pad_order: list | None = None) -> list:
        name = state.name
        if name.endswith(".auto"):
            return self.launch_args(run_dir, True, pad_order)
        # Wybrany stan jako autozapis wczytywany przy starcie — bez --entryslot: ten ustawiał
        # bieżący slot na wybrany (np. 4), więc następny zapis szedł do 5 i nadpisywał istniejący
        # stan. Bez niego savestate_auto_index ustawia slot na najwyższy, nowy zapis = kolejny numer.
        m = re.match(r"^(.+)\.state\d*$", name)
        if not m:
            return self.launch_args(run_dir, False, pad_order)
        auto = state.with_name(m.group(1) + ".state.auto")
        if auto.exists():                       # poprzedni autozapis — kopia obok (RetroArch i tak go nadpisze przy wyjściu)
            shutil.copy2(auto, auto.with_name(auto.name + ".emustart-bak"))
        shutil.copy2(state, auto)
        png = state.with_name(name + ".png")
        if png.is_file():
            shutil.copy2(png, auto.with_name(auto.name + ".png"))
        return self.launch_args(run_dir, True, pad_order)

    def quit(self, proc) -> None:
        self._cmd("QUIT")
        time.sleep(0.4)
        self._cmd("QUIT")     # quit_press_twice


class DuckStation(Adapter):
    family = "duckstation"
    pause_key = "SPACE"
    state_glob = "*.sav"

    def first_run(self, cfg: dict) -> None:
        bios = self._bios_from_server(cfg, {"core": "swanstation"}, self.home / "bios")
        ini = self._ini()
        changes = {("Main", "SetupWizardIncomplete"): "false", ("AutoUpdater", "CheckAtStartup"): "false"}
        if not ini.is_file():
            # bez wersji ustawień DuckStation uznałby plik za stary i przywrócił domyślne (z kreatorem)
            changes[("Main", "SettingsVersion")] = "3"
            changes[("BIOS", "SearchDirectory")] = "bios"
        ini_set(ini, changes)
        if bios:
            log.info("DuckStation: BIOS-y z serwera (%d), bez kreatora", len(bios))

    def policy_ini(self) -> Path:
        return self._ini()

    def _ini(self) -> Path:
        portable = (self.home / "portable.txt").exists() or (self.home / "settings.ini").exists()
        return (self.home if portable else Path.home() / "Documents" / "DuckStation") / "settings.ini"

    def save_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "SaveSelectedSaveState")) or "F2"

    def _hk(self, name: str, default: str = "") -> str:
        return _qt_key(_ini_value(self._ini(), name)) or default

    def menu_key(self) -> str:
        return self._hk("OpenPauseMenu", "ESCAPE")

    def slot_keys(self) -> tuple:
        return (self._hk("SelectNextSaveStateSlot", "F4"), self._hk("SelectPreviousSaveStateSlot", "F3"))

    def screenshot_key(self) -> str:
        return self._hk("Screenshot", "F10")

    def record_key(self) -> str:
        return self._hk("ToggleMediaCapture")

    def ff_key(self) -> str:
        return self._hk("ToggleFastForward")

    def prepare_hotkeys(self) -> None:
        _bind_missing(self._ini(), "Hotkeys", {"ToggleFastForward": "Keyboard/Control & Keyboard/F11",
                                               "ToggleMediaCapture": "Keyboard/Control & Keyboard/F12"})

    def load_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "LoadSelectedSaveState")) or "F1"

    def _dir(self, section: str, key: str, default: str) -> Path:
        p = Path(ini_section(self._ini(), section).get(key, "") or default)
        return self._r(p if p.is_absolute() else self._ini().parent / p)

    def state_dirs(self) -> list:
        return [self._dir("Folders", "SaveStates", "savestates")]

    def memcard_dir(self) -> Path:
        return self._dir("MemoryCards", "Directory", "memcards")

    def can_redirect(self) -> bool:
        return True

    def redirect_session(self, card: str = ""):
        """Foldery profilu i karta pamięci gry (Card1Type = Shared, Card1Path = <gra>_1.mcd):
        nazwa karty od EmuStart, stała na każdym urządzeniu (DuckStation „per tytuł” bierze
        tytuł z własnej bazy, inny niż nazwa gry w bibliotece)."""
        ini = self._ini()
        if not ini.is_file() or not self.redirect:
            return None
        mem, states = self.memcard_dir(), self._dir("Folders", "SaveStates", "savestates")
        changes = {("MemoryCards", "Directory"): str(mem), ("Folders", "SaveStates"): str(states)}
        mc = ini_section(ini, "MemoryCards")
        if card:
            # karta gry z dotychczasowej: tej gry (karta „per tytuł”) albo wspólnej
            old_type = mc.get("Card1Type", "PerGameTitle")
            shared = mem / (mc.get("Card1Path") or "shared_card_1.mcd")
            same = [f for f in sorted(mem.glob("*_1.mcd")) if _bare(f.stem[:-2]) == _bare(card)] if mem.is_dir() else []
            _seed_card(mem / f"{card}_1.mcd", same + ([shared] if old_type == "Shared" else []))
            changes[("MemoryCards", "Card1Type")] = "Shared"
            changes[("MemoryCards", "Card1Path")] = f"{card}_1.mcd"
            if mc.get("Card2Type", "None") not in ("None", ""):
                changes[("MemoryCards", "Card2Type")] = "Shared"
                changes[("MemoryCards", "Card2Path")] = f"{card}_2.mcd"
        return _session_ini(ini, changes, {
            ("MemoryCards", "Directory"): "memcards", ("Folders", "SaveStates"): "savestates",
            ("MemoryCards", "Card1Type"): "PerGameTitle", ("MemoryCards", "Card1Path"): "shared_card_1.mcd",
            ("MemoryCards", "Card2Type"): "None", ("MemoryCards", "Card2Path"): "shared_card_2.mcd"})

    def settings_base(self) -> Path:
        return self._ini().parent

    def settings_files(self) -> list:
        return ["settings.ini", "gamesettings", "inputprofiles"]

    def machine_keys(self) -> dict:
        return {"settings.ini": [("Folders|BIOS|GameList|UI|GameListTableView|AutoUpdater", ".*"),
                                 ("MemoryCards", "Directory"), ("GPU", "Adapter"),
                                 ("Audio", "Backend|Driver|OutputDevice"), ("Main", "SettingsVersion"),
                                 ("Cheevos", "Enabled|Username|Token|LoginTimestamp|ChallengeMode")]}

    def _portable(self) -> bool:
        return self._ini().parent == self.home

    def secret_keys(self) -> dict:
        return {"settings.ini": [("Cheevos", "Token")]}

    def read_cheevos(self) -> dict:
        # DuckStation trzyma token zaszyfrowany (dstoken) — oddajemy zwykły
        c = ini_section(self._ini(), "Cheevos")
        user = c.get("Username", "")
        token = dstoken.decrypt(c.get("Token", ""), user, self._portable()) if user else ""
        return {"user": user, "token": token} if user and token else {}

    def apply_cheevos(self, ra: dict | None) -> None:
        on = bool(ra and ra.get("user") and ra.get("token"))
        token = dstoken.encrypt(ra["token"], ra["user"], self._portable()) if on else ""
        ini_set(self._ini(), {("Cheevos", "Enabled"): "true" if on else "false",
                              ("Cheevos", "Username"): ra["user"] if on else "",
                              ("Cheevos", "Token"): token,
                              ("Cheevos", "LoginTimestamp"): str(int(time.time())) if on else "0",
                              ("Cheevos", "ChallengeMode"): "true" if on and ra.get("hardcore") else "false"})

    def resume_args(self, state: Path) -> list:
        return ["-statefile", str(state)]

    def pause(self) -> None:
        winutil.send_keys(_qt_key(_ini_value(self._ini(), "TogglePause")) or "SPACE")

    def save_dirs(self) -> list:
        return [self.memcard_dir(), self._dir("Folders", "SaveStates", "savestates")]

    def state_patterns(self, game: dict, meta: dict) -> list:
        serial = _serial(meta)
        return [f"{serial}_*.sav"] if serial else []

    def prefix_of(self, filename: str) -> str:
        return filename.rsplit("_", 1)[0] + "_" if "_" in filename else ""

    def remap_pads(self, order: list):
        return _remap_sdl_pads(self, order)


class PCSX2(Adapter):
    family = "pcsx2"
    state_glob = "*.p2s"

    def first_run(self, cfg: dict) -> None:
        bios = self._bios_from_server(cfg, {"dir": "pcsx2/bios"}, self.home / "bios")
        ini = self._ini()
        changes = {("UI", "SetupWizardIncomplete"): "false", ("AutoUpdater", "CheckAtStartup"): "false"}
        if not ini.is_file():
            changes[("UI", "SettingsVersion")] = "1"       # inaczej PCSX2 przywróciłby domyślne
            changes[("Folders", "Bios")] = "bios"
        cur = ini_section(ini, "Filenames").get("BIOS", "") if ini.is_file() else ""
        main = [b for b in bios if b.lower().endswith(".bin")]
        if main and (not cur or not (self.home / "bios" / cur).is_file()):
            changes[("Filenames", "BIOS")] = main[0]
        ini_set(ini, changes)
        if bios:
            log.info("PCSX2: BIOS z serwera (%s), bez kreatora", ", ".join(main) or "—")

    def policy_ini(self) -> Path:
        return self._ini()

    def _ini(self) -> Path:
        if (self.home / "portable.ini").exists() or (self.home / "portable.txt").exists():
            return self.home / "inis" / "PCSX2.ini"
        return Path.home() / "Documents" / "PCSX2" / "inis" / "PCSX2.ini"

    def save_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "SaveStateToSlot")) or "F1"

    def _hk(self, name: str, default: str = "") -> str:
        return _qt_key(_ini_value(self._ini(), name)) or default

    def menu_key(self) -> str:
        return self._hk("OpenPauseMenu", "ESCAPE")

    def slot_keys(self) -> tuple:
        return (self._hk("NextSaveStateSlot", "F2"), self._hk("PreviousSaveStateSlot", "SHIFT+F2"))

    def screenshot_key(self) -> str:
        return self._hk("Screenshot", "F8")

    def record_key(self) -> str:
        return self._hk("ToggleVideoCapture")

    def ff_key(self) -> str:
        return self._hk("ToggleTurbo", "TAB")

    def prepare_hotkeys(self) -> None:
        _bind_missing(self._ini(), "Hotkeys", {"ToggleVideoCapture": "Keyboard/Control & Keyboard/F12"})

    def load_key(self) -> str:
        return _qt_key(_ini_value(self._ini(), "LoadStateFromSlot")) or "F3"

    def _dir(self, key: str, default: str) -> Path:
        p = Path(ini_section(self._ini(), "Folders").get(key, "") or default)
        return self._r(p if p.is_absolute() else self._ini().parent.parent / p)

    def state_dirs(self) -> list:
        return [self._dir("Savestates", "sstates")]

    def memcard_dir(self) -> Path:
        return self._dir("MemoryCards", "memcards")

    def can_redirect(self) -> bool:
        return True

    def redirect_session(self, card: str = ""):
        """Foldery profilu i karta pamięci osobna dla gry (Slot1_Filename = <gra>.ps2) —
        wspólna karta wszystkich gier dawałaby konflikt synchronizacji przy każdej zmianie.
        Pierwsza karta gry to kopia dotychczasowej wspólnej (ma też zapisy tej gry)."""
        ini = self._ini()
        if not ini.is_file() or not self.redirect:
            return None
        mem = self.memcard_dir()
        changes = {("Folders", "MemoryCards"): str(mem), ("Folders", "Savestates"): str(self._dir("Savestates", "sstates"))}
        if card:
            cur = ini_section(ini, "MemoryCards").get("Slot1_Filename") or "Mcd001.ps2"
            _seed_card(mem / f"{card}.ps2", [mem / cur])
            changes[("MemoryCards", "Slot1_Filename")] = f"{card}.ps2"
        return _session_ini(ini, changes, {
            ("Folders", "MemoryCards"): "memcards", ("Folders", "Savestates"): "sstates",
            ("MemoryCards", "Slot1_Filename"): "Mcd001.ps2"})

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

    def secret_keys(self) -> dict:
        return {"inis/PCSX2.ini": [("Achievements", "Token")]}

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
        return [self.memcard_dir(), self._dir("Savestates", "sstates")]

    def state_patterns(self, game: dict, meta: dict) -> list:
        serial = _serial(meta)
        return [f"{serial} (*).*.p2s"] if serial else []

    def prefix_of(self, filename: str) -> str:
        parts = filename.rsplit(".", 2)
        return parts[0] + "." if len(parts) == 3 else ""

    def remap_pads(self, order: list):
        return _remap_sdl_pads(self, order)


class Dolphin(Adapter):
    family = "dolphin"
    state_glob = "*.s[0-9][0-9]"

    def policy_ini(self) -> Path:
        return self._user() / "Config" / "Dolphin.ini"

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

    _HK = {"Other State Hotkeys/Increase Selected State Slot": "@(Ctrl+F11)",
           "Other State Hotkeys/Decrease Selected State Slot": "@(Ctrl+F10)",
           "Save State/Save to Selected Slot": "@(Ctrl+F12)",
           "Load State/Load from Selected Slot": "@(Ctrl+F9)"}

    def prepare_hotkeys(self) -> None:
        _bind_missing(self._user() / "Config" / "Hotkeys.ini", "Hotkeys", self._HK)

    def slot_keys(self) -> tuple:
        return (self._hotkey("Other State Hotkeys/Increase Selected State Slot", "CTRL+F11"),
                self._hotkey("Other State Hotkeys/Decrease Selected State Slot", "CTRL+F10"))

    def save_here_key(self) -> str:
        return self._hotkey("Save State/Save to Selected Slot", "CTRL+F12")

    def load_here_key(self) -> str:
        return self._hotkey("Load State/Load from Selected Slot", "CTRL+F9")

    def screenshot_key(self) -> str:
        return self._hotkey("General/Take Screenshot", "F9")

    def ff_key(self) -> str:
        return self._hotkey("Emulation Speed/Disable Emulation Speed Limit", "TAB")

    def ff_hold(self) -> bool:
        return True

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

    # nazwy, pod którymi SDL pokazuje pady obsługiwane własnym sterownikiem (HIDAPI); inne pady HID
    # mają nazwę z układu EmuStart (sdlpads.mapping)
    SDL_NAMES = {(0x054C, 0x05C4): "PS4 Controller", (0x054C, 0x09CC): "PS4 Controller",
                 (0x054C, 0x0BA0): "PS4 Controller", (0x054C, 0x0CE6): "PS5 Controller",
                 (0x054C, 0x0DF2): "PS5 Controller", (0x057E, 0x2009): "Nintendo Switch Pro Controller"}

    def remap_pads(self, order: list):
        path = self._user() / "Config" / "GCPadNew.ini"
        changes = {}
        names: list = []
        for p, slot in enumerate(order[:4]):
            sec = f"GCPad{p + 1}"
            cur = ini_section(path, sec)
            dev = cur.get("Device", "")
            if pads.is_hid(slot):
                # pad HID jest w Dolphinie tylko przez SDL: SDL/<n>/<nazwa SDL> (n — kolejny pad o tej
                # nazwie); przypisania XInput → nazwy SDL (różnią się tylko A/B/X/Y: S/E/W/N)
                pad = pads.hid_pad(slot)
                if pad is None or not re.match(r"^(XInput|SDL)/\d+/", dev):
                    continue
                name = self.SDL_NAMES.get((pad.vid, pad.pid), pad.name.replace(",", " "))
                nd = f"SDL/{names.count(name)}/{name}"
                names.append(name)
                if dev.startswith("XInput/"):
                    for k, v in cur.items():
                        nv = re.sub(r"\bButton ([ABXY])\b", lambda m: "Button " + "SEWN"["ABXY".index(m.group(1))], v)
                        if nv != v:
                            changes[(sec, k)] = nv
            else:
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

    _NK = {61: "TAB", 68: "GRAVE", 111: "ESCAPE", 113: "CTRL", 114: "CTRL", 59: "SHIFT", 60: "SHIFT"}

    def _controls(self) -> Path:
        return self.home / "memstick" / "PSP" / "SYSTEM" / "controls.ini"

    def _key(self, name: str, default: str) -> str:
        raw = _ini_value(self._controls(), name)
        for part in raw.split(","):              # kilka przypisań — pierwsze z klawiatury (urządzenie 1)
            keys = []
            for one in part.split(":"):          # kilka klawiszy naraz
                dev, _, code = one.partition("-")
                if dev != "1" or not code.isdigit():
                    break
                c = int(code)
                keys.append(f"F{c - self._ANDROID_F1 + 1}" if 0 <= c - self._ANDROID_F1 < 12 else self._NK.get(c, ""))
            else:
                if keys and all(keys):
                    return "+".join(keys)
        return default

    def prepare_hotkeys(self) -> None:
        # Ctrl + F10 / F11 / F12 (kody Androida: Ctrl 113, F10 140, F11 141, F12 142)
        _bind_missing(self._controls(), "ControlMapping", {"Previous Slot": "1-113:1-140",
                                                           "Audio/Video Recording": "1-113:1-141",
                                                           "Screenshot": "1-113:1-142"})

    def screenshot_key(self) -> str:
        return self._key("Screenshot", "")

    def record_key(self) -> str:
        return self._key("Audio/Video Recording", "")

    def ff_key(self) -> str:
        return self._key("SpeedToggle", "GRAVE")

    def save_key(self) -> str:
        return self._key("Save State", "F2")

    def menu_key(self) -> str:
        return "ESCAPE"                     # pauza z menu PPSSPP

    def slot_keys(self) -> tuple:
        return (self._key("Next Slot", "F3"), self._key("Previous Slot", ""))

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


def state_thumb(state: Path) -> str | None:
    """Obrazek stanu gry zapisany przez emulator → plik PNG w pamięci grafik (podgląd przy
    wyborze zapisu). RetroArch: `<stan>.png` obok (savestate_thumbnail_enable); PCSX2: zrzut
    w archiwum .p2s; DuckStation: zrzut w nagłówku .sav (nieskompresowany). None = brak."""
    import hashlib
    from emustart import mediastore
    try:
        st = state.stat()
    except OSError:
        return None
    name = hashlib.sha1(f"{state}|{st.st_mtime_ns}".encode("utf-8", "replace")).hexdigest()[:20] + ".png"
    if mediastore.exists("_stany", name):
        return name
    data = _state_png(state)
    if not data:
        return None
    try:
        mediastore.put("_stany", name, data)
    except Exception:
        return None
    return name


def _state_png(state: Path) -> bytes | None:
    side = state.with_name(state.name + ".png")
    if side.is_file():                                     # RetroArch
        try:
            return side.read_bytes()
        except OSError:
            return None
    suf = state.suffix.lower()
    if suf == ".p2s":                                      # PCSX2: archiwum z zrzutem ekranu
        import zipfile
        try:
            with zipfile.ZipFile(state) as z:
                name = next((n for n in z.namelist() if n.lower() == "screenshot.png"), None)
                return z.read(name) if name else None
        except (OSError, zipfile.BadZipFile, KeyError):
            return None
    if suf == ".sav":                                      # DuckStation: RGBA w nagłówku
        return _duckstation_png(state)
    return None


def _duckstation_png(state: Path) -> bytes | None:
    import io
    import struct
    try:
        with open(state, "rb") as f:
            head = f.read(200)
            if len(head) < 200 or head[:4] != b"DUCS":
                return None
            w, h, size, off = struct.unpack_from("<4I", head, 184)
            if not (0 < w <= 4096 and 0 < h <= 4096 and size == w * h * 4):
                return None                                 # inny format (np. skompresowany zrzut)
            f.seek(off)
            raw = f.read(size)
        if len(raw) != size:
            return None
        from PIL import Image
        buf = io.BytesIO()
        Image.frombytes("RGBA", (w, h), raw).save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


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
