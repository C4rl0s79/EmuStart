"""logos — logo systemów do karuzeli.

Kolejność:
  1. wybrane ręcznie (panel serwera albo urządzenie administratora),
  2. wbudowane (assets/systems.media, z PyLinks) — 40 systemów,
  3. pobrane w tle: motyw Carbon dla EmulationStation (<folder ES>/art/system.svg), a gdy go
     nie ma — ikona systemu z motywu RetroArcha (libretro/retroarch-assets).
Do ręcznego wyboru: motywy ES / ES-DE i RetroArcha z internetu (ES_THEMES, RA_THEMES) oraz
paczka logo z folderu ustawionego w panelu serwera.
Pobrane trafiają do media/_systems.media. Brak logo zapamiętujemy (wpis .none), żeby nie
pytać sieci przy każdym starcie.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from emustart import art_sources, mediaprogress, systems

log = logging.getLogger("emustart.logos")

GH = "https://raw.githubusercontent.com/"
CARBON = GH + "RetroPie/es-theme-carbon/master/{es}/art/system.svg"
_ALIAS = {"FBNEO": "ARCADE", "SUPERGRAFX": "PCENGINE", "SNESMSU1": "SNES"}
_busy: set = set()
_lock = threading.Lock()


STORE = "_systems"                    # logo systemów: media/_systems.media (emustart/mediastore.py)


def _has(f: str) -> bool:
    from emustart import mediastore
    return mediastore.exists(STORE, f)


def _get(f: str) -> bytes | None:
    from emustart import mediastore
    got = mediastore.get(STORE, f)
    return got[0] if got else None


def _put(f: str, data: bytes) -> None:
    from emustart import mediastore
    mediastore.put(STORE, f, data)


def drop(es: str) -> None:
    """Wszystkie logo systemu `es` z magazynu (np. lokalna kopia po zmianie na serwerze)."""
    from emustart import mediastore
    for f in mediastore.files(STORE):
        if f.split(".")[0] == es:
            mediastore.delete(STORE, f)


def bundled(plat: str) -> str:
    from emustart import mediastore
    for key in (plat, _ALIAS.get(plat, "")):
        if key and f"{key}.png" in mediastore.bundled_names():
            return f"/assets/systems/{key}.png"
    return ""


def downloaded(es: str) -> str:
    for ext in ("svg", "png"):
        if _has(f"{es}.{ext}"):
            return f"/media/_systems/{es}.{ext}"
    return ""


def custom(es: str) -> str:
    from emustart import mediastore
    for ext in ("svg", "png", "jpg", "webp"):
        t = mediastore.mtime(STORE, f"{es}.custom.{ext}")
        if t:
            return f"/media/_systems/{es}.custom.{ext}?v={int(t)}"
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

# motywy EmulationStation / ES-DE z logo systemów (nazwy folderów ES pasują 1:1) — pobiera
# serwer, bez żadnych plików na dysku; propozycje sprawdzane naraz (puste ramki pomijane)
ES_THEMES = (
    ("Art Book Next", GH + "anthonycaccese/art-book-next-es-de/main/_inc/systems/logos/{es}.svg"),
    ("Carbon", CARBON),
    ("Alekfull NX", GH + "anthonycaccese/alekfull-nx-es-de/main/_inc/systems/logos/{es}.svg"),
    ("Canvas", GH + "Siddy212/canvas-es-de/main/_inc/system-logo/{es}.svg"),
    ("Simple", GH + "RetroPie/es-theme-simple/master/{es}/art/{es}.svg"),
    ("Pixel", GH + "ehettervik/es-theme-pixel/master/{es}/logo.png"),
    ("SNES Mini", GH + "ruckage/es-theme-snes-mini/master/art/logos/{es}.png"),
)
# gdy motyw nie ma własnego logo systemu — logo systemu „rodzica” (WHDLoad → Amiga, MSU-1 → SNES)
ES_PARENT = {"amigawhddemos": "amiga", "amigawhdgames": "amiga", "SNESMSU1": "snes", "msx2": "msx",
             "n64dd": "n64", "fbneo": "arcade", "xbox360": "x360"}


# motywy Playnite (sprawdzone 2026-11-03): logo nazwane jak platformy LaunchBox / Playnite
# („Sony Playstation 2.png”, „Nintendo 64.png”) albo krótkim kodem („plat_n64.svg”)
PN_NAME_THEMES = (
    ("Aniki Lite (Playnite)", GH + "Mike-Aniki/Aniki-Lite/main/Source/Icons/Platform/{name}.png"),
    ("Jonos ReMix (Playnite)", GH + "jonosellier/ReMiX_jonosellier/main/source/Icons/Filter/{name}.png"),
)
PN_CODE_THEMES = (
    ("Playnite Platform Logos", GH + "itscosmyx/Playnite-Platform-Logos/main/plat_{es}.svg"),
)
# licencje z repozytoriów (README / plik licencji); bez wpisu — nie sprawdzono
LICENSES = {
    "Art Book Next": "CC BY-NC-SA", "Alekfull NX": "CC BY-NC-SA", "Canvas": "CC0",
    "Aniki Lite (Playnite)": "brak licencji w repozytorium", "Jonos ReMix (Playnite)": "brak licencji w repozytorium",
    "Playnite Platform Logos": "brak licencji w repozytorium",
}


def playnite_names(es: str) -> list:
    """Nazwy platformy w stylu LaunchBox / Playnite, w kilku zapisach (GitHub rozróżnia wielkość liter):
    „Sony Playstation 2”, „Playstation 2”, „PlayStation 2”."""
    from emustart import launchbox
    info = systems.info(es)
    base = [launchbox.PLATFORMS.get(es, ""), LB_NAMES.get(es, ""), info["display"]]
    base += [n.replace(" - ", " ") for n in systems.names(es) if " - " in n][:2]
    out = []
    for n in base:
        words = n.split()
        for v in (n, n.title(), " ".join(words[1:]) if len(words) > 2 else "", " ".join(words[1:]).title() if len(words) > 2 else ""):
            if v and v not in out:
                out.append(v)
    return out


def _web(es: str, only: str = "") -> list:
    """[(źródło, adres, etykieta)] logo systemu w motywach ES / ES-DE i Playnite (`only` — jedno źródło)."""
    import urllib.parse
    names = theme_names(es)
    out = [(src, tpl.format(es=n), n) for src, tpl in ES_THEMES + PN_CODE_THEMES for n in names]
    out += [(src, tpl.format(name=urllib.parse.quote(n)), n) for src, tpl in PN_NAME_THEMES for n in playnite_names(es)]
    return [w for w in out if not only or w[0] == only]


def pack_sources() -> list:
    """Paczki logo (motywy) do zmiany logo wszystkich systemów naraz: [{id, license}]."""
    return [{"id": src, "license": LICENSES.get(src, "")}
            for src, _tpl in ES_THEMES + PN_CODE_THEMES + PN_NAME_THEMES]


def apply_pack(cfg: dict, source: str, es_list) -> dict:
    """Logo wszystkich systemów z jednej paczki (jednolity wygląd): system, którego paczka nie ma,
    zostaje przy swoim. {ok, changed, missing}. Zapis ustawień — wywołujący."""
    if source not in {p["id"] for p in pack_sources()}:
        return {"ok": False, "reason": "Nie ma takiej paczki logo."}
    changed, missing = [], []
    for es in es_list:
        hit = next((u for _s, u, _l in _web(es, source) if _exists(u)), None)
        (changed if hit and choose(cfg, es, "url:" + hit) else missing).append(es)
    return {"ok": True, "changed": changed, "missing": missing}


def theme_names(es: str) -> list:
    """Nazwy folderów do sprawdzenia w motywach ES / ES-DE: kod ES i inne nazwy w stylu ES
    (systems.names: „atarilynx”, „gc”, „genesis”…), potem system „rodzica"."""
    out = []
    for e in [es] + ([ES_PARENT[es]] if es in ES_PARENT else []):
        names_ = systems.names(e)
        # + nazwa wyświetlana i libretro sklejone: „Atari Lynx” → atarilynx (styl ES-DE)
        for n in names_ + [re.sub(r"[^a-z0-9]+", "", n.lower()) for n in names_[1:3]]:
            v = n.lower()
            if re.fullmatch(r"[a-z0-9-]+", v) and v not in out:
                out.append(v)
    return out


