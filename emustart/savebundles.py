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
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

CHUNK = 1 << 20
ARC = "game"                     # nazwa umowna pliku gry w paczce
# zapisy w grze RetroArcha (nie stany): baterie, zegary, EEPROM-y, karty rdzeni
RA_SAVE_EXTS = {"srm", "sav", "rtc", "eep", "fla", "mpk", "sra", "nv", "mcr", "bkr", "bcr", "brm",
                "dsv", "dsa", "ram", "ldci", "lcd"}
_RA_STATE = re.compile(r"(.+?)(\.state\d*(?:\.auto)?(?:\.png)?)", re.I)
# stan PCSX2 / ArmSX2: „SLUS-20851 (39B574F0).03.p2s”, wznowienie PCSX2 (.resume), autozapis ArmSX2
_P2S = re.compile(r"^(?P<prefix>.+?)\.(?P<slot>\d{2}|resume|autosave(?:\.\d)?)\.p2s$", re.I)
_TRASH = re.compile(r"^[0-9a-f]{12}\.[^/\\:]+$")
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
        """Ścieżka na tym urządzeniu dla pliku paczki („game.state2” → „<plik gry>.state2”;
        stany PCSX2 — nazwa jak w paczce, od numeru płyty)."""
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
                "folder": str(self.folder), "stem": self.stem, "names": list(self.names)}

    def collect(self) -> "Bundle":
        """Pliki paczki w folderze (od nowa)."""
        self.files = []
        if not self.folder.is_dir():
            return self
        names = ({n.lower() for n in self.names if n} if (self.emulator_id, self.kind) == ("pcsx2", "states")
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
                  spec["stem"], list(spec.get("names") or [])).collect()


def norm(s: str) -> str:
    """„Chrono Trigger (USA)” = „Chrono_Trigger_(USA)” (RetroArch nazywa pliki od ROM-u w ZIP-ie)."""
    return re.sub(r"[\s_]+", " ", s or "").strip().lower()


def _match(emu: str, kind: str, name: str, names: set):
    """(nazwa gry w pliku, nazwa w paczce) albo None — plik nie należy do paczki tej gry."""
    if kind == "trash":                      # kosz — folder tylko tej gry i slotu
        return (name, name) if _TRASH.match(name) else None
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
    elif emu == "pcsx2" and kind == "states":
        # nazwy stanów to przedrostki z numeru płyty („slus-20851 (”) — nie nazwy gry
        m = _P2S.match(name)
        if m and not name.lower().endswith(".backup") and any(name.lower().startswith(p) for p in names):
            return m.group("prefix"), name
    elif emu == "pcsx2" and kind == "memcards":
        m = re.fullmatch(r"(.+)\.ps2", name, re.I)
        if m and norm(m.group(1)) in names:
            return m.group(1), f"{ARC}.ps2"
    return None


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
    return [Bundle("retroarch", save_core(core), "saves",
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
    out = []
    if adapter.family in ("duckstation", "pcsx2") and adapter.memcard_dir():
        card = card_stem(content, game)
        out.append(Bundle(adapter.family, "", "memcards", adapter.memcard_dir(), card, [card]).collect())
    if adapter.family == "pcsx2":
        prefixes = ps2_prefixes(game)
        if prefixes and adapter.state_dirs():
            # stany wspólne z ArmSX2 na telefonie (ten sam format nazw: numer płyty i CRC)
            out.append(Bundle("pcsx2", "", "states", adapter.state_dirs()[0], "", prefixes).collect())
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
    m = _P2S.match(arc)
    return int(m.group("slot")) if m and m.group("slot").isdigit() else -1


def _free_slot(arc: str, used: set, nxt: int) -> tuple[str | None, int]:
    """Nazwa dla starszego stanu z zajętego slotu: RetroArch — następny numer; PCSX2 —
    pierwszy wolny slot 00–09 tej gry (None — brak wolnego: zostaje w historii serwera)."""
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
