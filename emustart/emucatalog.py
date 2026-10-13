"""emucatalog — emulatory, które EmuStart Server trzyma i rozdaje komputerom.

Źródła (jak w aktualizatorze emu_updater i dawnym instalatorze klienta): GitHub Releases,
dolphin-emu.org, strona wydań Edena, buildbot.libretro.com (RetroArch i rdzenie).
Z internetu pobiera WYŁĄCZNIE serwer (emustart/emustore.py); komputery biorą emulatory
z serwera (emustart/installer.py).

Pozycja („item”) to jedna instalowana rzecz:
    <klucz samodzielnego>   np. "pcsx2"   → <folder emulatorów>\\PCSX2
    "retroarch"                           → <folder emulatorów>\\RetroArch
    "core-<rdzeń>"          np. "core-swanstation" → RetroArch\\cores\\swanstation_libretro.dll
    "capsimg"                             → folder system RetroArcha (obrazy .ipf Amigi)
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import zipfile
from pathlib import Path

from emustart import emulators, paths, systems

log = logging.getLogger("emustart.emucatalog")

BUILDBOT = "https://buildbot.libretro.com"
UA = {"User-Agent": "EmuStart-Server/1.0"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# samodzielne emulatory; klucz = podłańcuch nazwy w emulators.STANDALONE_PREF
STANDALONE = {
    "duckstation": {"type": "github", "repo": "stenzek/duckstation",
                    "asset": r"duckstation-windows-x64-release\.zip$", "dir": "DuckStation",
                    "portable": "portable.txt", "keep": ["settings.ini", "memcards/*", "cache/*"]},
    "pcsx2": {"type": "github", "repo": "PCSX2/pcsx2", "prerelease": True,
              "asset": r"pcsx2-.*-windows-x64-Qt\.7z$", "dir": "PCSX2", "portable": "portable.ini",
              "keep": ["inis/*", "memcards/*", "sstates/*", "cache/*"]},
    "rpcs3": {"type": "github", "repo": "RPCS3/rpcs3-binaries-win",
              "asset": r"rpcs3-.*_win64.*\.7z$", "dir": "RPCS3",
              "keep": ["config/*", "dev_hdd0/*", "dev_hdd1/*", "GuiConfigs/*", "cache/*"]},
    "ppsspp": {"type": "github", "repo": "hrydgard/ppsspp",
               "asset": r"PPSSPP-v.*-Windows-x64\.zip$", "dir": "PPSSPP", "keep": ["memstick/*"]},
    "vita3k": {"type": "github", "repo": "Vita3K/Vita3K",
               "asset": r"windows-latest\.zip$", "dir": "Vita3K", "keep": ["config.yml", "ux0/*"]},
    "dolphin": {"type": "dolphin", "dir": "Dolphin", "portable": "portable.txt", "keep": ["User/*"]},
    "cemu": {"type": "github", "repo": "cemu-project/Cemu",
             "asset": r"cemu-.*-windows-x64\.zip$", "dir": "Cemu", "strip_root": True,
             "keep": ["settings.xml", "mlc01/*", "keys.txt"]},
    "eden": {"type": "eden", "asset": r"Eden-Windows-.*amd64-clang-pgo\.zip$", "dir": "Eden",
             "keep": ["user/*"]},
    # Citron: oficjalne Forgejo blokuje automaty (HTTP 525) — mirror na GitHubie
    "citron": {"type": "github", "repo": "citron-neo/emulator",
               "asset": r"citron.*(msvc|windows.*x64)\.zip$", "dir": "Citron", "strip_root": True,
               "keep": ["user/*"]},
    "azahar": {"type": "github", "repo": "azahar-emu/azahar",
               "asset": r"azahar-windows-msvc-[\d.]+\.zip$", "dir": "Azahar", "strip_root": True,
               "keep": ["user/*"]},
    "melonds": {"type": "github", "repo": "melonDS-emu/melonDS",
                "asset": r"melonDS-.*-windows-x86_64\.zip$", "dir": "melonDS", "keep": ["melonDS.toml"]},
    "ares": {"type": "github", "repo": "ares-emulator/ares",
             "asset": r"ares-windows-x64\.zip$", "dir": "ares", "strip_root": True, "keep": ["settings.bml"]},
    "snes9x": {"type": "github", "repo": "snes9xgit/snes9x",
               "asset": r"snes9x-.*-win32-x64\.zip$", "dir": "Snes9x", "keep": ["snes9x.conf"]},
    "mgba": {"type": "github", "repo": "mgba-emu/mgba",
             "asset": r"mGBA-.*-win64\.7z$", "dir": "mGBA", "strip_root": True, "keep": ["config.ini"]},
    "flycast": {"type": "github", "repo": "flyinghead/flycast",
                "asset": r"flycast-win64-.*\.zip$", "dir": "Flycast", "keep": ["emu.cfg", "data/*"]},
    "xenia": {"type": "github", "repo": "xenia-canary/xenia-canary-releases",
              "asset": r"xenia_canary_windows_?\.zip$", "dir": "Xenia",
              "keep": ["xenia-canary.config.toml", "content/*", "cache/*"]},
    "xemu": {"type": "github", "repo": "xemu-project/xemu",
             "asset": r"xemu-win-x86_64-release\.zip$", "dir": "xemu", "keep": ["xemu.toml"]},
    "shadps4": {"type": "github", "repo": "shadps4-emu/shadPS4",
                "asset": r"shadps4-win64-(qt|sdl)-?.*\.zip$", "dir": "shadPS4", "keep": ["user/*"]},
    "mame": {"type": "github", "repo": "mamedev/mame",
             "asset": r"mame\d+b_x64\.exe$", "dir": "MAME", "sfx": True,
             "keep": ["mame.ini", "ui.ini", "cfg/*", "nvram/*", "ini/*", "roms/*"]},
}

# pliki użytkownika w folderze RetroArcha — pobierane tylko, gdy ich nie ma
RETROARCH_KEEP = ["retroarch.cfg", "retroarch-core-options.cfg", "config/*", "saves/*", "states/*",
                  "playlists/*", "thumbnails/*", "system/*", "cores/*", "screenshots/*"]
# wszędzie: znaczniki trybu przenośnego i typowe foldery zapisów
COMMON_KEEP = ["portable.txt", "portable.ini", "saves/*", "save/*", "states/*", "memcards/*"]

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

CAPSIMG_REPO = "FrodeSolheim/capsimg"
_SAFE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")   # nazwy rdzeni: snes9x, bsnes-jg…


# ── pozycje ──

def item_info(item: str) -> dict:
    """{kind, label, dir, keep, portable} — `dir` względem folderu emulatorów
    ("" dla capsimg: folder system RetroArcha)."""
    if item in STANDALONE:
        c = STANDALONE[item]
        return {"kind": "standalone", "label": c["dir"], "dir": c["dir"],
                "keep": COMMON_KEEP + c.get("keep", []), "portable": c.get("portable", "")}
    if item == "retroarch":
        return {"kind": "retroarch", "label": "RetroArch", "dir": "RetroArch",
                "keep": COMMON_KEEP + RETROARCH_KEEP, "portable": ""}
    if item.startswith("core-") and _SAFE.match(item[5:]):
        return {"kind": "core", "label": f"RetroArch: rdzeń {item[5:]}", "dir": "RetroArch/cores",
                "keep": [], "portable": ""}
    if item == "capsimg":
        return {"kind": "capsimg", "label": "capsimg — obrazy .ipf (Amiga)", "dir": "", "keep": [],
                "portable": ""}
    raise KeyError(item)


def valid_item(item: str) -> bool:
    try:
        item_info(item)
        return True
    except KeyError:
        return False


def keep_file(item: str, rel: str) -> bool:
    """Plik użytkownika (konfiguracja, zapisy): pobierany tylko, gdy go nie ma."""
    rel = rel.replace("\\", "/")
    return any(fnmatch.fnmatch(rel.lower(), pat.lower()) for pat in item_info(item)["keep"])


def plan(es: str, emu_rec: str = "", core_rec: str = "") -> list:
    """Pozycje potrzebne systemowi: emulator samodzielny albo RetroArch + rdzeń.
    `emu_rec` ("", "standalone", "retroarch") i `core_rec` — zalecenia z panelu serwera."""
    plat = systems.info(es)["plat"]
    if emu_rec != "retroarch":
        for pref in emulators.STANDALONE_PREF.get(plat, []):
            if pref in STANDALONE:
                return [pref]
    core = (core_rec or CORES.get(plat, "")).lower()
    if not core or not _SAFE.match(core):
        return []
    out = ["retroarch", f"core-{core}"]
    if plat == "AMIGA":
        out.append("capsimg")
    return out


# ── wszystkie rdzenie RetroArcha (buildbot) ──

CORES_URL = f"{BUILDBOT}/nightly/windows/x86_64/latest/"
INFO_URL = f"{BUILDBOT}/assets/frontend/info.zip"


def all_cores() -> list:
    """Nazwy wszystkich rdzeni dla Windows x64 z buildbota (np. 'bsnes', 'bsnes-jg', 'snes9x')."""
    with _open(CORES_URL, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    names = sorted(set(re.findall(r"([A-Za-z0-9_-]+)_libretro\.dll\.zip", html)))
    return [n for n in names if _SAFE.match(n)]


def core_databases(info_zip: Path) -> dict:
    """{rdzeń: [bazy libretro]} z plików info/*.info (pole database, np. „Nintendo - Super
    Nintendo Entertainment System”) — po nich rdzeń trafia do systemów."""
    out = {}
    with zipfile.ZipFile(info_zip) as z:
        for n in z.namelist():
            m = re.match(r"^(?:.*/)?([A-Za-z0-9_-]+)_libretro\.info$", n)
            if not m:
                continue
            text = z.read(n).decode("utf-8", "replace")
            d = re.search(r'^\s*database\s*=\s*"([^"]*)"', text, re.M)
            if d and d.group(1).strip():
                out[m.group(1)] = [x.strip() for x in d.group(1).split("|") if x.strip()]
    return out


