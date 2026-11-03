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


SVG_FMT = "s=1"     # SVG podawane z rozmiarem (svg_sized) — inny adres niż dawne 0×0 w pamięci przeglądarek


def downloaded(es: str) -> str:
    for ext in ("svg", "png"):
        if _has(f"{es}.{ext}"):
            return f"/media/_systems/{es}.{ext}" + ("?" + SVG_FMT if ext == "svg" else "")
    return ""


def custom(es: str) -> str:
    from emustart import mediastore
    for ext in ("svg", "png", "jpg", "webp"):
        t = mediastore.mtime(STORE, f"{es}.custom.{ext}")
        if t:
            return f"/media/_systems/{es}.custom.{ext}?v={int(t)}" + ("&" + SVG_FMT if ext == "svg" else "")
    return ""


def url_for(es: str, cfg: dict | None = None) -> dict:
    """{'url', 'glow'}. Kolejność: wybrane ręcznie, wbudowane, pobrane.
    glow: pobrane logo bywają ciemne, UI dodaje im poświatę (dla wybranych
    ręcznie decyduje ustawienie systemu)."""
    sc = ((cfg or {}).get("systems") or {}).get(es) or {}
    choice = sc.get("logo", "")
    if choice == "none":
        return {"url": "", "glow": False, "pixel": 0}
    if choice == "custom":
        u = custom(es)
        if u:
            return {"url": u, "glow": bool(sc.get("logo_glow", False)), "pixel": logo_pixel(es, sc)}
    u = bundled(systems.info(es)["plat"])
    if u:
        return {"url": u, "glow": bool(sc.get("logo_glow", False)), "pixel": 0}
    u = downloaded(es)
    return {"url": u, "glow": bool(sc.get("logo_glow", bool(u))), "pixel": 0}


# ── ręczny wybór logo ──

# motywy EmulationStation / ES-DE z logo systemów (nazwy folderów ES pasują 1:1) — pobiera
# serwer, bez żadnych plików na dysku; propozycje sprawdzane naraz (puste ramki pomijane)
# Alekfull NX i Canvas mają zwykle te same pliki logo co Art Book Next — jedna paczka do zmiany wszystkich
# systemów naraz; w ręcznym wyborze osobno, gdy plik się różni (sha1 2026-11-03: 8 z 73 systemów, np. n64dd, wii)
ABN = "Art Book Next / Alekfull NX / Canvas"
ABN_REPOS = {"/art-book-next-es-de/": ("Art Book Next", "CC BY-NC-SA"),
             "/alekfull-nx-es-de/": ("Alekfull NX", "CC BY-NC-SA"), "/canvas-es-de/": ("Canvas", "CC0")}