# ikony systemów z motywów menu RetroArcha (repozytorium libretro/retroarch-assets)
RA_ICON = GH + "libretro/retroarch-assets/master/xmb/{theme}/png/{name}.png"
RA_THEMES = ("systematic", "monochrome", "flatui", "flatux", "retrosystem", "pixel", "dot-art", "daite", "automatic")
PACK_LABELS = {"Light_Just_White": "białe", "Light_Color": "kolorowe", "Dark_Just_Black": "czarne"}
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


PACK_STORE = "_logopack"      # paczka logo w jednym pliku: media/_logopack.media
PACK_SEP = "~"                # nazwa w magazynie: <wariant>~<plik>, np. Light_Color~Sega Saturn-01.png


def import_pack(folder: Path) -> int:
    """Folder paczki logo (np. platform_logos z PyLinks: _variants/<wariant>/*.png albo same
    obrazy) → magazyn _logopack — jeden plik zamiast tysięcy małych (kopiowanie serwera,
    kopie zapasowe). Zastępuje poprzednią zawartość. Zwraca liczbę logo."""
    from emustart import mediastore
    root = Path(folder)
    if (root / "_variants").is_dir():
        root = root / "_variants"
    if not root.is_dir():
        raise OSError(f"nie ma folderu {folder}")
    dirs = [(d, d.name) for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith("_")]
    dirs.append((root, "paczka"))
    names = set()

    def items():
        for d, variant in dirs:
            for f in sorted(d.iterdir()):
                if f.is_file() and f.suffix.lower() in _IMG and PACK_SEP not in f.name:
                    names.add(f"{variant}{PACK_SEP}{f.name}")
                    yield f"{variant}{PACK_SEP}{f.name}", f.read_bytes(), f.stat().st_mtime
    n = mediastore.put_many(PACK_STORE, items())
    for old in set(mediastore.files(PACK_STORE)) - names:
        mediastore.delete(PACK_STORE, old)
    log.info("paczka logo z %s: %d plików", folder, n)
    return n


