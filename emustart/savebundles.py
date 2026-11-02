"""savebundles — które pliki na tym komputerze należą do paczki zapisów gry (emustart/savesync.py).

Paczka to pełny zestaw plików jednej gry dla emulatora (i rdzenia RetroArcha) jednego rodzaju:

    RetroArch    saves     <folder zapisów>[/<rdzeń>]/<plik gry>.srm, .sav, .rtc…   (per rdzeń)
    RetroArch    states    <folder stanów>[/<rdzeń>]/<plik gry>.state*, miniatury   (per rdzeń)
    DuckStation  memcards  <memcards>/<gra>_1.mcd, _2.mcd    (karta osobna dla gry)
    PCSX2        memcards  <memcards>/<gra>.ps2              (karta osobna dla gry)

Kosz slotu (rodzaj „trash”, emustart/saveslots.py): stany usunięte przez gracza, w folderze
profilu, nazwy „<12 znaków SHA-256>.<nazwa w paczce>” — bez nazwy gry.

Nic poza tym: bez konfiguracji, cache, shaderów i plików innych gier. W paczce pliki mają
nazwy umowne („game.srm”, „game.state3.png”, „game_1.mcd”, „game.ps2”) — urządzenia nazywają
grę różnie (plik w ZIP-ie, rozpakowany, .m3u), przy pobraniu dostają nazwę od pliku gry na
tym urządzeniu. Zmiany wykrywa skrót zawartości (nazwy umowne i treść), nie czas modyfikacji.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1 << 20
ARC = "game"                     # nazwa umowna pliku gry w paczce
# zapisy w grze RetroArcha (nie stany): baterie, zegary, EEPROM-y, karty rdzeni
RA_SAVE_EXTS = {"srm", "sav", "rtc", "eep", "fla", "mpk", "sra", "nv", "mcr", "bkr", "bcr", "brm",
                "dsv", "dsa", "ram", "ldci", "lcd",
                "fs",       # FBNeo: NVRAM (<zapisy>/fbneo/<set>.fs)
                "ps2",      # LRPS2: karta gry przy wyłączonych wspólnych kartach (<zapisy>/<gra>.ps2)
                "nvr"}      # PUAE: NVRAM CD32 / CDTV (<zapisy>/<gra>.nvr)
# zapisy z przyrostkiem po nazwie gry: Flycast — karta VMU gry (<gra>.A1.bin). Kartridż pamięci Kronos
# (<gra>-ext1M.ram) — poza paczką: klient sprzed 0.55 zebrałby z paczki tylko <gra>.ram i wysłał ją niepełną
_RA_SAVE_SFX = (re.compile(r"^(.+)(\.[A-D][12]\.bin)$", re.I),)
_RA_STATE = re.compile(r"(.+?)(\.state\d*(?:\.auto)?(?:\.png)?)", re.I)
# stan PCSX2 / ArmSX2: „SLUS-20851 (39B574F0).03.p2s”, wznowienie PCSX2 (.resume), autozapis ArmSX2
_P2S = re.compile(r"^(?P<prefix>.+?)\.(?P<slot>\d{2}|resume|autosave(?:\.\d)?)\.p2s$", re.I)
_TRASH = re.compile(r"^[0-9a-f]{12}\.[^/\\:]+$")
_CORE_ARG = re.compile(r'-L\s+"?([^"]*?)([\w.+-]+?)_libretro\.(?:dll|so|dylib)"?', re.I)
SKIP = (".emustart-new", ".emustart-tmp", ".tmp", ".part")
# stany Dolphina („GKBEAF.s05”, ID gry z płyty) i PPSSPP („ULUS10336_1.ppst”, numer gry)
_DOLPHIN_STATE = re.compile(r"^(?P<prefix>[A-Z0-9]{6})\.s(?P<slot>\d{2})$", re.I)
_PPSSPP_STATE = re.compile(r"^(?P<prefix>[A-Z]{4}\d{5})_(?P<slot>\d{1,2})\.ppst$", re.I)
# paczki z nazwami jak na urządzeniu (te same na każdym: od numeru / ID gry, nie od nazwy pliku)
NATIVE = {("pcsx2", "states"), ("dolphin", "states"), ("dolphin", "saves"), ("ppsspp", "states"), ("ppsspp", "saves"),
          ("retroarch", "memcards")}       # nazwy w paczce jak na urządzeniu (sloty zapisów je zachowują)
_SAFE_DIR = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]{1,120}$")
# paczki z podfolderami (nazwa w paczce ze ścieżką: „ULUS10336DATA00/DATA.BIN”, „GC/USA/Card A/01-GKBE-x.gci”)
DEEP = {("dolphin", "saves"), ("ppsspp", "saves"),
        ("retroarch", "memcards")}       # rdzeń z zapisami w podfolderach gier (PUAE: WHDSaves/<gra>/…)
log = logging.getLogger("emustart.savebundles")


@dataclass
class Bundle:
    emulator_id: str
    core_id: str
    kind: str
    folder: Path
    stem: str                                       # nazwa pliku gry na tym urządzeniu
    names: list = field(default_factory=list)       # nazwy gry, po których rozpoznajemy pliki
    files: list = field(default_factory=list)       # [(ścieżka, nazwa w paczce)]
    # nazwy poznawane po grze (learn_snapshot / learn_after): „files” — karta <nazwa>.<port>.bin (Flycast:
    # ID gry z płyty), „dirs” — podfolder gry (PUAE: WHDSaves/<gra>); learn_game — gra w mapie nazw
    learn: str = ""
    learn_game: str = ""
    seen: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)     # podfoldery z paczki serwera — przyjęte po udanym zapisie

    def accept_pending(self) -> None:
        """Paczka z serwera zapisana w całości: jej podfoldery należą do tej gry (zapamiętane na urządzeniu)."""
        new = [n for n in self.pending if n not in self.names]
        self.pending = []
        if new:
            self.names += new
            if self.learn == "dirs":
                _remember(self.core_id, self.learn_game, new)

    @property
    def key(self) -> tuple:
        return self.emulator_id, self.core_id, self.kind

    def local_path(self, arc: str) -> Path:
        """Ścieżka na tym urządzeniu dla pliku paczki („game.state2” → „<plik gry>.state2”;
        stany PCSX2 — nazwa jak w paczce, od numeru płyty)."""
        if (self.emulator_id, self.kind) in DEEP:
            parts = arc.split("/")
            bad = "\\" in arc or ":" in arc or any(x in ("", ".", "..") for x in parts)
            if (not bad and self.learn == "dirs" and len(parts) >= 2 and parts[0] not in self.names
                    and all(_SAFE_DIR.match(x) for x in parts)
                    and (self.core_id != "xenia_edge" or _TITLE_ID.match(parts[0]) and parts[0] != _XENIA_PROFILE)
                    and parts[0] not in _learned_by_others(self.core_id, self.learn_game)
                    and parts[0] not in self.pending):
                self.pending.append(parts[0])       # podfolder gry z paczki — przyjęty po zapisie całej paczki
                if not _deep_ok(self, parts):       # zła ścieżka w nowym folderze — folder nie zostaje
                    self.pending.remove(parts[0])
            if bad or not _deep_ok(self, parts):
                raise ValueError(f"nieoczekiwana nazwa w paczce: {arc}")
            return self.folder.joinpath(*parts)
        if "/" in arc or "\\" in arc or ".." in arc or ":" in arc:
            raise ValueError(f"nieoczekiwana nazwa w paczce: {arc}")
        if self.kind == "trash":
            if not _TRASH.match(arc):
                raise ValueError(f"nieoczekiwana nazwa w paczce: {arc}")
            return self.folder / arc
        if self.emulator_id == "pcsx2" and self.kind == "states":
            if not _P2S.match(arc):
                raise ValueError(f"nieoczekiwana nazwa w paczce: {arc}")
            return self.folder / arc
        if (self.emulator_id, self.kind) in NATIVE:
            if not _match(self.emulator_id, self.kind, arc, {n.lower() for n in self.names}):
                raise ValueError(f"nieoczekiwana nazwa w paczce: {arc}")
            return self.folder / arc
        if not arc.startswith(ARC):
            raise ValueError(f"nieoczekiwana nazwa w paczce: {arc}")
        return self.folder / (self.stem + arc[len(ARC):])

    def adopt_names(self) -> int:
        """Pliki paczki nazwane od innej nazwy gry (np. od nazwy ZIP-a zamiast ROM-u w środku)
        → nazwa od pliku gry, którą uruchomi emulator (inaczej RetroArch ich nie widzi)."""
        n = 0
        for p, arc in list(self.files):
            want = self.local_path(arc)
            if Path(p) != want and not want.exists():
                os.replace(p, want)
                n += 1
        if n:
            self.collect()
        return n

    def spec(self) -> dict:
        return {"emu": self.emulator_id, "core": self.core_id, "kind": self.kind,
                "folder": str(self.folder), "stem": self.stem, "names": list(self.names),
                "learn": self.learn, "learn_game": self.learn_game}

    def collect(self) -> "Bundle":
        """Pliki paczki w folderze (od nowa)."""
        self.files = []
        if not self.folder.is_dir():
            return self
        if (self.emulator_id, self.kind) in DEEP:
            self.files = sorted(_deep_files(self), key=lambda x: x[1])
            return self
        names = ({n.lower() for n in self.names if n} if (self.emulator_id, self.kind) in NATIVE
                 else {norm(n) for n in self.names if n} | {norm(self.stem)})
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
                  spec["stem"], list(spec.get("names") or []),
                  learn=spec.get("learn", ""), learn_game=spec.get("learn_game", "")).collect()


def norm(s: str) -> str:
    """„Chrono Trigger (USA)” = „Chrono_Trigger_(USA)” (RetroArch nazywa pliki od ROM-u w ZIP-ie)."""
    return re.sub(r"[\s_]+", " ", s or "").strip().lower()


def _match(emu: str, kind: str, name: str, names: set):
    """(nazwa gry w pliku, nazwa w paczce) albo None — plik nie należy do paczki tej gry."""
    if kind == "trash":                      # kosz — folder tylko tej gry i slotu
        return (name, name) if _TRASH.match(name) else None
    if emu == "retroarch" and kind == "saves":
        for rx in _RA_SAVE_SFX:
            m = rx.match(name)
            if m and norm(m.group(1)) in names:
                return m.group(1), ARC + m.group(2)
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
    elif emu == "pcsx2" and kind == "states":
        # nazwy stanów to przedrostki z numeru płyty („slus-20851 (”) — nie nazwy gry
        m = _P2S.match(name)
        if m and not name.lower().endswith(".backup") and any(name.lower().startswith(p) for p in names):
            return m.group("prefix"), name
    elif (emu, kind) in (("dolphin", "states"), ("ppsspp", "states")):
        m = (_DOLPHIN_STATE if emu == "dolphin" else _PPSSPP_STATE).match(name)
        if m and m.group("prefix").lower() in names:
            return m.group("prefix"), name
    elif emu == "pcsx2" and kind == "memcards":
        m = re.fullmatch(r"(.+)\.ps2", name, re.I)
        if m and norm(m.group(1)) in names:
            return m.group(1), f"{ARC}.ps2"
    return None


def _deep_ok(b, parts: list) -> bool:
    """Ścieżka w paczce z podfolderami należy do tej gry."""
    ids = [n.upper() for n in b.names if n]
    if b.emulator_id == "ppsspp":            # SAVEDATA/<numer gry…>/plik
        return len(parts) == 2 and any(parts[0].upper().startswith(i) for i in ids)
    if b.emulator_id == "dolphin":
        if parts[0] == "GC":                 # GC/<region>/Card A/<…>-<ID4>-<…>.gci
            return (len(parts) == 4 and parts[2].startswith("Card ") and parts[3].lower().endswith(".gci")
                    and any(f"-{i[:4]}-" in parts[3].upper() for i in ids))
        return len(parts) > 4 and parts[:3] == ["Wii", "title", "00010000"] and \
            parts[3].lower() in {i[:4].encode().hex() for i in ids}
    if b.emulator_id == "retroarch":         # <podfolder gry>/… (nazwy poznane po grze albo z paczki gry)
        known = set(b.names) | set(getattr(b, "pending", ()))
        if b.core_id == "xenia_edge":        # <Title ID>/00000001/… i Headers/00000001/… — zapisy, bez aktualizacji i DLC
            return len(parts) >= 3 and parts[0] in known and (
                parts[1] == XENIA_SAVES or (len(parts) >= 4 and parts[1:3] == ["Headers", XENIA_SAVES]))
        return len(parts) >= 2 and parts[0] in known
    return False


def _deep_files(b) -> list:
    """[(ścieżka, nazwa w paczce)] paczki z podfolderami."""
    out, root = [], b.folder
    ids = [n.upper() for n in b.names if n]
    if b.emulator_id == "ppsspp":
        tops = [d for d in root.iterdir() if d.is_dir() and any(d.name.upper().startswith(i) for i in ids)]
    elif b.emulator_id == "dolphin":
        tops = [root / "GC"] + [root / "Wii" / "title" / "00010000" / i[:4].encode().hex() for i in ids]
    elif b.emulator_id == "retroarch":
        tops = [root / n for n in b.names if n]
    else:
        tops = []
    for top in tops:
        if not top.is_dir():
            continue
        for dp, _dirs, fns in os.walk(top, followlinks=True):
            for fn in fns:
                if fn.endswith(SKIP):
                    continue
                p = Path(dp) / fn
                arc = p.relative_to(root).as_posix()
                if _deep_ok(b, arc.split("/")):
                    out.append((p, arc))
    return out


_DOLPHIN_IDS: dict = {}
_GAME_ID = re.compile(rb"^[A-Z0-9]{6}$")
# miejsce nagłówka płyty (6 znaków ID gry) w pliku: RVZ/WIA — kopia nagłówka za nagłówkiem pliku (0x48 + 0x10),
# WBFS — za pierwszym sektorem (0x200), CISO — za mapą bloków (0x8000), ISO/GCM — na początku
_DISC_AT = ((b"RVZ\x01", 0x58), (b"WIA\x01", 0x58), (b"WBFS", 0x200), (b"CISO", 0x8000))


def disc_id(content: Path) -> str:
    """ID gry GameCube / Wii z nagłówka pliku gry (ISO, GCM, RVZ, WIA, WBFS, CISO) — bez DolphinTool.
    Inne formaty (GCZ: nagłówek skompresowany) i pliki bez ID — „”."""
    try:
        with open(content, "rb") as f:
            magic = f.read(4)
            f.seek(next((at for m, at in _DISC_AT if magic == m), 0))
            gid = f.read(6)
    except OSError:
        return ""
    return gid.decode() if _GAME_ID.match(gid) else ""


def dolphin_id(adapter, content: Path) -> str:
    """ID gry GameCube / Wii („GKBEAF”) — z nagłówka płyty (disc_id, a dla innych formatów DolphinTool
    header samodzielnego Dolphina), zapamiętane po nazwie pliku gry (plik bywa jeszcze niepobrany,
    gdy lista zapisów pyta o paczki)."""
    from emustart import library
    key = Path(content).name.lower()
    if not _DOLPHIN_IDS:
        try:
            _DOLPHIN_IDS.update(json.loads(library.meta_get("dolphin_ids", "{}")))
        except Exception:
            pass
    if _DOLPHIN_IDS.get(key):
        return _DOLPHIN_IDS[key]
    gid = disc_id(content) if Path(content).is_file() else ""
    tool = Path(adapter.home) / "DolphinTool.exe"
    if not gid and tool.is_file() and Path(content).is_file():
        try:
            out = subprocess.run([str(tool), "header", "-i", str(content)], capture_output=True, text=True, timeout=60,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        except (OSError, subprocess.SubprocessError) as ex:
            log.info("DolphinTool: %s", ex)
            return ""
        m = re.search(r"Game ID:\s*([A-Z0-9]{6})", out or "")
        gid = m.group(1) if m else ""
    if not gid:
        return ""
    _DOLPHIN_IDS[key] = gid
    try:
        library.meta_set("dolphin_ids", json.dumps(_DOLPHIN_IDS))
    except Exception:
        pass
    return gid


def psp_serial(game: dict) -> str:
    """Numer gry PSP tak, jak nazywa pliki PPSSPP: „ULUS-10336” → „ULUS10336”."""
    m = re.search(r"([A-Z]{4})-?(\d{5})", (game.get("serial") or "").upper())
    return m.group(1) + m.group(2) if m else ""


# Zapisy w grze (rodzaj „saves”) wspólne dla rdzeni o zgodnym formacie zapisu — surowy SRAM /
# pamięć baterii (.srm) — jak „root overrides” Steam Cloud: różne programy, te same pliki.
# Stany zostają per rdzeń (format stanu zależy od rdzenia). Rdzenie spoza listy — per rdzeń.
SAVE_GROUPS = {
    "sram-snes": {"snes9x", "snes9x2002", "snes9x2005", "snes9x2005_plus", "snes9x2010", "bsnes", "bsnes_hd_beta",
                  "bsnes2014_accuracy", "bsnes2014_balanced", "bsnes2014_performance", "bsnes_mercury_accuracy",
                  "bsnes_mercury_balanced", "bsnes_mercury_performance", "bsnes_cplusplus98", "mesen-s"},
    "sram-nes": {"fceumm", "nestopia", "mesen", "quicknes"},
}
_GROUP_OF = {c: g for g, members in SAVE_GROUPS.items() for c in members}


def save_core(core: str) -> str:
    """Rdzeń paczki zapisów w grze: grupa zgodnych rdzeni („sram-snes”) albo sam rdzeń."""
    c = (core or "").lower()
    return _GROUP_OF.get(c, c)


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


def ra_folder(adapter, core: str, content: Path, key: str, default: str, by_content: str, by_core: str,
              sort: bool = True) -> Path:
    """Folder zapisów / stanów RetroArcha dla gry — tam, gdzie szuka ich ten RetroArch (ustawienia sortowania
    po folderze gry / po rdzeniu); sort=False — bez podfolderów sortowania."""
    from emustart.ingame import _ini_value
    cfg = adapter.home / "retroarch.cfg"
    d = Path(adapter._cfg_dir(key, default))
    if sort and _ini_value(cfg, by_content) == "true":
        d = d / Path(content).parent.name
    if sort and _ini_value(cfg, by_core) == "true" and core:
        d = d / core_name(adapter.home, core)
    return d


def ra_core_options_file(adapter, core: str) -> Path:
    """Plik opcji rdzenia, który czyta ten RetroArch: wspólny (global_core_options albo gdy plik rdzenia
    config/<rdzeń>/<rdzeń>.opt jeszcze nie istnieje — wtedy RetroArch czyta wspólny) albo plik rdzenia.
    Pliki .opt gry i folderu nie są tu brane pod uwagę."""
    from emustart.ingame import _ini_value
    main = adapter.home / "retroarch.cfg"
    name = core_name(adapter.home, core)
    own = adapter.home / "config" / name / f"{name}.opt"
    if _ini_value(main, "global_core_options") != "true" and own.is_file():
        return own
    p = _ini_value(main, "core_options_path")
    if p.startswith(":"):
        p = str(adapter.home) + p[1:]
    return Path(p) if p else adapter.home / "retroarch-core-options.cfg"


def ra_core_option(adapter, core: str, key: str) -> str:
    """Wartość opcji rdzenia z pliku opcji, którego używa ten RetroArch („” — brak)."""
    from emustart.ingame import _ini_value
    f = ra_core_options_file(adapter, core)
    return _ini_value(f, key) if f.is_file() else ""


# Rdzenie z kartą wspólną dla gier w system\ i opcją osobnej karty dla gry (źródła / pliki rdzeni):
# rdzeń → (wspólna karta w system\, przyrostek karty gry w katalogu zapisów, opcja, wartości „osobna karta”)
RA_SHARED_CARDS = {
    "flycast": (("dc", "vmu_save_A1.bin"), ".A1.bin", "reicast_per_content_vmus", ("VMU A1", "All VMUs")),
    "pcsx2": (("pcsx2", "memcards", "Mcd001.ps2"), ".ps2", "pcsx2_shared_memory_cards", ("disabled",)),
}


def seed_from_shared(adapter, core: str, content: Path, game: dict, owner: bool = True) -> list:
    """Pierwsza gra z osobną kartą: kopia wspólnej karty z system\\ (dotychczasowe zapisy są dalej widoczne).
    Bez kopii, gdy gra ma już kartę albo rdzeń używa jeszcze wspólnej. Wspólna karta zostaje nietknięta."""
    # owner=False — gracz inny niż właściciel urządzenia: wspólna karta (zapisy wszystkich graczy) nie trafia
    # do jego paczek
    c = (core or "").lower()
    spec = RA_SHARED_CARDS.get(c)
    if adapter.family != "retroarch" or not spec or not owner:
        return []
    shared, sfx, key, on = spec
    if ra_core_option(adapter, core, key) not in on:
        return []
    src = Path(adapter._cfg_dir("system_directory", "system")).joinpath(*shared)
    b = next((x for x in for_game(adapter, core, content, game) if (x.emulator_id, x.kind) == ("retroarch", "saves")),
             None)
    if b is None or not src.is_file() or any(arc == ARC + sfx for _p, arc in b.files):
        return []
    dst = b.folder / (b.stem + sfx)
    if dst.exists():
        return []
    import shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    log.info("karta gry z kopii wspólnej: %s → %s", src, dst)
    return [dst]


def ra_save_subdir(adapter, core: str) -> tuple:
    """Podfolder katalogu zapisów, w którym rdzeń trzyma zapisy nazwane jak gra (źródła / pliki rdzeni):
    FBNeo — fbneo, Kronos — kronos/saturn (z opcją „Share saves with beetle” — wprost w katalogu zapisów)."""
    c = (core or "").lower()
    if c == "fbneo":
        return ("fbneo",)
    if c == "kronos" and ra_core_option(adapter, core, "kronos_use_beetle_saves") != "enabled":
        return ("kronos", "saturn")
    return ()


def retroarch(adapter, core: str, content: Path, names: list) -> list:
    """Zapisy i stany RetroArcha tej gry dla rdzenia `core` — w folderach, w których szuka ich
    ten RetroArch (ustawienia sortowania po rdzeniu / po folderze gry)."""
    stem = Path(content).stem
    return [Bundle("retroarch", save_core(core), "saves",
                   ra_folder(adapter, core, content, "savefile_directory", "saves",
                             "sort_savefiles_by_content_enable", "sort_savefiles_enable"
                             ).joinpath(*ra_save_subdir(adapter, core)),
                   stem, names).collect(),
            Bundle("retroarch", core, "states",
                   ra_folder(adapter, core, content, "savestate_directory", "states",
                             "sort_savestates_by_content_enable", "sort_savestates_enable"),
                   stem, names).collect()]


# Rdzenie, które trzymają zapisy w podfolderze katalogu zapisów RetroArcha w układzie samodzielnego emulatora:
# rdzeń → (emulator, którego paczka — ten sam format zapisu, ta sama paczka na serwerze; podfolder)
RA_CORE_ID = "libretro"
RA_DEEP_CORES = {"dolphin": ("dolphin", ("dolphin-emu", "User")),
                 "ppsspp": ("ppsspp", ("PSP", "SAVEDATA"))}


def ra_core_bundles(adapter, core: str, content: Path, game: dict) -> list:
    """Zapisy rdzenia z RA_DEEP_CORES — paczka jak dla samodzielnego emulatora. Folder: z sortowaniem po
    rdzeniu (tam RetroArch kieruje rdzeń), a gdy zapisy leżą już bez podfolderu sortowania — tam."""
    emu, sub = RA_DEEP_CORES.get((core or "").lower(), ("", ()))
    if not emu:
        return []
    gid = dolphin_id(adapter, content) if emu == "dolphin" else psp_serial(game)
    if not gid:
        return []
    keys = ("savefile_directory", "saves", "sort_savefiles_by_content_enable", "sort_savefiles_enable")
    places = [ra_folder(adapter, core, content, *keys).joinpath(*sub),
              ra_folder(adapter, core, content, *keys, sort=False).joinpath(*sub)]
    folder = next((p for p in places if p.is_dir()), places[0])
    # RA_CORE_ID: osobny stan na urządzeniu niż samodzielny emulator (inny folder); serwer zapisuje paczkę emulatora
    # bez rdzenia — ta sama paczka, zapis przechodzi między rdzeniem a samodzielnym emulatorem
    return [Bundle(emu, RA_CORE_ID, "saves", folder, gid, [gid]).collect()]


# ── nazwy poznawane po grze ──
# Część rdzeni nazywa zapisy nie od pliku gry: Flycast — karta <ID gry z płyty>.<port>.bin, PUAE — WHDLoad
# zapisuje w WHDSaves/<podfolder gry>. Nazwy poznajemy po grze: co zmieniło się w czasie gry i nie należy do
# innej gry tego rdzenia — należy do tej gry (zapamiętane na urządzeniu; pobranie paczki gry też je uczy).
LEARN_KEY = "learned_saves"


def _learned_map() -> dict:
    from emustart import library
    try:
        return json.loads(library.meta_get(LEARN_KEY, "{}") or "{}")
    except (ValueError, TypeError):
        return {}


def learned(core: str, game_key: str) -> list:
    return list(_learned_map().get(core, {}).get(game_key, []))


def _learned_by_others(core: str, game_key: str) -> set:
    return {n for g, ns in _learned_map().get(core, {}).items() if g != game_key for n in ns}


def _remember(core: str, game_key: str, names: list) -> None:
    from emustart import library
    m = _learned_map()
    have = m.setdefault(core, {}).setdefault(game_key, [])
    for n in names:
        if n not in have:
            have.append(n)
    library.meta_set(LEARN_KEY, json.dumps(m, ensure_ascii=False))


def _learn_entries(b) -> dict:
    """{nazwa: podpis} wpisów w folderze paczki: karty <nazwa>.<port>.bin albo podfoldery (czas i rozmiar)."""
    out: dict = {}
    if not b.folder.is_dir():
        return out
    for e in b.folder.iterdir():
        if b.learn == "files" and e.is_file():
            m = _RA_SAVE_SFX[0].match(e.name)
            if m:
                st = e.stat()
                out[m.group(1)] = max(out.get(m.group(1), (0, 0)), (st.st_mtime_ns, st.st_size))
        elif b.learn == "dirs" and e.is_dir():
            sig = [(0, 0, 0)]
            for dp, _d, fns in os.walk(e):
                for fn in fns:
                    st = (Path(dp) / fn).stat()
                    sig.append((st.st_mtime_ns, st.st_size, len(fn)))
            out[e.name] = (max(sig), len(sig), sum(s[1] for s in sig))
    return out


def learn_snapshot(bundles: list) -> None:
    """Przed grą (po pobraniu zapisów z serwera): stan folderów, w których poznajemy nazwy."""
    for b in bundles:
        if b.learn:
            b.seen = _learn_entries(b)


# Xenia Edge (system\Xenia-Edge\content\<XUID>\…, pliki rdzenia i dane z gry): zapisy gry w <Title ID>\00000001,
# profil (konto, osiągnięcia) w FFFE07D1 — zmienia się przy każdej grze, nie jest zapisem żadnej gry
XENIA_SAVES = "00000001"
_XENIA_PROFILE = "FFFE07D1"
_XUID = re.compile(r"^[0-9A-F]{16}$")
_TITLE_ID = re.compile(r"^[0-9A-F]{8}$")


XENIA_OWNERS = "xenia_owners"


def xenia_xuids(content: Path) -> list:
    """Wszystkie profile Xenii na urządzeniu (foldery z kontem)."""
    return sorted(d.name for d in (content.iterdir() if content.is_dir() else ())
                  if _XUID.match(d.name) and d.name != "0" * 16
                  and (d / _XENIA_PROFILE / "00010000" / d.name / "Account").is_file())


def xenia_note(adapter, core: str) -> str:
    """Komunikat dla gracza, gdy zapisy Xbox 360 nie są synchronizowane, bo na urządzeniu jest kilka profili Xenii."""
    if (core or "").lower() != "xenia_edge" or adapter.family != "retroarch":
        return ""
    content = xenia_content(adapter)
    if len(xenia_xuids(content)) > 1:
        return "Kilka profili Xbox 360 w folderze gracza na tym urządzeniu — zapisy gier X360 bez synchronizacji z serwerem."
    return ""


XENIA_SHARED = "0000000000000000"     # content\0000000000000000: aktualizacje i DLC — wspólne dla graczy


def xenia_content(adapter) -> Path:
    return Path(adapter._cfg_dir("system_directory", "system")) / "Xenia-Edge" / "content"


def _xenia_owners() -> dict:
    from emustart import library
    try:
        owners = json.loads(library.meta_get(XENIA_OWNERS, "{}") or "{}")
    except (ValueError, TypeError):
        owners = {}
    return owners if isinstance(owners, dict) else {}


def _xenia_own(content: Path, pid: int) -> bool:
    """content\\ Xenii to dowiązanie do folderu gracza `pid` (xenia_attach)."""
    from emustart import profiles
    t = profiles._junction_target(content) if profiles._is_junction(content) else None
    return bool(t) and profiles._same(t, profiles.local_dir(int(pid), "xenia_edge", "content"))


def xenia_attach(adapter, pid: int, pinned: str = "") -> None:
    """Przed grą rdzeniem Xenia: system\\Xenia-Edge\\content → dowiązanie do folderu gracza (profile\\<gracz>\\
    xenia_edge\\content) — każdy gracz ma swój profil Xboksa i swoje zapisy, jak foldery Dolphina. Aktualizacje
    i DLC (content\\0000000000000000) — jeden wspólny folder obok (content-shared), podpięty u każdego gracza.
    Pierwszy raz: profile z folderu przechodzą do graczy, którzy z nich grali (mapa właścicieli), pozostałe — do
    pierwszego profilu. Profil przypięty graczowi w panelu serwera (`pinned`), leżący u innego gracza na tym
    urządzeniu, przechodzi do niego."""
    from emustart import profiles
    content = xenia_content(adapter)
    shared = content.parent / "content-shared" / XENIA_SHARED
    mine = profiles.local_dir(int(pid), "xenia_edge", "content")
    if content.is_dir() and not profiles._is_junction(content):
        owners = _xenia_owners()
        for d in list(content.iterdir()):
            if profiles._is_junction(d):
                os.rmdir(d)                          # samo dowiązanie, nie zawartość
            elif d.is_dir() and d.name == XENIA_SHARED:
                profiles._merge_move(d, shared)
            elif d.is_dir() and _XUID.match(d.name) and profiles.get(int(owners.get(d.name) or 0)):
                profiles._merge_move(d, profiles.local_dir(int(owners[d.name]), "xenia_edge", "content") / d.name)
    if pinned and _XUID.match(pinned) and not (mine / pinned).exists():
        for other in profiles.LOCAL.glob(f"*/xenia_edge/content/{pinned}"):
            if other.is_dir() and not profiles._same(other.parent, mine):
                log.info("Xenia: profil %s przypięty graczowi %s — przenoszę z %s", pinned, pid, other.parent)
                profiles._merge_move(other, mine / pinned)
                break
    profiles.attach(int(pid), "xenia_edge", content)
    link = mine / XENIA_SHARED
    if profiles._is_junction(link):
        if profiles._same(profiles._junction_target(link) or Path(), shared):
            return
        os.rmdir(link)
    elif link.is_dir():                              # pobrane, zanim było dowiązanie — do wspólnego
        profiles._merge_move(link, shared)
    shared.mkdir(parents=True, exist_ok=True)
    profiles._make_junction(link, shared)


def xenia_claim(bundles: list, pid: int, pinned: str = "", claim: bool = True) -> list:
    """Paczki zapisów Xenii gracza `pid`. content\\ jest folderem tego gracza (xenia_attach) — jego. Inaczej
    (bez profili graczy albo wspólny folder): profil Xenii (XUID) na urządzeniu należy do pierwszego gracza,
    który z niego grał — albo do gracza, któremu przypięto go w panelu serwera (`pinned`; jest na urządzeniu —
    paczka w jego folderze). Inny gracz nie dostaje paczki zapisów Xenii — nie pobierze swoich zapisów do
    cudzego profilu i nie wyśle cudzych jako swoich."""
    from emustart import library
    out = []
    for b in bundles:
        if getattr(b, "core_id", "") != "xenia_edge":       # tylko paczki Xenii (inne obiekty — bez zmian)
            out.append(b)
            continue
        if _xenia_own(b.folder.parent, pid):
            out.append(b)
            continue
        owners = _xenia_owners()
        if pinned and pinned == b.folder.name:   # profil przypięty graczowi w panelu serwera — jego
            owners[pinned] = int(pid)
        xuid = b.folder.name
        if not claim:                         # widok (opcje gry, sloty): bez zajmowania profilu
            if owners.get(xuid) == int(pid):
                out.append(b)
            continue
        if owners.setdefault(xuid, int(pid)) != int(pid):
            log.info("Xenia: profil %s należy do innego gracza — zapisy bez synchronizacji", xuid)
            continue
        library.meta_set(XENIA_OWNERS, json.dumps(owners))
        out.append(b)
    return out


def learn_after(bundles: list) -> list:
    """Po grze: nowe albo zmienione karty / podfoldery, których nie ma inna gra tego rdzenia — tej gry.
    Zwraca poznane nazwy (zapamiętane; paczka zebrana od nowa)."""
    found = []
    for b in bundles:
        if not b.learn:
            continue
        others = _learned_by_others(b.core_id, b.learn_game)
        now = _learn_entries(b)
        new = [n for n, sig in sorted(now.items(), key=lambda x: x[1])
               if b.seen.get(n) != sig and n not in b.names and n not in others and n != b.stem
               and (b.core_id != "xenia_edge" or _TITLE_ID.match(n) and n != _XENIA_PROFILE)]
        if not new:
            continue
        b.names += new
        if b.learn == "files":
            b.stem = new[-1]                 # nazwa, pod którą rdzeń zapisuje (i czyta) kartę tej gry
        _remember(b.core_id, b.learn_game, new)
        b.collect()
        found += new
    return found


def remember_downloaded(bundles: list) -> None:
    """Po pobraniu z serwera: podfoldery przyjęte z paczki gry (local_path) — zapamiętane na urządzeniu."""
    for b in bundles:
        if b.learn == "dirs" and b.names:
            _remember(b.core_id, b.learn_game, b.names)


def _game_key(game: dict) -> str:
    return f"{game.get('es', '')}/{game.get('name', '')}"


def ra_learned_bundles(adapter, core: str, content: Path, game: dict, bundles: list) -> list:
    """Flycast: karta gry w paczce zapisów pod nazwą poznaną po grze; PUAE: paczka WHDSaves/<podfolder gry>."""
    c, key = (core or "").lower(), _game_key(game)
    if c == "flycast":
        for b in bundles:
            if (b.emulator_id, b.kind) == ("retroarch", "saves"):
                b.learn, b.learn_game = "files", key
                known = learned(b.core_id, key)
                b.names = b.names + [n for n in known if n not in b.names]
                if known:
                    b.stem = known[-1]
                b.collect()
        return []
    if c == "puae":
        keys = ("savefile_directory", "saves", "sort_savefiles_by_content_enable", "sort_savefiles_enable")
        places = [ra_folder(adapter, core, content, *keys) / "WHDSaves",
                  ra_folder(adapter, core, content, *keys, sort=False) / "WHDSaves"]
        folder = next((p for p in places if p.is_dir()), places[0])
        b = Bundle("retroarch", c, "memcards", folder, "", learned(c, key), learn="dirs", learn_game=key)
        return [b.collect()]
    if c == "xenia_edge":
        # zapisy względem profilu na tym urządzeniu: XUID bywa inny na każdym urządzeniu; bez profilu —
        # bez paczki (zapisy trafiłyby do folderu nieistniejącego profilu)
        content = xenia_content(adapter)
        # kilka profili: nie wiadomo, który zaloguje Xenia (logowanie profilu — do sprawdzenia na żywo) —
        # bez paczki, zapisy nie trafią do cudzego profilu
        xuids = xenia_xuids(content)
        if len(xuids) != 1:
            return []
        xuid = xuids[0]
        b = Bundle("retroarch", c, "memcards", content / xuid, "", learned(c, key), learn="dirs", learn_game=key)
        return [b.collect()]
    return []


def for_game(adapter, core: str, content: Path, game: dict) -> list:
    """Paczki tej gry dla emulatora `adapter` (pusta lista — emulator jeszcze bez synchronizacji
    paczkami, działa dotychczasowa synchronizacja folderów profilu)."""
    if adapter.family == "retroarch":
        if not core:
            return []
        names = sorted({Path(content).stem, game.get("name", ""), Path(game.get("rel", "")).stem} - {""})
        out = retroarch(adapter, core, content, names)
        return out + ra_core_bundles(adapter, core, content, game) + ra_learned_bundles(adapter, core, content, game, out)
    out = []
    if adapter.family in ("duckstation", "pcsx2") and adapter.memcard_dir():
        card = card_stem(content, game)
        out.append(Bundle(adapter.family, "", "memcards", adapter.memcard_dir(), card, [card]).collect())
    if adapter.family == "pcsx2":
        prefixes = ps2_prefixes(game)
        if prefixes and adapter.state_dirs():
            # stany wspólne z ArmSX2 na telefonie (ten sam format nazw: numer płyty i CRC)
            out.append(Bundle("pcsx2", "", "states", adapter.state_dirs()[0], "", prefixes).collect())
    if adapter.family == "dolphin":
        gid = dolphin_id(adapter, content)
        if gid:
            out += [Bundle("dolphin", "", "saves", adapter._user(), gid, [gid]).collect(),
                    Bundle("dolphin", "", "states", adapter.state_dirs()[0], gid, [gid]).collect()]
    if adapter.family == "ppsspp":
        sid = psp_serial(game)
        if sid:
            out += [Bundle("ppsspp", "", "saves", adapter.save_dirs()[0], sid, [sid]).collect(),
                    Bundle("ppsspp", "", "states", adapter.state_dirs()[0], sid, [sid]).collect()]
    return out


def ps2_prefixes(game: dict) -> list:
    """Przedrostki nazw stanów PS2 gry: z numeru płyty („SLUS-20851 (”) i poznane po grze
    („SLUS-20851 (39B574F0).”); małe litery."""
    out = set()
    m = re.search(r"[A-Z]{4}-\d{3,5}", (game.get("serial") or "").upper())
    if m:
        out.add(m.group(0).lower() + " (")
    for p in game.get("state_prefixes") or []:
        if p:
            out.add(p.lower())
    return sorted(out)


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
    if adapter.family == "duckstation":
        return [adapter.memcard_dir()]
    if adapter.family == "pcsx2":
        return [adapter.memcard_dir()] + list(adapter.state_dirs()[:1])     # karty i stany — paczkami
    if adapter.family in ("dolphin", "ppsspp"):
        return list(adapter.save_dirs())     # GC, Wii, StateSaves / SAVEDATA, PPSSPP_STATE
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


# ── łączenie stanów z dwóch miejsc (urządzenie ↔ serwer, migracja ↔ paczka) ──
_SLOT = re.compile(r"^game\.state(\d*)(\.auto)?$")


def _slot_no(arc: str) -> int:
    m = _SLOT.match(arc)
    if m:
        return int(m.group(1) or 0) if not m.group(2) else -1
    m = _DOLPHIN_STATE.match(arc) or _PPSSPP_STATE.match(arc)
    if m:
        return int(m.group("slot"))
    m = _P2S.match(arc)
    return int(m.group("slot")) if m and m.group("slot").isdigit() else -1


def _free_slot(arc: str, used: set, nxt: int) -> tuple[str | None, int]:
    """Nazwa dla starszego stanu z zajętego slotu: RetroArch — następny numer; PCSX2 —
    pierwszy wolny slot 00–09 tej gry (None — brak wolnego: zostaje w historii serwera)."""
    for rx, fmt, lo in ((_DOLPHIN_STATE, "{p}.s{n:02d}", 1), (_PPSSPP_STATE, "{p}_{n}.ppst", 0)):
        m = rx.match(arc)
        if m:
            for n in range(lo, 100):
                cand = fmt.format(p=m.group("prefix"), n=n)
                if cand.lower() not in used:
                    used.add(cand.lower())
                    return cand, nxt
            return None, nxt
    m = _P2S.match(arc)
    if m:
        for n in range(10):
            cand = f"{m.group('prefix')}.{n:02d}.p2s"
            if cand.lower() not in used:
                used.add(cand.lower())
                return cand, nxt
        return None, nxt
    return f"game.state{nxt}", nxt + 1


def merge_states(mine: dict, other: dict, removed=()) -> tuple[dict, list]:
    """Stany z dwóch miejsc → suma slotów (nic nie znika). `mine` / `other`:
    {nazwa w paczce: (sha256, czas)} — stan i jego miniatura „.png” razem. Ten sam slot z inną
    zawartością: nowszy zostaje w slocie, starszy dostaje następny wolny numer (z miniaturą).
    `removed` — {(nazwa w paczce, sha256)} stanów świadomie usuniętych / przenumerowanych /
    przeniesionych przez gracza: ta treść pod tą nazwą nie wraca (ani z `other`, ani z `mine`),
    znika razem z miniaturą. Ta sama treść pod inną nazwą (kopia) zostaje.
    Zwraca (wynik {nazwa: ("mine"|"other", nazwa źródłowa)}, [(skąd, stara nazwa, nowa nazwa)])."""
    if removed:
        removed = set(removed)

        def keep(side: dict) -> dict:
            gone = {a for a, (sha, _t) in side.items() if not a.endswith(".png") and (a, sha) in removed}
            return {a: v for a, v in side.items() if a not in gone and a[:-4] not in gone}
        mine, other = keep(mine), keep(other)
    out = {a: ("mine", a) for a in mine}
    moves = []
    nxt = max({_slot_no(a) for a in list(mine) + list(other) if _SLOT.match(a)} | {0}) + 1
    used = {a.lower() for a in list(mine) + list(other)}
    for arc, (sha, t) in sorted(other.items()):
        if arc.endswith(".png"):
            continue
        png = arc + ".png"
        if arc not in mine:
            out[arc] = ("other", arc)
            if png in other:
                out[png] = ("other", png)
            continue
        if mine[arc][0] == sha:
            if png in other and png not in mine:
                out[png] = ("other", png)
            continue
        # kolizja: nowszy w slocie, starszy — nowy numer
        new, nxt = _free_slot(arc, used, nxt)
        if new is None:                         # PCSX2: wszystkie sloty gry zajęte
            if t > mine[arc][1]:
                out[arc] = ("other", arc)
                out.pop(png, None)
            continue
        if t > mine[arc][1]:
            moves.append(("mine", arc, new))
            out[new] = ("mine", arc)
            if png in mine:
                out[new + ".png"] = ("mine", png)
            out[arc] = ("other", arc)
            out.pop(png, None)
            if png in other:
                out[png] = ("other", png)
        else:
            moves.append(("other", arc, new))
            out[new] = ("other", arc)
            if png in other:
                out[new + ".png"] = ("other", png)
    return out, moves