ES_THEMES = (
    (ABN, GH + "anthonycaccese/art-book-next-es-de/main/_inc/systems/logos/{es}.svg"),
    (ABN, GH + "anthonycaccese/alekfull-nx-es-de/main/_inc/systems/logos/{es}.svg"),     # logo, których ABN nie ma
    (ABN, GH + "Siddy212/canvas-es-de/main/_inc/system-logo/{es}.svg"),
    ("Carbon", CARBON),
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
# Playnite Platform Logos (plat_<kod>.svg) usunięte: kwadrat 256×256 z małą plakietką — w ramce logo jak kropka
# licencje z repozytoriów (README / plik licencji); bez wpisu — nie sprawdzono
LICENSES = {
    ABN: "CC BY-NC-SA (Canvas: CC0)",
    "Aniki Lite (Playnite)": "brak licencji w repozytorium", "Jonos ReMix (Playnite)": "brak licencji w repozytorium",
}
# pierwsze słowo nazwy, które można pominąć („Sony Playstation 2” → „Playstation 2”); inne zostają
# („Super Nintendo Entertainment System” to nie „Nintendo Entertainment System”)
MAKERS = {"sony", "nintendo", "sega", "microsoft", "nec", "snk", "atari", "bandai", "commodore", "sinclair",
          "coleco", "mattel", "magnavox", "philips", "panasonic", "amstrad", "apple", "nokia", "tiger", "watara",
          "gce", "fairchild", "epoch", "casio", "sharp", "fujitsu", "hudson", "vtech", "tandy", "acorn", "msx"}


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
        bare = " ".join(words[1:]) if len(words) > 2 and words[0].lower() in MAKERS else ""
        for v in (n, n.title(), bare, bare.title()):
            if v and v not in out:
                out.append(v)
    return out


def _web(es: str, only: str = "") -> list:
    """[(źródło, adres, etykieta)] logo systemu w motywach ES / ES-DE, Playnite i ikonach RetroArcha
    (`only` — jedno źródło)."""
    import urllib.parse
    names = theme_names(es)
    out = [(src, tpl.format(es=n), n) for src, tpl in ES_THEMES for n in names]
    out += [(src, tpl.format(name=urllib.parse.quote(n)), n) for src, tpl in PN_NAME_THEMES for n in playnite_names(es)]
    lr = systems.info(es)["libretro"]
    if lr:
        out += [(ra_source(t), RA_ICON.format(theme=t, name=urllib.parse.quote(lr)), lr) for t in RA_THEMES]
    return [w for w in out if not only or w[0] == only]


def ra_source(theme: str) -> str:
    return f"RetroArch ({theme})"


def license_of(src: str) -> str:
    """Licencja źródła logo (z repozytorium); pusty — nie sprawdzono."""
    return RA_LICENSE if src.startswith("RetroArch (") else LICENSES.get(src, "")


def pack_sources() -> list:
    """Paczki logo (motywy, ikony RetroArcha) do zmiany logo wszystkich systemów naraz: [{id, license}]."""
    names = list(dict.fromkeys(src for src, _tpl in ES_THEMES + PN_NAME_THEMES)) + [ra_source(t) for t in RA_THEMES]
    return [{"id": src, "license": license_of(src)} for src in names]


def apply_pack(cfg: dict, source: str, es_list, started: bool = False) -> dict:
    """Logo wszystkich systemów z jednej paczki (jednolity wygląd): system, którego paczka nie ma,
    zostaje przy swoim. {ok, changed, missing}. Zapis ustawień — wywołujący."""
    if source not in {p["id"] for p in pack_sources()}:
        return {"ok": False, "reason": "Nie ma takiej paczki logo."}
    changed, missing = [], []
    es_list = list(es_list)
    if es_list and not started:
        PACK.start(len(es_list), f"paczka {source}")           # osobny pasek w panelu (nie pobieranie w tle)
    for es in es_list:
        t0, result, err = PACK.begin(es), "retry", ""
        art_sources.clear_error()
        try:
            hit = next((u for _s, u, _l in _web(es, source) if _exists(u)), None)
            if not hit:
                result = "none"                                  # paczka nie ma tego systemu
            elif choose(cfg, es, "url:" + hit):
                result = "found"
            else:
                err = art_sources.last_error() or "nie udało się pobrać logo"
        except Exception as ex:
            log.exception("paczka logo %s: %s", source, es)
            err = str(ex)[:120]
        finally:
            PACK.end(es, t0, result, err)                      # licznik kończy się także po błędzie
            (changed if result == "found" else missing).append(es)
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
RA_LICENSE = "CC BY 4.0"            # libretro/retroarch-assets (licencja repozytorium, sprawdzone 2026-11-03)
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


# import paczki w toku / wynik ostatniego (pole „Paczka logo platform” w panelu)
IMPORT = {"running": False, "done": 0, "total": 0, "error": ""}


def import_pack(folder: Path) -> int:
    """Folder paczki logo (np. platform_logos z PyLinks: _variants/<wariant>/*.png albo same
    obrazy) → magazyn _logopack — jeden plik zamiast tysięcy małych (kopiowanie serwera,
    kopie zapasowe). Zastępuje poprzednią zawartość. Zwraca liczbę logo.
    Folder bez żadnego obrazka — błąd, magazyn zostaje (zła ścieżka nie kasuje wczytanej paczki)."""
    from emustart import mediastore
    IMPORT.update(running=True, done=0, total=0, error="")
    try:
        root = Path(folder)
        if (root / "_variants").is_dir():
            root = root / "_variants"
        if not root.is_dir():
            raise OSError(f"nie ma folderu {folder}")
        dirs = [(d, d.name) for d in sorted(root.iterdir()) if d.is_dir() and not d.name.startswith("_")]
        dirs.append((root, "paczka"))
        todo = [(f"{variant}{PACK_SEP}{f.name}", f) for d, variant in dirs for f in sorted(d.iterdir())
                if f.is_file() and f.suffix.lower() in _IMG and PACK_SEP not in f.name]
        if not todo:
            raise OSError(f"w folderze {folder} nie ma obrazków logo — paczka bez zmian")
        IMPORT["total"] = len(todo)

        def items():
            for name, f in todo:
                yield name, f.read_bytes(), f.stat().st_mtime
                IMPORT["done"] += 1
        n = mediastore.put_many(PACK_STORE, items())
        names = {name for name, _f in todo}
        for old in set(mediastore.files(PACK_STORE)) - names:
            mediastore.delete(PACK_STORE, old)
        log.info("paczka logo z %s: %d plików", folder, n)
        return n
    except Exception as ex:
        IMPORT["error"] = str(ex)
        raise
    finally:
        IMPORT["running"] = False


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
    web = _web(es)                                    # motywy ES, Playnite i ikony RetroArcha
    with ThreadPoolExecutor(max_workers=12) as ex:    # pustych ramek (404) nie pokazujemy
        found = list(ex.map(lambda w: _exists(w[1]), web))
    seen, abn_files = set(), set()
    for (src, u, label), ok in zip(web, found):
        name, lic = (_abn_repo(u) if src == ABN else (src, license_of(src)))
        if ok and name not in seen:                   # z motywu pierwsza istniejąca nazwa
            seen.add(name)
            if src == ABN:                            # ten sam plik co w motywie wyżej — bez powtórki
                digest = _sha1_of(u)
                if digest and digest in abn_files:
                    continue
                abn_files.add(digest)
            out.append({"id": "url:" + u, "url": u, "source": name, "label": label, "license": lic})
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


def _abn_repo(url: str) -> tuple:
    """(nazwa motywu, licencja) adresu z grupy Art Book Next / Alekfull NX / Canvas."""
    return next((v for k, v in ABN_REPOS.items() if k in url), (ABN, license_of(ABN)))


def _sha1_of(url: str) -> str:
    import hashlib
    data = art_sources.fetch(url, timeout=8)
    return hashlib.sha1(data).hexdigest() if data else ""


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
                    sc["logo_pixel"] = _store_custom(es, data, ext)
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
        sc["logo_pixel"] = _store_custom(es, data, "svg" if b"<svg" in data[:3000] else "png")
    elif cid.startswith("pack:"):
        from emustart import mediastore
        key = cid[5:]
        got = mediastore.get(PACK_STORE, key) if Path(key).suffix.lower() in _IMG else None
        if not got:
            return False
        sc["logo_pixel"] = _store_custom(es, got[0], Path(key).suffix.lower().lstrip("."))
    else:
        return False
    sc["logo"] = "custom"
    return True


_SVG_TAG = re.compile(rb"<svg\b[^>]*>", re.S)


def svg_sized(data: bytes) -> bytes:
    """Logo SVG bez własnego rozmiaru (bez width / height albo w %) dostaje go z viewBox: obraz bez rozmiaru
    ma w ramce logo 0×0 (Lumen, karuzela). Podany wymiar zostaje, brakujący — z proporcji viewBox. Wołane
    przy serwowaniu logo systemów, więc działa też dla zapisanych wcześniej. Inne pliki — bez zmian."""
    m = _SVG_TAG.search(data[:5000])
    if not m:
        return data
    tag = m.group(0)

    def size(name: bytes) -> float | None:
        v = re.search(rb"\s" + name + rb"""\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>/]+))""", tag)
        raw = (v.group(1) or v.group(2) or v.group(3) or b"").strip() if v else b""
        num = re.fullmatch(rb"([0-9]*\.?[0-9]+)(px)?", raw)               # %, em, puste — nie rozmiar
        return float(num.group(1)) if num and float(num.group(1)) > 0 else None
    w, h = size(b"width"), size(b"height")
    if w and h:
        return data
    vb = re.search(rb"""viewBox\s*=\s*["']([^"']+)["']""", tag)
    nums = re.split(rb"[\s,]+", vb.group(1).strip()) if vb else []
    try:
        vw, vh = float(nums[2]), float(nums[3])
    except (IndexError, ValueError):
        return data
    if vw <= 0 or vh <= 0:
        return data
    if w:
        h = w * vh / vw
    elif h:
        w = h * vw / vh
    else:
        w, h = vw, vh
    tag2 = re.sub(rb"""\s(?:width|height)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>/]+)""", b"", tag)
    tag2 = tag2.replace(b"<svg", f'<svg width="{w:g}" height="{h:g}"'.encode(), 1)
    return data[:m.start()] + tag2 + data[m.end():]


def _store_custom(es: str, data: bytes, ext: str) -> int:
    """Zapisuje logo wybrane ręcznie. Zwraca rozmiar piksela grafiki (pixel_block; 0 — nie pixel-art):
    pixel-art traci przezroczysty margines (równo z siatką pikseli), rozmiar pliku zostaje — klienci,
    którzy nie znają znacznika, pokazują go jak dotąd."""
    from emustart import mediastore
    for old in mediastore.files(STORE):
        if old.startswith(f"{es}.custom."):
            mediastore.delete(STORE, old)
    block = pixel_block(data) if ext != "svg" else 0
    if block:
        data, ext = _pixel_crop(data, block), "png"
    _put(f"{es}.custom.{ext}", data)
    return block


def pixel_block(data: bytes) -> int:
    """Rozmiar piksela grafiki w pikselach pliku, gdy logo to pixel-art (twarda przezroczystość, najwyżej 64
    kolory): 8 — ikona z bloków 8×8 (RetroArch dot-art), 1 — pixel-art w natywnym rozmiarze (motyw ES Pixel).
    0 — zwykły obraz (wygładzone krawędzie, wiele kolorów). Urządzenia (Lumen) powiększają pixel-art
    o całe piksele grafiki — ostre i równe."""
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return 0
    w, h = im.size
    if w > 1024 or h > 1024 or sum(im.getchannel("A").histogram()[1:255]) or im.getcolors(64) is None:
        return 0
    for b in (32, 16, 8, 4, 2):
        if w % b or h % b:
            continue
        if im.resize((w // b, h // b), Image.NEAREST).resize((w, h), Image.NEAREST).tobytes() == im.tobytes():
            return b
    return 1


def _pixel_crop(data: bytes, b: int) -> bytes:
    """Pixel-art bez przezroczystego marginesu, cięcie po siatce pikseli grafiki (większe powiększenie)."""
    import io
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    box = im.getbbox()
    if not box:
        return data
    left, top, right, bottom = box
    im = im.crop((left // b * b, top // b * b, -(-right // b) * b, -(-bottom // b) * b))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


_pixel_seen: dict = {}            # (es, czas pliku) → rozmiar piksela — logo wybrane przed 0.56.4


def logo_pixel(es: str, sc: dict) -> int:
    """Rozmiar piksela grafiki logo wybranego ręcznie (0 — nie pixel-art). Wybrane wcześniej, bez wpisu
    w ustawieniach — rozpoznane z pliku raz na wersję pliku."""
    if "logo_pixel" in sc:
        return int(sc.get("logo_pixel") or 0)
    from emustart import mediastore
    for ext in ("png", "webp", "jpg"):
        t = mediastore.mtime(STORE, f"{es}.custom.{ext}")
        if t:
            key = (es, t)
            if key not in _pixel_seen:
                got = mediastore.get(STORE, f"{es}.custom.{ext}")
                _pixel_seen[key] = pixel_block(got[0]) if got else 0
            return _pixel_seen[key]
    return 0


PROGRESS = mediaprogress.Progress("logo")            # pobieranie w tle — pasek w panelu serwera
PACK = mediaprogress.Progress("paczka logo")         # zmiana paczki logo — osobny pasek
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
