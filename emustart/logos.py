"""logos — logo systemów do karuzeli.

Kolejność:
  1. wbudowane (assets/systems/<kod>.png, z PyLinks) — 40 systemów,
  2. pobrane z motywu Carbon dla EmulationStation (RetroPie/es-theme-carbon,
     <folder ES>/art/system.svg) — nazwy folderów ES pasują 1:1,
  3. ikona systemu z RetroArcha (assets/xmb/systematic/png/<nazwa libretro>.png),
     kopiowana lokalnie — bez sieci.
Pobrane trafiają do data/media/_systems/. Brak logo zapamiętujemy (plik .none),
żeby nie pytać sieci przy każdym starcie.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
from pathlib import Path

from emustart import art_sources, paths, systems

log = logging.getLogger("emustart.logos")

CARBON = "https://raw.githubusercontent.com/RetroPie/es-theme-carbon/master/{es}/art/system.svg"
_ALIAS = {"FBNEO": "ARCADE", "SUPERGRAFX": "PCENGINE", "SNESMSU1": "SNES"}
_busy: set = set()
_lock = threading.Lock()


def _dir() -> Path:
    return paths.MEDIA / "_systems"


def bundled(plat: str) -> str:
    for key in (plat, _ALIAS.get(plat, "")):
        if key and (paths.ASSETS / "systems" / f"{key}.png").is_file():
            return f"/assets/systems/{key}.png"
    return ""


def downloaded(es: str) -> str:
    for ext in ("svg", "png"):
        if (_dir() / f"{es}.{ext}").is_file():
            return f"/media/_systems/{es}.{ext}"
    return ""


def custom(es: str) -> str:
    for ext in ("svg", "png", "jpg", "webp"):
        f = _dir() / f"{es}.custom.{ext}"
        if f.is_file():
            return f"/media/_systems/{es}.custom.{ext}?v={int(f.stat().st_mtime)}"
    return ""


def url_for(es: str, cfg: dict | None = None) -> dict:
    """{'url', 'glow'}. Kolejność: wybrane ręcznie, wbudowane, pobrane.
    glow: pobrane logo bywają ciemne, UI dodaje im poświatę (dla wybranych
    ręcznie decyduje ustawienie systemu)."""
    sc = ((cfg or {}).get("systems") or {}).get(es) or {}
    choice = sc.get("logo", "")
    if choice == "none":
        return {"url": "", "glow": False}
    if choice == "custom":
        u = custom(es)
        if u:
            return {"url": u, "glow": bool(sc.get("logo_glow", False))}
    u = bundled(systems.info(es)["plat"])
    if u:
        return {"url": u, "glow": bool(sc.get("logo_glow", False))}
    u = downloaded(es)
    return {"url": u, "glow": bool(sc.get("logo_glow", bool(u)))}


# ── ręczny wybór logo ──

ARTBOOK = ("https://raw.githubusercontent.com/anthonycaccese/art-book-next-es-de/main/"
           "_inc/systems/logos/{es}.svg")
PACK_DEFAULT = Path(r"D:\py\PyLinks\platform_logos")
PACK_LABELS = {"Light_Just_White": "białe", "Light_Color": "kolorowe", "Dark_Just_Black": "czarne"}
RA_THEMES = ("systematic", "flatui", "flatux", "retrosystem", "pixel", "dot-art",
             "monochrome", "daite", "automatic")
_IMG = {".png", ".svg", ".jpg", ".jpeg", ".webp"}

# nazwy platform w stylu LaunchBox (tak nazwane są pliki w paczkach logo)
LB_NAMES = {
    "psx": "Sony Playstation", "ps2": "Sony Playstation 2", "ps3": "Sony Playstation 3",
    "psp": "Sony PSP", "psvita": "Sony Playstation Vita", "snes": "Super Nintendo Entertainment System",
    "megadrive": "Sega Genesis", "mastersystem": "Sega Master System", "fbneo": "Final Burn Neo",
    "tg16": "NEC TurboGrafx", "pcengine": "NEC PC Engine", "supergrafx": "NEC PC Engine SuperGrafx",
    "gameandwatch": "Nintendo Game & Watch", "x360": "Microsoft Xbox 360",
    "xbox360": "Microsoft Xbox 360", "3do": "3DO Interactive Multiplayer", "segacd": "Sega CD",
    "ngp": "SNK Neo Geo Pocket", "ngpc": "SNK Neo Geo Pocket Color", "neogeo": "SNK Neo Geo AES",
    "zxspectrum": "Sinclair ZX Spectrum", "msx": "Microsoft MSX", "msx2": "Microsoft MSX2",
    "fds": "Nintendo Famicom Disk System", "wii": "Nintendo Wii", "gamecube": "Nintendo GameCube",
    "dreamcast": "Sega Dreamcast", "saturn": "Sega Saturn", "atarijaguar": "Atari Jaguar",
}


def _clean(stem: str) -> str:
    """'Atari 2600-01-05' → 'Atari 2600'; 'Sony Playstation 3-,07' → 'Sony Playstation 3'."""
    s = re.sub(r"(?:[\s,;._']*-+[\s,;._']*\d{1,2}(?:\s*\(\d\))?)+$", "", stem)
    return s.strip(" -_.,;'")


def pack_dirs(cfg: dict) -> list:
    """[(folder, etykieta)] paczek logo: ustawienie `logo_pack_dir` albo paczka PyLinks."""
    root = Path(cfg.get("logo_pack_dir") or PACK_DEFAULT)
    out = []
    if (root / "_variants").is_dir():
        root = root / "_variants"
    if root.is_dir():
        subs = [d for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith("_")]
        out += [(d, PACK_LABELS.get(d.name, d.name)) for d in subs]
        if any(f.suffix.lower() in _IMG for f in root.iterdir() if f.is_file()):
            out.append((root, "paczka"))
    return out


def candidates(cfg: dict, es: str) -> list:
    """Propozycje logo: [{id, url, source, label}]. `id` przekazuje się do choose()."""
    from emustart import art_sources, server
    info = systems.info(es)
    out = []
    b = bundled(info["plat"])
    if b:
        out.append({"id": "bundled", "url": b, "source": "wbudowane", "label": info["plat"]})
    d = downloaded(es)
    if d:
        out.append({"id": "downloaded", "url": d, "source": "pobrane wcześniej", "label": es})
    for tpl, src in ((ARTBOOK, "Art Book Next"), (CARBON, "Carbon")):
        u = tpl.format(es=es)
        if _exists(u):                       # pustych ramek (404) nie pokazujemy
            out.append({"id": "url:" + u, "url": u, "source": src, "label": es})
    targets = [t for t in {info["display"], info["libretro"].replace(" - ", " "),
                           LB_NAMES.get(es, "")} if t]
    scored = []
    for folder, label in pack_dirs(cfg):
        for f in folder.iterdir():
            if f.suffix.lower() not in _IMG:
                continue
            name = _clean(f.stem)
            sc = max(art_sources.similarity(name, t) for t in targets)
            if sc >= 0.72:
                scored.append((sc, f, folder, label, name))
    scored.sort(key=lambda x: (-x[0], x[3], x[1].name.lower()))
    for sc, f, folder, label, name in scored[:60]:
        out.append({"id": "file:" + str(f), "url": server.local_url(folder, f),
                    "source": f"paczka ({label})", "label": name})
    ra = Path(cfg.get("emu_root") or "") / "RetroArch" / "assets" / "xmb"
    for theme in RA_THEMES:
        f = ra / theme / "png" / f"{info['libretro']}.png"
        if f.is_file():
            out.append({"id": "file:" + str(f), "url": server.local_url(ra, f),
                        "source": f"RetroArch ({theme})", "label": info["libretro"]})
    return out


def _exists(url: str) -> bool:
    import urllib.request
    try:
        req = urllib.request.Request(url, method="HEAD", headers=art_sources.UA)
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception:
        return False


def choose(cfg: dict, es: str, cid: str) -> bool:
    """Zapisuje wybrane logo jako <es>.custom.<ext> i ustawia je dla systemu."""
    sc = cfg.setdefault("systems", {}).setdefault(es, {})
    if cid in ("bundled", "downloaded", "default"):
        sc["logo"] = ""
        if cid == "downloaded":
            # pobrane wygrywa z wbudowanym tylko jako kopia ręcznie wybrana
            for ext in ("svg", "png"):
                f = _dir() / f"{es}.{ext}"
                if f.is_file():
                    _store_custom(es, f.read_bytes(), ext)
                    sc["logo"] = "custom"
        return True
    if cid == "none":
        sc["logo"] = "none"
        return True
    if cid.startswith("url:"):
        data = art_sources.fetch(cid[4:], timeout=20)
        if not data or (b"<svg" not in data[:3000] and not art_sources.is_image(data)):
            return False
        _store_custom(es, data, "svg" if b"<svg" in data[:3000] else "png")
    elif cid.startswith("file:"):
        f = Path(cid[5:])
        if not f.is_file() or f.suffix.lower() not in _IMG:
            return False
        _store_custom(es, f.read_bytes(), f.suffix.lower().lstrip("."))
    else:
        return False
    sc["logo"] = "custom"
    return True


def _store_custom(es: str, data: bytes, ext: str) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob(f"{es}.custom.*"):
        old.unlink(missing_ok=True)
    (d / f"{es}.custom.{ext}").write_bytes(data)


def fetch_missing(cfg: dict, es_list) -> None:
    """W tle: logo dla systemów bez wbudowanego i bez pobranego."""
    todo = []
    with _lock:
        for es in es_list:
            if es in _busy or bundled(systems.info(es)["plat"]) or downloaded(es) \
                    or (_dir() / f"{es}.none").exists():
                continue
            _busy.add(es)
            todo.append(es)
    if todo:
        threading.Thread(target=_run, args=(cfg, todo), daemon=True, name="logos").start()


def _run(cfg: dict, todo: list) -> None:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    for es in todo:
        try:
            data = art_sources.fetch(CARBON.format(es=es), timeout=15)
            if data and b"<svg" in data[:2000]:
                (d / f"{es}.svg").write_bytes(data)
                continue
            ra = Path(cfg.get("emu_root") or "") / "RetroArch" / "assets" / "xmb" / "systematic" / "png"
            icon = ra / f"{systems.info(es)['libretro']}.png"
            if icon.is_file():
                shutil.copy2(icon, d / f"{es}.png")
            elif data is not None or art_sources.online():
                (d / f"{es}.none").write_text("")       # sprawdzone — logo nie ma
        except Exception:
            log.exception("logo %s", es)
        finally:
            with _lock:
                _busy.discard(es)


# ── wygląd systemów na serwerze EmuStart (dla aplikacji na Androida) ──

_push_lock = threading.Lock()


def _system_state(cfg: dict, es: str):
    """(logo, plik, poświata, nazwa) systemu na tym komputerze; logo: custom/none/default/keep."""
    sc = (cfg.get("systems") or {}).get(es) or {}
    lg = url_for(es, cfg)
    url = lg["url"].split("?")[0]
    f = None
    if sc.get("logo") == "none":
        logo = "none"
    elif url.startswith("/media/_systems/"):
        f = _dir() / url.rsplit("/", 1)[1]
        logo = "custom" if f.is_file() else "keep"
    elif url.startswith("/assets/"):
        logo = "default"
    else:
        logo = "keep"                         # nic tu nie ma — serwer zostaje przy swoim
    name = sc.get("name", "")
    return logo, f, bool(lg["glow"]), name


def push_to_server(cfg: dict, only: list | None = None) -> int:
    """Logo, poświata i nazwa systemów z tego komputera → serwer EmuStart (tylko zmienione
    od ostatniego wysłania). Aplikacja na Androida pokazuje to, co ma serwer."""
    import hashlib
    import http.client
    import urllib.parse
    from emustart import library, netsrc
    ep = netsrc.endpoint(cfg)
    if not ep:
        return 0
    info = netsrc.info(cfg)
    if not info.get("ok") or "system" not in info.get("features", []):
        return 0
    with _push_lock:
        es_list = only or sorted(set((cfg.get("systems") or {})) | {p.name.split(".")[0] for p in _dir().glob("*.*")})
        n = 0
        for es in es_list:
            try:
                logo, f, glow, name = _system_state(cfg, es)
                data = f.read_bytes() if logo == "custom" and f else b""
                sig = hashlib.sha1(f"{logo}|{int(glow)}|{name}".encode() + data).hexdigest()
                if library.meta_get(f"logo_pushed:{ep[0]}:{es}", "") == sig:
                    continue
                q = {"es": es, "logo": logo, "glow": "1" if glow else "0", "name": name}
                if logo == "custom":
                    q["ext"] = f.suffix.lower().lstrip(".")
                c = http.client.HTTPConnection(ep[0], ep[1], timeout=20)
                try:
                    c.request("PUT", "/v1/system?" + urllib.parse.urlencode(q), body=data,
                              headers={"Authorization": f"Bearer {ep[2]}"})
                    r = c.getresponse()
                    r.read()
                finally:
                    c.close()
                if r.status == 200:
                    library.meta_set(f"logo_pushed:{ep[0]}:{es}", sig)
                    n += 1
                else:
                    log.warning("logo %s na serwer: %s", es, r.status)
            except (OSError, http.client.HTTPException, ValueError) as ex:
                log.info("logo %s na serwer: %s", es, ex)
                break
        if n:
            log.info("wygląd %d systemów wysłany na serwer %s", n, ep[0])
        return n


def push_later(cfg: dict, only: list | None = None) -> None:
    threading.Thread(target=lambda: push_to_server(cfg, only), daemon=True, name="logo-serwer").start()
