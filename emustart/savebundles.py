"""savebundles — które pliki na tym komputerze należą do paczki zapisów gry (emustart/savesync.py).

Paczka to pełny zestaw plików jednej gry dla emulatora (i rdzenia RetroArcha) jednego rodzaju:

    RetroArch    saves     <folder zapisów>[/<rdzeń>]/<plik gry>.srm, .sav, .rtc…   (per rdzeń)
    RetroArch    states    <folder stanów>[/<rdzeń>]/<plik gry>.state*, miniatury   (per rdzeń)
    DuckStation  memcards  <memcards>/<gra>_1.mcd, _2.mcd    (karta osobna dla gry)
    PCSX2        memcards  <memcards>/<gra>.ps2              (karta osobna dla gry)

Nic poza tym: bez konfiguracji, cache, shaderów i plików innych gier. W paczce pliki mają
nazwy umowne („game.srm”, „game.state3.png”, „game_1.mcd”, „game.ps2”) — urządzenia nazywają
grę różnie (plik w ZIP-ie, rozpakowany, .m3u), przy pobraniu dostają nazwę od pliku gry na
tym urządzeniu. Zmiany wykrywa skrót zawartości (nazwy umowne i treść), nie czas modyfikacji.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1 << 20
ARC = "game"                     # nazwa umowna pliku gry w paczce
# zapisy w grze RetroArcha (nie stany): baterie, zegary, EEPROM-y, karty rdzeni
RA_SAVE_EXTS = {"srm", "sav", "rtc", "eep", "fla", "mpk", "sra", "nv", "mcr", "bkr", "bcr", "brm",
                "dsv", "dsa", "ram", "ldci", "lcd"}
_RA_STATE = re.compile(r"(.+?)(\.state\d*(?:\.auto)?(?:\.png)?)", re.I)
_CORE_ARG = re.compile(r'-L\s+"?([^"]*?)([\w.+-]+?)_libretro\.(?:dll|so|dylib)"?', re.I)
SKIP = (".emustart-new", ".emustart-tmp", ".tmp", ".part")


@dataclass
class Bundle:
    emulator_id: str
    core_id: str
    kind: str
    folder: Path
    stem: str                                       # nazwa pliku gry na tym urządzeniu
    names: list = field(default_factory=list)       # nazwy gry, po których rozpoznajemy pliki
    files: list = field(default_factory=list)       # [(ścieżka, nazwa w paczce)]

    @property
    def key(self) -> tuple:
        return self.emulator_id, self.core_id, self.kind

    def local_path(self, arc: str) -> Path:
        """Ścieżka na tym urządzeniu dla pliku paczki („game.state2” → „<plik gry>.state2”)."""
        if not arc.startswith(ARC) or "/" in arc or "\\" in arc or ".." in arc:
            raise ValueError(f"nieoczekiwana nazwa w paczce: {arc}")
        return self.folder / (self.stem + arc[len(ARC):])

    def spec(self) -> dict:
        return {"emu": self.emulator_id, "core": self.core_id, "kind": self.kind,
                "folder": str(self.folder), "stem": self.stem, "names": list(self.names)}

    def collect(self) -> "Bundle":
        """Pliki paczki w folderze (od nowa)."""
        self.files = []
        if not self.folder.is_dir():
            return self
        names = {norm(n) for n in self.names if n} | {norm(self.stem)}
        mine = norm(self.stem)
        picked: dict = {}
        for f in sorted(self.folder.iterdir()):
            if not f.is_file() or f.name.endswith(SKIP):
                continue
            hit = _match(self.emulator_id, self.kind, f.name, names)
            if not hit:
                continue
            raw, arc = hit
            # dwa pliki o tej samej nazwie umownej (np. od nazwy gry i od pliku) — wygrywa
            # nazwa od pliku gry na tym urządzeniu (tej użyje emulator)
            if arc in picked and norm(raw) != mine:
                continue
            picked[arc] = f
        self.files = [(p, a) for a, p in sorted(picked.items())]
        return self


def from_spec(spec: dict) -> Bundle:
    return Bundle(spec["emu"], spec.get("core", ""), spec["kind"], Path(spec["folder"]),
                  spec["stem"], list(spec.get("names") or [])).collect()


def norm(s: str) -> str:
    """„Chrono Trigger (USA)” = „Chrono_Trigger_(USA)” (RetroArch nazywa pliki od ROM-u w ZIP-ie)."""
    return re.sub(r"[\s_]+", " ", s or "").strip().lower()


def _match(emu: str, kind: str, name: str, names: set):
    """(nazwa gry w pliku, nazwa w paczce) albo None — plik nie należy do paczki tej gry."""
    if emu == "retroarch" and kind == "saves":
        base, _, ext = name.rpartition(".")
        if base and ext.lower() in RA_SAVE_EXTS and norm(base) in names:
            return base, f"{ARC}.{ext.lower()}"
    elif emu == "retroarch" and kind == "states":
        m = _RA_STATE.fullmatch(name)
        if m and norm(m.group(1)) in names:
            return m.group(1), ARC + m.group(2).lower()
    elif emu == "duckstation" and kind == "memcards":
        m = re.fullmatch(r"(.+)(_[12])\.mcd", name, re.I)
        if m and norm(m.group(1)) in names:
            return m.group(1), f"{ARC}{m.group(2)}.mcd"
    elif emu == "pcsx2" and kind == "memcards":
        m = re.fullmatch(r"(.+)\.ps2", name, re.I)
        if m and norm(m.group(1)) in names:
            return m.group(1), f"{ARC}.ps2"
    return None


def core_id(args: str) -> str:
    """Rdzeń z parametrów RetroArcha (-L …\\cores\\bsnes_libretro.dll → bsnes)."""
    m = _CORE_ARG.search(args or "")
    return m.group(2).lower() if m else ""


def core_name(ra_home: Path, core: str) -> str:
    """Nazwa folderu rdzenia, której RetroArch używa przy sortowaniu (corename z .info)."""
    f = ra_home / "info" / f"{core}_libretro.info"
    try:
        m = re.search(r'^corename\s*=\s*"([^"]*)"', f.read_text(encoding="utf-8", errors="replace"), re.M)
        if m and m.group(1):
            return m.group(1)
    except OSError:
        pass
    return core


def retroarch(adapter, core: str, content: Path, names: list) -> list:
    """Zapisy i stany RetroArcha tej gry dla rdzenia `core` — w folderach, w których szuka ich
    ten RetroArch (ustawienia sortowania po rdzeniu / po folderze gry)."""
    from emustart.ingame import _ini_value
    cfg = adapter.home / "retroarch.cfg"

    def folder(key: str, default: str, by_content: str, by_core: str) -> Path:
        d = Path(adapter._cfg_dir(key, default))
        if _ini_value(cfg, by_content) == "true":
            d = d / Path(content).parent.name
        if _ini_value(cfg, by_core) == "true" and core:
            d = d / core_name(adapter.home, core)
        return d

    stem = Path(content).stem
    return [Bundle("retroarch", core, "saves",
                   folder("savefile_directory", "saves", "sort_savefiles_by_content_enable", "sort_savefiles_enable"),
                   stem, names).collect(),
            Bundle("retroarch", core, "states",
                   folder("savestate_directory", "states", "sort_savestates_by_content_enable", "sort_savestates_enable"),
                   stem, names).collect()]


def for_game(adapter, core: str, content: Path, game: dict) -> list:
    """Paczki tej gry dla emulatora `adapter` (pusta lista — emulator jeszcze bez synchronizacji
    paczkami, działa dotychczasowa synchronizacja folderów profilu)."""
    if adapter.family == "retroarch":
        names = sorted({Path(content).stem, game.get("name", ""), Path(game.get("rel", "")).stem} - {""})
        return retroarch(adapter, core, content, names) if core else []
    if adapter.family in ("duckstation", "pcsx2") and adapter.memcard_dir():
        card = card_stem(content, game)
        return [Bundle(adapter.family, "", "memcards", adapter.memcard_dir(), card, [card]).collect()]
    return []


def card_stem(content: Path, game: dict) -> str:
    """Nazwa karty pamięci gry — od nazwy gry (stała między urządzeniami; plik gry bywa w
    pamięci podręcznej albo rozpakowany pod inną nazwą)."""
    s = game.get("name") or Path(content).stem
    s = re.sub(r"\s*[([](disc|disk|cd)\s*\d+[^)\]]*[)\]]", "", s, flags=re.I)   # dyski jednej gry — jedna karta
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", s).strip(" .")
    return s[:120] or "gra"


def covered_dirs(adapter) -> list:
    """Foldery emulatora, którymi zajmują się paczki — dotychczasowa synchronizacja folderów
    profilu je pomija (jedna prawda: serwer)."""
    if adapter.family == "retroarch":
        return list(adapter.save_dirs())
    if adapter.family in ("duckstation", "pcsx2"):
        return [adapter.memcard_dir()]
    return []


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for buf in iter(lambda: f.read(CHUNK), b""):
            h.update(buf)
    return h.hexdigest()


def content_hash(files: list) -> str:
    """Skrót zestawu: nazwy w paczce i treści plików; pusty zestaw — ''."""
    if not files:
        return ""
    h = hashlib.sha256()
    for path, arc in sorted(files, key=lambda x: x[1]):
        h.update(arc.encode("utf-8") + b"\0" + file_sha(Path(path)).encode() + b"\n")
    return h.hexdigest()