def _pack_entries() -> list:
    """[(nazwa w magazynie, etykieta wariantu, nazwa pliku)]."""
    from emustart import mediastore
    out = []
    for name in mediastore.files(PACK_STORE):
        variant, _, f = name.partition(PACK_SEP)
        if f:
            out.append((name, PACK_LABELS.get(variant, variant), f))
    return out


def candidates(cfg: dict, es: str) -> list:
    """Propozycje logo: [{id, url, source, label}]. `id` przekazuje się do choose()."""
    from emustart import art_sources
    info = systems.info(es)
    out = []
    b = bundled(info["plat"])
    if b:
        out.append({"id": "bundled", "url": b, "source": "wbudowane", "label": info["plat"]})
    d = downloaded(es)
    if d:
        out.append({"id": "downloaded", "url": d, "source": "pobrane wcześniej", "label": es})
    import urllib.parse
    from concurrent.futures import ThreadPoolExecutor
    web = _web(es)
    web += [(f"RetroArch ({t})", RA_ICON.format(theme=t, name=urllib.parse.quote(info["libretro"])),
             info["libretro"]) for t in RA_THEMES if info["libretro"]]
    with ThreadPoolExecutor(max_workers=12) as ex:    # pustych ramek (404) nie pokazujemy
        found = list(ex.map(lambda w: _exists(w[1]), web))
    seen = set()
    for (src, u, label), ok in zip(web, found):
        if ok and src not in seen:                    # z motywu pierwsza istniejąca nazwa
            seen.add(src)
            out.append({"id": "url:" + u, "url": u, "source": src, "label": label, "license": LICENSES.get(src, "")})
    # paczka (nazwy jak LaunchBox: „Atari Lynx”, „Sony Playstation”) — po wszystkich pełnych nazwach
    targets = [t for t in dict.fromkeys([n.replace(" - ", " ") for n in systems.names(es) if " " in n]
                                        + [info["display"], LB_NAMES.get(es, "")]) if t]
    scored = []
    for key, label, f in _pack_entries():
        name = _clean(Path(f).stem)
        sc = max(art_sources.similarity(name, t) for t in targets)
        if sc >= 0.72:
            scored.append((sc, key, label, name))
    scored.sort(key=lambda x: (-x[0], x[2], x[1].lower()))
    for sc, key, label, name in scored[:60]:
        out.append({"id": "pack:" + key, "url": f"/media/{PACK_STORE}/" + urllib.parse.quote(key),
                    "source": f"paczka ({label})", "label": name})
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
                data = _get(f"{es}.{ext}")
                if data:
                    _store_custom(es, data, ext)
                    sc["logo"] = "custom"
        return True
    if cid == "none":
        sc["logo"] = "none"
        return True
    if cid.startswith("url:"):
        if not cid[4:].startswith(GH):        # tylko adresy z propozycji (motywy na GitHubie)
            return False
        data = art_sources.fetch(cid[4:], timeout=20)
        if not data or (b"<svg" not in data[:3000] and not art_sources.is_image(data)):
            return False
        _store_custom(es, data, "svg" if b"<svg" in data[:3000] else "png")
    elif cid.startswith("pack:"):
        from emustart import mediastore
        key = cid[5:]
        got = mediastore.get(PACK_STORE, key) if Path(key).suffix.lower() in _IMG else None
        if not got:
            return False
        _store_custom(es, got[0], Path(key).suffix.lower().lstrip("."))
    else:
        return False
    sc["logo"] = "custom"
    return True


