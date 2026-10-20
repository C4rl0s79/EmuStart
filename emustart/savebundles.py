"""savebundles — które pliki na tym komputerze należą do paczki zapisów gry (emustart/savesync.py).

Paczka to pełny zestaw plików jednej gry dla emulatora (i rdzenia RetroArcha) jednego rodzaju:

    RetroArch    saves     <folder zapisów>[/<rdzeń>]/<plik gry>.srm, .sav, .rtc…   (per rdzeń)
    RetroArch    states    <folder stanów>[/<rdzeń>]/<plik gry>.state*, miniatury   (per rdzeń)
    DuckStation  memcards  <memcards>/<plik gry>_1.mcd, _2.mcd    (karta osobna dla gry)
    PCSX2        memcards  <memcards>/<plik gry>.ps2              (karta osobna dla gry)

Nic poza tym: bez konfiguracji, cache, shaderów i plików innych gier. Zmiany wykrywa skrót
zawartości zestawu (SHA-256 nazw i treści), nie czas modyfikacji.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1 << 20
# zapisy w grze RetroArcha (nie stany): baterie, zegary, EEPROM-y, karty rdzeni
RA_SAVE_EXTS = {"srm", "sav", "rtc", "eep", "fla", "mpk", "sra", "nv", "mcr", "bkr", "bcr", "brm",
                "dsv", "dsa", "ram", "ldci", "lcd"}


@dataclass
class Bundle:
    emulator_id: str
    core_id: str
    kind: str
    folder: Path
    stems: set = field(default_factory=set)
    files: list = field(default_factory=list)      # [(ścieżka, nazwa w paczce)]

    @property
    def key(self) -> tuple:
        return self.emulator_id, self.core_id, self.kind


def norm(s: str) -> str:
    """„Chrono Trigger (USA)” = „Chrono_Trigger_(USA)” (RetroArch nazywa pliki od ROM-u w ZIP-ie)."""
    return re.sub(r"[\s_]+", " ", s or "").strip().lower()


def _stem_of(name: str) -> str:
    """Nazwa gry z nazwy pliku zapisu / stanu: „Gra (USA).state3.png” → „Gra (USA)”."""
    n = re.sub(r"\.png$", "", name, flags=re.I)
    n = re.sub(r"\.state\d*(\.auto)?$", "", n, flags=re.I)
    return n.rsplit(".", 1)[0] if "." in n else n


def _collect(b: Bundle, match) -> Bundle:
    if b.folder.is_dir():
        for f in sorted(b.folder.iterdir()):
            if f.is_file() and match(f.name) and not f.name.endswith((".emustart-new", ".tmp", ".part")):
                b.files.append((f, f.name))
    return b


def retroarch(adapter, core: str, content: Path, stems: set) -> list:
    """Zapisy i stany RetroArcha tej gry dla rdzenia `core` — w folderach, w których szuka ich
    ten RetroArch (ustawienia sortowania po rdzeniu / po folderze gry)."""
    from emustart import rastates
    from emustart.ingame import _ini_value
    cfg = adapter.home / "retroarch.cfg"
    names = {norm(s) for s in stems if s}

    def folder(key: str, default: str, by_content: str, by_core: str) -> Path:
        d = Path(adapter._cfg_dir(key, default))
        if _ini_value(cfg, by_content) == "true":
            d = d / content.parent.name
        if _ini_value(cfg, by_core) == "true" and core:
            d = d / rastates.core_name(adapter.home, core)
        return d

    saves = Bundle("retroarch", core, "saves",
                   folder("savefile_directory", "saves", "sort_savefiles_by_content_enable", "sort_savefiles_enable"), names)
    states = Bundle("retroarch", core, "states",
                    folder("savestate_directory", "states", "sort_savestates_by_content_enable", "sort_savestates_enable"), names)
    _collect(saves, lambda n: n.rsplit(".", 1)[-1].lower() in RA_SAVE_EXTS and norm(n.rsplit(".", 1)[0]) in names)
    _collect(states, lambda n: re.search(r"\.state\d*(\.auto)?(\.png)?$", n, re.I) is not None
             and norm(_stem_of(n)) in names)
    return [saves, states]


def duckstation_card(stem: str) -> str:
    return f"{stem}_1.mcd"


def duckstation(adapter, stem: str) -> list:
    """Karty pamięci tej gry (DuckStation: karta osobna dla gry, nazwa od pliku gry)."""
    b = Bundle("duckstation", "", "memcards", adapter.memcard_dir(), {norm(stem)})
    return [_collect(b, lambda n: re.fullmatch(r"(.+)_[12]\.mcd", n, re.I) is not None
                     and norm(re.fullmatch(r"(.+)_[12]\.mcd", n, re.I).group(1)) == norm(stem))]


def pcsx2_card(stem: str) -> str:
    return f"{stem}.ps2"


def pcsx2(adapter, stem: str) -> list:
    """Karta pamięci tej gry (PCSX2: karta osobna dla gry — wspólna dawałaby konflikt przy
    każdej zmianie na dwóch urządzeniach)."""
    b = Bundle("pcsx2", "", "memcards", adapter.memcard_dir(), {norm(stem)})
    return [_collect(b, lambda n: n.lower() == pcsx2_card(stem).lower())]


def for_game(adapter, core: str, content: Path, game: dict) -> list:
    """Paczki tej gry dla emulatora `adapter` (pusta lista — emulator jeszcze bez synchronizacji
    paczkami, działa dotychczasowa synchronizacja folderów profilu)."""
    stems = {Path(content).stem, game.get("name", ""), Path(game.get("rel", "")).stem}
    if adapter.family == "retroarch":
        return retroarch(adapter, core, content, stems) if core else []
    if adapter.family == "duckstation":
        return duckstation(adapter, card_stem(content, game))
    if adapter.family == "pcsx2":
        return pcsx2(adapter, card_stem(content, game))
    return []


def card_stem(content: Path, game: dict) -> str:
    """Nazwa karty pamięci gry — od nazwy gry (stała między urządzeniami; plik gry bywa w
    pamięci podręcznej albo rozpakowany pod inną nazwą)."""
    s = game.get("name") or Path(content).stem
    s = re.sub(r"\s*[([](disc|disk|cd)\s*\d+[^)\]]*[)\]]", "", s, flags=re.I)   # dyski jednej gry — jedna karta
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", s).strip(" .")
    return s[:120] or "gra"


# folder emulatora, którym zajmują się paczki — dotychczasowa synchronizacja folderu go pomija
COVERED = {"retroarch": {"saves", "states"}, "duckstation": {"memcards"}, "pcsx2": {"memcards"}}


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for buf in iter(lambda: f.read(CHUNK), b""):
            h.update(buf)
    return h.hexdigest()


def content_hash(files: list) -> str:
    """Skrót zestawu: nazwy i treści plików (kolejność bez znaczenia); pusty zestaw — ''."""
    if not files:
        return ""
    h = hashlib.sha256()
    for path, name in sorted(files, key=lambda x: x[1].lower()):
        h.update(name.lower().encode("utf-8") + b"\0" + file_sha(Path(path)).encode() + b"\n")
    return h.hexdigest()
