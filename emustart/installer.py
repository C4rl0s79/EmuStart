"""installer — pobieranie brakujących emulatorów i rdzeni RetroArcha.

Źródła i nazwy plików z aktualizatora ROM Helpera (chd_buddy/core/updater.py):
GitHub Releases, dolphin-emu.org, strona wydań Edena, buildbot.libretro.com.
Instalacja do <emu_root>\\<Folder>\\ — tak jak Twoje obecne emulatory.

Wybór dla systemu („jak obecnie”): samodzielny emulator tam, gdzie EmuStart go
preferuje (emulators.STANDALONE_PREF), w pozostałych systemach rdzeń RetroArcha
(CORES). Brak RetroArcha → najpierw sam RetroArch.

Archiwa .7z rozpakowuje 7-Zip (zainstalowany albo 7zr.exe pobrany z 7-zip.org
do data/tools przy pierwszej potrzebie).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from emustart import emulators, paths, systems

log = logging.getLogger("emustart.installer")

BUILDBOT = "https://buildbot.libretro.com"
UA = {"User-Agent": "EmuStart-installer/1.0"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# samodzielne emulatory (z ROM Helpera); klucz = podłańcuch nazwy w STANDALONE_PREF
STANDALONE = {
    "duckstation": {"type": "github", "repo": "stenzek/duckstation",
                    "asset": r"duckstation-windows-x64-release\.zip$", "dir": "DuckStation",
                    "portable": "portable.txt"},
    "pcsx2": {"type": "github", "repo": "PCSX2/pcsx2", "prerelease": True,
              "asset": r"pcsx2-.*-windows-x64-Qt\.7z$", "dir": "PCSX2", "portable": "portable.ini"},
    "rpcs3": {"type": "github", "repo": "RPCS3/rpcs3-binaries-win",
              "asset": r"rpcs3-.*_win64.*\.7z$", "dir": "RPCS3"},
    "ppsspp": {"type": "github", "repo": "hrydgard/ppsspp",
               "asset": r"PPSSPP-v.*-Windows-x64\.zip$", "dir": "PPSSPP"},
    "vita3k": {"type": "github", "repo": "Vita3K/Vita3K",
               "asset": r"windows-latest\.zip$", "dir": "Vita3K"},
    "dolphin": {"type": "dolphin", "dir": "Dolphin", "portable": "portable.txt"},
    "cemu": {"type": "github", "repo": "cemu-project/Cemu",
             "asset": r"cemu-.*-windows-x64\.zip$", "dir": "Cemu", "strip_root": True},
    "eden": {"type": "eden", "asset": r"Eden-Windows-.*amd64-clang-pgo\.zip$", "dir": "Eden"},
    "azahar": {"type": "github", "repo": "azahar-emu/azahar",
               "asset": r"azahar-windows-msvc-[\d.]+\.zip$", "dir": "Azahar", "strip_root": True},
    "melonds": {"type": "github", "repo": "melonDS-emu/melonDS",
                "asset": r"melonDS-.*-windows-x86_64\.zip$", "dir": "melonDS"},
    "ares": {"type": "github", "repo": "ares-emulator/ares",
             "asset": r"ares-windows-x64\.zip$", "dir": "ares", "strip_root": True},
    "snes9x": {"type": "github", "repo": "snes9xgit/snes9x",
               "asset": r"snes9x-.*-win32-x64\.zip$", "dir": "Snes9x"},
    "mgba": {"type": "github", "repo": "mgba-emu/mgba",
             "asset": r"mGBA-.*-win64\.7z$", "dir": "mGBA", "strip_root": True},
    "flycast": {"type": "github", "repo": "flyinghead/flycast",
                "asset": r"flycast-win64-.*\.zip$", "dir": "Flycast"},
    "xenia": {"type": "github", "repo": "xenia-canary/xenia-canary-releases",
              "asset": r"xenia_canary_windows_?\.zip$", "dir": "Xenia"},
    "xemu": {"type": "github", "repo": "xemu-project/xemu",
             "asset": r"xemu-win-x86_64-release\.zip$", "dir": "xemu"},
    "shadps4": {"type": "github", "repo": "shadps4-emu/shadPS4",
                "asset": r"shadps4-win64-(qt|sdl)-?.*\.zip$", "dir": "shadPS4"},
    "mame": {"type": "github", "repo": "mamedev/mame",
             "asset": r"mame\d+b_x64\.exe$", "dir": "MAME", "sfx": True},
}

# rdzeń RetroArcha dla systemów bez preferowanego emulatora samodzielnego
CORES = {
    "NES": "nestopia", "SNES": "snes9x", "SNESMSU1": "snes9x", "N64": "mupen64plus_next",
    "GB": "gambatte", "GBC": "gambatte", "GBA": "mgba", "NDS": "melonds",
    "MD": "genesis_plus_gx", "SMS": "genesis_plus_gx", "GG": "genesis_plus_gx",
    "SEGACD": "genesis_plus_gx", "32X": "picodrive", "PICO": "picodrive",
    "SATURN": "mednafen_saturn", "PCENGINE": "mednafen_pce_fast",
    "SUPERGRAFX": "mednafen_supergrafx", "ATARI2600": "stella", "ATARI5200": "a5200",
    "ATARI7800": "prosystem", "JAGUAR": "virtualjaguar", "LYNX": "handy", "3DO": "opera",
    "AMIGA": "puae", "C64": "vice_x64", "VIC20": "vice_xvic", "PLUS4": "vice_xplus4",
    "MSX": "bluemsx", "MSX2": "bluemsx", "NGP": "mednafen_ngp", "WSWAN": "mednafen_wswan",
    "ODYSSEY2": "o2em", "ZX": "fuse", "GW": "gw", "PC88": "quasi88", "PC98": "np2kai",
    "FBNEO": "fbneo", "NEOGEO": "fbneo", "ATARI800": "atari800", "ATARIST": "hatari",
    "COLECO": "gearcoleco", "INTV": "freeintv", "VECTREX": "vecx", "VB": "mednafen_vb",
    "POKEMINI": "pokemini", "CHANNELF": "freechaf", "SUPERVISION": "potator",
    "MEGADUCK": "sameduck", "DC": "flycast", "PS1": "swanstation", "PSP": "ppsspp",
}


# ── sieć ──

def _open(url: str, hdrs: dict | None = None, timeout: int = 60):
    return urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(hdrs or {})}),
                                  timeout=timeout)


def _json(url: str):
    with _open(url, {"Accept": "application/vnd.github+json"}, 30) as r:
        return json.loads(r.read())


def _download(url: str, dest: Path, progress, cancel: threading.Event | None) -> None:
    with _open(url, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if progress:
            progress(0, total)
        done = 0
        with open(dest, "wb") as f:
            while True:
                if cancel and cancel.is_set():
                    raise InterruptedError("anulowano")
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress:
                    progress(done, total)


def _head_size(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=20) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


# ── 7-Zip ──

def _find_7z() -> str | None:
    for cand in ("7z", "7zz", "7za", r"C:\Program Files\7-Zip\7z.exe",
                 r"C:\Program Files (x86)\7-Zip\7z.exe", str(paths.DATA / "tools" / "7zr.exe")):
        if shutil.which(cand) or Path(cand).is_file():
            return shutil.which(cand) or cand
    return None


def _ensure_7z() -> str:
    found = _find_7z()
    if found:
        return found
    dest = paths.DATA / "tools" / "7zr.exe"
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("pobieram 7zr.exe z 7-zip.org")
    _download("https://www.7-zip.org/a/7zr.exe", dest, None, None)
    return str(dest)


def _extract(archive: Path, target: Path, strip_root: bool = False, sfx: bool = False) -> None:
    target.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="emustart_inst_"))
    try:
        if archive.suffix.lower() == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
        else:   # .7z albo samorozpakowujący .exe (MAME)
            subprocess.run([_ensure_7z(), "x", str(archive), f"-o{tmp}", "-y"],
                           check=True, capture_output=True, creationflags=_NO_WINDOW)
        src = tmp
        entries = list(tmp.iterdir())
        if strip_root and len(entries) == 1 and entries[0].is_dir():
            src = entries[0]
        for root, _dirs, files in os.walk(src):
            for fname in files:
                rel = Path(root).relative_to(src) / fname
                dst = target / rel
                if dst.exists():
                    continue          # świeża instalacja: istniejących plików nie ruszamy
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(Path(root) / fname, dst)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── wersje (wspólny format z ROM Helperem) ──

def _versions_file() -> Path:
    return paths.DATA / "emu_versions.json"


def _save_version(key: str, ver: str) -> None:
    f = _versions_file()
    try:
        v = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        v = {}
    v[key] = ver
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(v, indent=2), encoding="utf-8")


# ── źródła: znajdź plik do pobrania ──

def _resolve_standalone(key: str) -> dict:
    """{'url', 'size', 'version', 'name'} najnowszego wydania."""
    c = STANDALONE[key]
    if c["type"] == "github":
        base = f"https://api.github.com/repos/{c['repo']}/releases"
        rel = next((r for r in _json(base + "?per_page=10") if not r.get("draft")), None) \
            if c.get("prerelease") else _json(base + "/latest")
        rx = re.compile(c["asset"], re.I)
        a = next((a for a in rel.get("assets", []) if rx.search(a["name"])), None)
        if not a:
            raise RuntimeError(f"brak pliku {c['asset']} w wydaniu {rel.get('tag_name')}")
        return {"url": a["browser_download_url"], "size": a.get("size", 0),
                "version": rel.get("tag_name") or "", "name": a["name"]}
    if c["type"] == "dolphin":
        tags = [t["name"] for t in _json("https://api.github.com/repos/dolphin-emu/dolphin/tags?per_page=30")
                if re.fullmatch(r"\d{4}[a-z]?", t["name"])]
        ver = max(tags)
        url = f"https://dl.dolphin-emu.org/releases/{ver}/dolphin-{ver}-x64.7z"
        return {"url": url, "size": _head_size(url), "version": ver, "name": f"dolphin-{ver}-x64.7z"}
    if c["type"] == "eden":
        with _open("https://git.eden-emu.dev/eden-emu/eden/releases", timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
        m = re.search(r"https://stable\.eden-emu\.dev/(v[\d.]+(?:-rc\d+)?)/", html)
        rx = re.compile(c["asset"], re.I)
        url = next((u for u in re.findall(r'https://stable\.eden-emu\.dev/[^"\'<>\s)]+', html)
                    if m and f"/{m.group(1)}/" in u and rx.search(u.rsplit("/", 1)[-1])), None)
        if not url:
            raise RuntimeError("nie umiem odczytać strony wydań Edena")
        return {"url": url, "size": _head_size(url), "version": m.group(1),
                "name": url.rsplit("/", 1)[-1]}
    raise RuntimeError(f"nieznane źródło {c['type']}")


def _retroarch_url() -> tuple:
    with _open(f"{BUILDBOT}/stable/", timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    vers = set(re.findall(r'href="[^"]*?/?(\d+\.\d+(?:\.\d+)?)/?"', html))
    ver = max(vers, key=lambda v: [int(x) for x in v.split(".")])
    return f"{BUILDBOT}/stable/{ver}/windows/x86_64/RetroArch.7z", ver


def core_url(core: str) -> str:
    return f"{BUILDBOT}/nightly/windows/x86_64/latest/{core}_libretro.dll.zip"


# ── plan ──

def _retroarch_exe(emu_root: Path) -> Path | None:
    for p in (emu_root / "RetroArch" / "retroarch.exe", emu_root / "retroarch" / "retroarch.exe"):
        if p.is_file():
            return p
    return None


CAPSIMG_REPO = "FrodeSolheim/capsimg"
KICKSTARTS = ("kick34005.A500", "kick40068.A1200", "kick40063.A600", "kick37175.A500")


def ra_system_dir(ra_root: Path) -> Path:
    """Folder „system” RetroArcha (BIOS-y, capsimg, Kickstarty) wg retroarch.cfg."""
    d = ""
    try:
        m = re.search(r'^\s*system_directory\s*=\s*"?([^"\r\n]*)"?', (ra_root / "retroarch.cfg").read_text(
            encoding="utf-8", errors="replace"), re.M)
        d = m.group(1).strip() if m else ""
    except OSError:
        pass
    if d.startswith(":"):
        d = str(ra_root) + d[1:]
    return Path(d) if d and d != "default" else ra_root / "system"


def amiga_extras(emu_root: str) -> list:
    """Brakujące dodatki Amigi w RetroArchu: capsimg.dll (pliki .ipf)."""
    ra = _retroarch_exe(Path(emu_root))
    ra_root = ra.parent if ra else Path(emu_root) / "RetroArch"
    if (ra_system_dir(ra_root) / "capsimg.dll").is_file():
        return []
    return [{"kind": "capsimg", "key": "capsimg", "label": "capsimg — obsługa obrazów .ipf (Amiga)"}]


def amiga_kickstart_missing(emu_root: str) -> bool:
    ra = _retroarch_exe(Path(emu_root))
    sysdir = ra_system_dir(ra.parent if ra else Path(emu_root) / "RetroArch")
    return not any((sysdir / k).is_file() for k in KICKSTARTS)


def _capsimg_url() -> dict:
    rel = _json(f"https://api.github.com/repos/{CAPSIMG_REPO}/releases/latest")
    for a in rel.get("assets", []):
        if re.search(r"Windows_x86-64\.(tar\.xz|zip)$", a["name"]):
            return {"url": a["browser_download_url"], "size": a.get("size", 0),
                    "version": rel.get("tag_name", ""), "name": a["name"]}
    raise OSError("brak paczki capsimg dla Windows x64")


def plan_for(es: str, emu_root: str) -> list:
    """Co trzeba pobrać, żeby system `es` miał emulator: lista kroków
    [{kind: standalone|retroarch|core, key, label}] (pusta = nie umiemy)."""
    root = Path(emu_root)
    plat = systems.info(es)["plat"]
    for pref in emulators.STANDALONE_PREF.get(plat, []):
        if pref in STANDALONE:
            return [{"kind": "standalone", "key": pref, "label": STANDALONE[pref]["dir"]}]
    core = CORES.get(plat)
    if not core:
        return []
    steps = []
    if not _retroarch_exe(root):
        steps.append({"kind": "retroarch", "key": "retroarch", "label": "RetroArch"})
    steps.append({"kind": "core", "key": core, "label": f"RetroArch: rdzeń {core}"})
    if plat == "AMIGA":
        steps += amiga_extras(emu_root)
    return steps


def describe(steps: list) -> list:
    """Uzupełnia kroki o rozmiar i wersję (zapytania do sieci)."""
    out = []
    for s in steps:
        d = dict(s)
        try:
            if s["kind"] == "standalone":
                r = _resolve_standalone(s["key"])
                d.update(size=r["size"], version=r["version"])
            elif s["kind"] == "retroarch":
                url, ver = _retroarch_url()
                d.update(size=_head_size(url), version=ver)
            elif s["kind"] == "capsimg":
                r = _capsimg_url()
                d.update(size=r["size"], version=r["version"])
            else:
                d.update(size=_head_size(core_url(s["key"])), version="nightly")
        except Exception as ex:
            d.update(size=0, version="", error=str(ex))
        out.append(d)
    return out


# ── wykonanie ──

class Job:
    """Instalacja kroków dla systemów (kolejno). UI odpytuje status()."""

    def __init__(self, cfg: dict, items: list, on_done=None):
        """items = [(es, [kroki])]"""
        self.cfg, self.items, self.on_done = cfg, items, on_done
        self.cancel = threading.Event()
        self.total_steps = sum(len(s) for _es, s in items)
        self.step = 0
        self.current = ""
        self.done_bytes = self.total_bytes = 0
        self.errors: list = []
        self.installed: list = []
        self.finished = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="installer")

    def start(self) -> None:
        self.thread.start()

    def status(self) -> dict:
        return {"running": not self.finished, "step": self.step, "steps": self.total_steps,
                "current": self.current, "done": self.done_bytes, "total": self.total_bytes,
                "errors": list(self.errors), "installed": list(self.installed),
                "cancelled": self.cancel.is_set()}

    def _progress(self, done: int, total: int) -> None:
        self.done_bytes, self.total_bytes = done, total

    def _run(self) -> None:
        root = Path(self.cfg.get("emu_root") or "")
        try:
            for es, steps in self.items:
                for s in steps:
                    if self.cancel.is_set():
                        return
                    self.step += 1
                    self.current = s["label"]
                    self.done_bytes = self.total_bytes = 0
                    try:
                        self._install(root, s)
                        self.installed.append(s["label"])
                    except Exception as ex:
                        log.exception("instalacja %s", s["label"])
                        self.errors.append(f"{s['label']}: {ex}")
                        break             # bez emulatora dalsze kroki systemu nie mają sensu
                self._assign(es)
        finally:
            self.finished = True
            if self.on_done:
                try:
                    self.on_done(self)
                except Exception:
                    log.exception("on_done")

    def _install(self, root: Path, s: dict) -> None:
        # kilka systemów może potrzebować tego samego (RetroArch, rdzeń gambatte
        # dla GB i GBC) — drugi raz nie pobieramy
        if s["kind"] == "core" and (root / "RetroArch" / "cores" / f"{s['key']}_libretro.dll").is_file():
            return
        if s["kind"] == "retroarch" and _retroarch_exe(root):
            return
        if s["kind"] == "capsimg":
            return self._capsimg(root)
        with tempfile.TemporaryDirectory(prefix="emustart_dl_") as td:
            if s["kind"] == "standalone":
                c = STANDALONE[s["key"]]
                r = _resolve_standalone(s["key"])
                arch = Path(td) / r["name"]
                _download(r["url"], arch, self._progress, self.cancel)
                target = root / c["dir"]
                _extract(arch, target, c.get("strip_root", False), c.get("sfx", False))
                if c.get("portable"):
                    # tryb przenośny: ustawienia i save'y w folderze emulatora,
                    # tak jak u Ciebie (profile podpinają foldery save'ów stamtąd)
                    (target / c["portable"]).touch(exist_ok=True)
                _save_version(s["key"], r["version"])
            elif s["kind"] == "retroarch":
                url, ver = _retroarch_url()
                arch = Path(td) / "RetroArch.7z"
                _download(url, arch, self._progress, self.cancel)
                _extract(arch, root / "RetroArch", strip_root=True)
                _save_version("retroarch", ver)
                self._ra_info(root / "RetroArch")
            else:
                cores = root / "RetroArch" / "cores"
                cores.mkdir(parents=True, exist_ok=True)
                arch = Path(td) / f"{s['key']}.zip"
                _download(core_url(s["key"]), arch, self._progress, self.cancel)
                with zipfile.ZipFile(arch) as z:
                    z.extractall(cores)
                self._ra_info(root / "RetroArch")

    def _capsimg(self, root: Path) -> None:
        """capsimg.dll do folderu system RetroArcha — z niej rdzeń PUAE czyta .ipf."""
        ra = _retroarch_exe(root)
        if not ra:
            raise OSError("najpierw potrzebny RetroArch")
        target = ra_system_dir(ra.parent) / "capsimg.dll"
        if target.is_file():
            return
        r = _capsimg_url()
        with tempfile.TemporaryDirectory(prefix="emustart_dl_") as td:
            arch = Path(td) / r["name"]
            _download(r["url"], arch, self._progress, self.cancel)
            data = None
            if r["name"].endswith(".zip"):
                with zipfile.ZipFile(arch) as z:
                    for m in z.infolist():
                        if Path(m.filename).name.lower() == "capsimg.dll":
                            data = z.read(m)
            else:
                import tarfile
                with tarfile.open(arch, "r:*") as t:
                    for m in t.getmembers():
                        if m.isfile() and Path(m.name).name.lower() == "capsimg.dll":
                            data = t.extractfile(m).read()   # sam plik, bez ścieżek z archiwum
            if not data:
                raise OSError("w paczce capsimg nie ma capsimg.dll")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _save_version("capsimg", r["version"])

    def _ra_info(self, ra: Path) -> None:
        """Pliki info/*.info — z nich EmuStart rozpoznaje, który rdzeń obsługuje system."""
        if (ra / "info").is_dir() and any((ra / "info").glob("*.info")):
            return
        with tempfile.TemporaryDirectory(prefix="emustart_info_") as td:
            arch = Path(td) / "info.zip"
            _download(f"{BUILDBOT}/assets/frontend/info.zip", arch, None, self.cancel)
            with zipfile.ZipFile(arch) as z:
                z.extractall(ra / "info")

    def _assign(self, es: str) -> None:
        """Po instalacji: emulator systemu = domyślna opcja (jak po „Wykryj emulatory”)."""
        emulators.forget_scan()
        cur0 = (self.cfg.get("systems") or {}).get(es) or {}
        if cur0.get("exe") and Path(cur0["exe"]).is_file():
            return                    # system ma już emulator (np. doinstalowany capsimg)
        opt = emulators.default_option(systems.info(es), self.cfg.get("emu_root", ""))
        if opt:
            cur = self.cfg.setdefault("systems", {}).setdefault(es, {"enabled": True})
            cur.update(label=opt["label"], exe=opt["exe"], args=opt["args"], enabled=True)