def _store_custom(es: str, data: bytes, ext: str) -> None:
    from emustart import mediastore
    for old in mediastore.files(STORE):
        if old.startswith(f"{es}.custom."):
            mediastore.delete(STORE, old)
    _put(f"{es}.custom.{ext}", data)


PROGRESS = mediaprogress.Progress("logo")            # pobieranie w tle — pasek w panelu serwera
_retry_at: dict = {}              # es → kiedy znowu próbować po błędzie sieci (nie przy każdym zapytaniu urządzenia)
RETRY_AFTER = 600


def fetch_missing(cfg: dict, es_list) -> None:
    """W tle: logo dla systemów bez wbudowanego i bez pobranego."""
    import time
    todo = []
    with _lock:
        for es in es_list:
            if es in _busy or bundled(systems.info(es)["plat"]) or downloaded(es)                     or _has(f"{es}.none") or _retry_at.get(es, 0) > time.monotonic():
                continue
            _busy.add(es)
            todo.append(es)
    if todo:
        PROGRESS.start(len(todo))
        threading.Thread(target=_run, args=(cfg, todo), daemon=True, name="logos").start()


def _run(cfg: dict, todo: list) -> None:
    import time
    import urllib.parse
    for es in todo:
        t0, result, err = PROGRESS.begin(es), "retry", ""
        art_sources.clear_error()
        try:
            data = art_sources.fetch(CARBON.format(es=es), timeout=15)
            if data and b"<svg" in data[:2000]:
                _put(f"{es}.svg", data)
                result = "found"
                continue
            name = systems.info(es)["libretro"]
            icon = art_sources.fetch(RA_ICON.format(theme="systematic", name=urllib.parse.quote(name)),
                                     timeout=15) if name else None
            if icon and art_sources.is_image(icon):
                _put(f"{es}.png", icon)
                result = "found"
            # oba źródła odpowiedziały (np. 404) — sprawdzone, logo nie ma; błąd sieci — ponowna próba po przerwie
            elif not art_sources.last_error() and (data is not None or art_sources.online()):
                _put(f"{es}.none", b"")
                result = "none"
            else:
                err = art_sources.last_error() or "brak sieci"
        except Exception as ex:
            log.exception("logo %s", es)
            err = str(ex)[:120]
        finally:
            PROGRESS.end(es, t0, result, err)
            with _lock:
                _busy.discard(es)
                if result == "retry":
                    _retry_at[es] = time.monotonic() + RETRY_AFTER
                else:
                    _retry_at.pop(es, None)