# ── sieć ──

def _open(url: str, hdrs: dict | None = None, timeout: int = 60, method: str = "GET"):
    return urllib.request.urlopen(urllib.request.Request(url, headers={**UA, **(hdrs or {})}, method=method),
                                  timeout=timeout)


def _json(url: str):
    hdrs = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok and url.startswith("https://api.github.com/"):
        hdrs["Authorization"] = f"Bearer {tok}"
    with _open(url, hdrs, 30) as r:
        return json.loads(r.read())


def download(url: str, dest: Path, progress=None, cancel: threading.Event | None = None) -> None:
    with _open(url, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
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


def _head(url: str) -> dict:
    with _open(url, timeout=20, method="HEAD") as r:
        return {"size": int(r.headers.get("Content-Length") or 0), "modified": r.headers.get("Last-Modified") or ""}


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
    download("https://www.7-zip.org/a/7zr.exe", dest)
    return str(dest)


def unpack(archive: Path, out: Path, strip_root: bool = False) -> Path:
    """Rozpakowuje archiwum (.zip, .7z, samorozpakowujący .exe) do `out`; zwraca folder z
    plikami programu (bez jedynego folderu nadrzędnego, gdy strip_root)."""
    out.mkdir(parents=True, exist_ok=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            for m in z.infolist():                      # bez ścieżek wychodzących poza folder
                if m.filename.startswith(("/", "\\")) or ".." in Path(m.filename).parts:
                    raise OSError(f"podejrzana ścieżka w archiwum: {m.filename}")
            z.extractall(out)
    else:
        subprocess.run([_ensure_7z(), "x", str(archive), f"-o{out}", "-y"],
                       check=True, capture_output=True, creationflags=_NO_WINDOW)
    entries = list(out.iterdir())
    if strip_root and len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return out


# ── najnowsze wersje ──

def _gh_release(repo: str, prerelease: bool) -> dict:
    base = f"https://api.github.com/repos/{repo}/releases"
    rel = next((r for r in _json(base + "?per_page=10") if not r.get("draft")), None) \
        if prerelease else _json(base + "/latest")
    if not rel:
        raise OSError(f"{repo}: brak wydań")
    return rel


def _gh_version(rel: dict) -> str:
    tag = rel.get("tag_name") or rel.get("name") or ""
    # tagi kroczące (DuckStation „latest”, Vita3K „continuous”) — wersja = data publikacji
    if tag in ("latest", "continuous", "preview", "nightly"):
        tag = f"{tag}@{(rel.get('published_at') or '')[:10]}"
    return tag


def latest(item: str) -> dict:
    """Najnowsze wydanie pozycji: {url, version, name, strip_root, sfx, extra}."""
    info = item_info(item)
    if info["kind"] == "standalone":
        c = STANDALONE[item]
        if c["type"] == "github":
            rel = _gh_release(c["repo"], c.get("prerelease", False))
            rx = re.compile(c["asset"], re.I)
            a = next((a for a in rel.get("assets", []) if rx.search(a["name"])), None)
            if not a:
                raise OSError(f"brak pliku {c['asset']} w wydaniu {rel.get('tag_name')}")
            return {"url": a["browser_download_url"], "version": _gh_version(rel), "name": a["name"],
                    "strip_root": c.get("strip_root", False)}
        if c["type"] == "dolphin":
            # dolphin-emu.org wykrywa automaty — numer wydania z tagów GitHuba, paczka z CDN
            tags = [t["name"] for t in _json("https://api.github.com/repos/dolphin-emu/dolphin/tags?per_page=30")
                    if re.fullmatch(r"\d{4}[a-z]?", t["name"])]
            if not tags:
                raise OSError("Dolphin: brak tagów wydań")
            ver = max(tags)
            return {"url": f"https://dl.dolphin-emu.org/releases/{ver}/dolphin-{ver}-x64.7z", "version": ver,
                    "name": f"dolphin-{ver}-x64.7z", "strip_root": True}
        if c["type"] == "eden":
            with _open("https://git.eden-emu.dev/eden-emu/eden/releases", timeout=30) as r:
                html = r.read().decode("utf-8", "replace")
            m = re.search(r"https://stable\.eden-emu\.dev/(v[\d.]+(?:-rc\d+)?)/", html)
            rx = re.compile(c["asset"], re.I)
            url = next((u for u in re.findall(r'https://stable\.eden-emu\.dev/[^"\'<>\s)]+', html)
                        if m and f"/{m.group(1)}/" in u and rx.search(u.rsplit("/", 1)[-1])), None)
            if not url:
                raise OSError("Eden: nie umiem odczytać strony wydań")
            return {"url": url, "version": m.group(1), "name": url.rsplit("/", 1)[-1], "strip_root": True}
        raise OSError(f"nieznane źródło {c['type']}")
    if info["kind"] == "retroarch":
        with _open(f"{BUILDBOT}/stable/", timeout=30) as r:
            html = r.read().decode("utf-8", "replace")
        vers = set(re.findall(r'href="[^"]*?/?(\d+\.\d+(?:\.\d+)?)/?"', html))
        if not vers:
            raise OSError("RetroArch: brak listy wersji na buildbocie")
        ver = max(vers, key=lambda v: [int(x) for x in v.split(".")])
        return {"url": f"{BUILDBOT}/stable/{ver}/windows/x86_64/RetroArch.7z", "version": ver,
                "name": "RetroArch.7z", "strip_root": True,
                "extra": {"info": f"{BUILDBOT}/assets/frontend/info.zip"}}
    if info["kind"] == "core":
        url = f"{BUILDBOT}/nightly/windows/x86_64/latest/{item[5:]}_libretro.dll.zip"
        h = _head(url)                       # buildbot nie podaje wersji rdzeni — data pliku
        from email.utils import parsedate_to_datetime
        try:
            ver = "nightly-" + parsedate_to_datetime(h["modified"]).strftime("%Y%m%d")
        except (TypeError, ValueError):
            ver = f"nightly-{h['size']}"
        return {"url": url, "version": ver, "name": f"{item[5:]}.zip"}
    rel = _json(f"https://api.github.com/repos/{CAPSIMG_REPO}/releases/latest")
    for a in rel.get("assets", []):
        if re.search(r"Windows_x86-64\.(tar\.xz|zip)$", a["name"]):
            return {"url": a["browser_download_url"], "version": rel.get("tag_name", ""), "name": a["name"]}
    raise OSError("brak paczki capsimg dla Windows x64")
