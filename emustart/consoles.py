"""consoles — zdjęcia konsol platform (serwer), do tła platformy w motywach Lumena.

Źródło automatyczne: Wikimedia Commons przez oficjalne API — studyjne zdjęcia Evana-Amosa (jeden styl
dla prawie każdej konsoli, domena publiczna albo CC BY-SA). Do wyboru w panelu (też jako paczka dla
wszystkich systemów): grafiki konsol z motywów ES / ES-DE (THEMES; część bez licencji — widać ją przy
źródle). LaunchBox ma zdjęcia urządzeń tylko na stronach serwisu, bez API.

Plik musi pokazywać konsolę tej platformy (nazwa systemu w nazwie pliku; „PlayStation” to nie
„PlayStation 3”), nie jej wnętrze ani karton, i mieć licencję pozwalającą go pokazać (domena publiczna,
CC0, CC BY, CC BY-SA — z podaniem autora: `credit`). Zdjęcie na białym tle studia jest wycinane.
Magazyn: media/_systems.media — <es>.console.webp, <es>.console.json ({title, credit, license, lum}),
<es>.console.none (sprawdzone — zdjęcia nie ma). Automaty (arcade) — bez zdjęcia.
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
import urllib.parse

from emustart import art_sources, systems

log = logging.getLogger("emustart.consoles")

STORE = "_systems"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIDTH = 1600                      # szerokość pobieranej miniatury (tło pełnego ekranu)
_FREE = re.compile(r"^(public domain|pd\b|cc0|cc[ -]by(-sa)?\b)", re.I)
# człony nazwy pliku: wnętrze, części, opakowanie, widok z tyłu, sam pad — nie zdjęcie konsoli
_NOT_CONSOLE = {"motherboard", "inside", "board", "pcb", "cartridge", "cart", "disc", "box", "logo", "screenshot",
                "manual", "chip", "adapter", "cable", "memory", "label", "teardown", "open", "back",
                "bottom", "controller", "controllers", "accessory", "packaging", "breakdown", "floppy", "datasette"}
# komputery domowe: szukane nazwą typowego modelu (Evan-Amos ich prawie nie ma, a „Amiga console” to CD32;
# sama nazwa platformy daje zrzuty gier i zdjęcia ludzi — sprawdzone 2026-11-04)
_COMPUTERS = {"amiga": "Amiga 500", "amigawhdgames": "Amiga 500", "amigawhddemos": "Amiga 500",
              "c64": "Commodore 64 computer", "vic20": "Commodore VIC-20 computer", "plus4": "Commodore Plus/4 computer",
              "zxspectrum": "Sinclair ZX Spectrum computer", "msx": "MSX computer", "msx2": "MSX2 computer",
              "atari800": "Atari 800 computer", "atarist": "Atari 1040ST computer", "pc88": "NEC PC-8801 computer",
              "pc98": "NEC PC-9801 computer"}
# u komputerów: konsola tej marki (CD32, CDTV, C64GS), zrzut gry / programu, rozebrany sprzęt — nie ten komputer
_NOT_COMPUTER = {"console", "cd32", "cdtv", "c64gs", "screen", "splash", "menu", "title", "game", "palette",
                 "software", "word", "founder", "manager", "advert", "poster", "magazine", "emulator",
                 "opened", "expansions", "cover", "top"}
_busy: set = set()
_lock = threading.Lock()

# motywy ES / ES-DE z grafikami konsol (sprawdzone 2026-11-03 na liście ES-DE i najpopularniejszych
# es-theme-* z GitHuba; pliki nazwane kodami ES jak logo). Trzy różne style; ten sam zestaw renderów
# ma też kilka innych motywów (PlayStation X, RELOAD, Animetaverse). Pominięte: A Touch of Glass,
# rq-full (w folderze „consoles” są pady), NeoGeoClassic (ikony 256 px).
GH = "https://raw.githubusercontent.com/"
THEMES = (    # (nazwa paczki, adres z {name}, licencja z repozytorium, pixel-art)
    ("Rendery — PlayStation X (ES-DE)", GH + "RobZombie9043/playstation-x-es-de/main/_inc/systems/consoles/{name}.webp",
     "brak licencji w repozytorium", False),
    ("Ilustracje — Retrorama CE", GH + "lipebello/es-theme-retrorama-ce/master/art/console/{name}.png", "GPL-3.0", False),
    ("Pixel-art — Analogue 3D (ES-DE)", GH + "seanfletchr/analogue-3d-menu-es-de/main/system/console-isometric/{name}.png",
     "brak licencji w repozytorium", True),
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _variants(es: str) -> list:
    """Nazwy platformy w postaci do porównania z nazwą pliku („ps2”, „playstation2”, „megadrive”…)."""
    out = []
    for n in systems.names(es):
        for part in (n, n.split(" - ")[-1]):
            v = _norm(part)
            if len(v) >= 3 and not v.isdigit() and v not in out:
                out.append(v)
    return out


def _named(title: str, variants: list, others: list = ()) -> list:
    """Nazwy platformy obecne w nazwie pliku — bez innej konsoli tej rodziny: „playstation” w
    „playstation3…” to PS3 (nazwa + cyfra = nazwa innej platformy), „playstationvita1101” to model Vity."""
    tokens = [x for x in re.split(r"[^a-z0-9]+", title.lower()) if x]
    t, ends, n = "", {}, 0
    for k, tok in enumerate(tokens):
        t += tok
        n += len(tok)
        ends[n] = k                                    # koniec członu k w sklejonej nazwie
    out = []
    for v in variants:
        i = t.find(v)
        while i >= 0:
            e = i + len(v)
            nxt = t[e:e + 1]
            k = ends.get(e)
            # osobny człon 1–2 cyfr po nazwie: kolejna generacja („PlayStation-3”, „PlayStation-4”)
            gen = k is not None and k + 1 < len(tokens) and tokens[k + 1].isdigit() and len(tokens[k + 1]) <= 2
            other = nxt.isdigit() and any(o.startswith(v + nxt) for o in others)
            if v[-1].isdigit() or not (gen or other):
                out.append(v)
                break
            i = t.find(v, i + 1)
    return out


def _other_named(title: str, others: list, mine: list) -> bool:
    """W nazwie pliku jest inna platforma — nie ta, której nazwa zawiera się w naszej („nes” w „snes”,
    „gameboy” w „gameboyadvance”, „xbox” w „xbox360”)."""
    t = _norm(title)
    return any(o in t and not any(o in m for m in mine) for o in others)


def _parts(title: str) -> set:
    return set(re.split(r"[^a-z0-9]+", title.lower()))


def _score(title: str) -> int:
    p, t = _parts(title), title.lower()
    return (3 * ("console-set" in t) + 2 * ("console" in p) + ("wcontroller" in p) + ("versions" in p)
            + ("handheld" in p) + bool(p & {"fl", "fr"}) + t.endswith(".png")
            + 2 * ("computer" in p) + 2 * bool(p & {"transparent", "trsp"}))


_ALL: dict = {}


def _others(es: str, mine: list) -> list:
    """Nazwy innych platform, niespokrewnionych z `es` (bez wspólnej nazwy — Amiga i Amiga WHDLoad to
    jedna rodzina)."""
    if not _ALL:
        for k in systems.SYSTEMS:
            _ALL[k] = _variants(k)
    out = []
    for k, vs in _ALL.items():
        if k != es and not set(vs) & set(mine):
            out += vs
    return out


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")).strip()


def search(es: str) -> list:
    """Zdjęcia konsoli z Commons, najlepsze pierwsze: [{title, thumb, page, license, credit}]."""
    info = systems.info(es)
    if info["kind"] == "arcade":
        return []
    variants = _variants(es)
    others = _others(es, variants)
    computer = es in _COMPUTERS
    skip = _NOT_CONSOLE | (_NOT_COMPUTER if computer else set())
    out, seen = [], set()
    names = [_COMPUTERS[es]] if computer else \
        [info["display"]] + [n.split(" - ")[-1] for n in systems.names(es)[1:6]] + [es]
    for name in dict.fromkeys(names):
        q = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": "6", "gsrlimit": "30",
             "gsrsearch": name if computer else f"{name} console Evan-Amos",
             "prop": "imageinfo", "iiprop": "url|extmetadata",
             "iiurlwidth": str(WIDTH)}
        raw = art_sources.fetch(COMMONS_API + "?" + urllib.parse.urlencode(q), timeout=20)
        try:
            pages = (json.loads(raw or b"{}").get("query") or {}).get("pages") or {}
        except ValueError:
            pages = {}
        for p in pages.values():
            title, ii = p.get("title", ""), (p.get("imageinfo") or [{}])[0]
            meta = ii.get("extmetadata") or {}
            lic = _plain((meta.get("LicenseShortName") or {}).get("value", ""))
            mine = _named(title, variants, others)
            if (title in seen or not ii.get("thumburl") or not _FREE.match(lic) or _parts(title) & skip
                    or not mine or _other_named(title, others, mine)):
                continue
            seen.add(title)
            author = _plain((meta.get("Artist") or {}).get("value", "")) or "autor nieznany"
            out.append({"title": title, "thumb": ii["thumburl"], "page": ii.get("descriptionurl", ""), "license": lic,
                        "credit": f"{author}, Wikimedia Commons ({lic})"})
        if out:
            break
    out.sort(key=lambda c: -_score(c["title"]))
    return out


def prepare(data: bytes, pixel: bool = False) -> tuple[bytes, float] | None:
    """Obraz → (WebP z przezroczystym tłem, przycięty do konsoli; średnia jasność 0–1). Białe tło studia
    (od krawędzi) staje się przezroczyste. `pixel` — pixel-art powiększony całkowitą wielokrotnością
    (ostre piksele na tle pełnego ekranu). None — to nie obraz."""
    from PIL import Image, ImageDraw
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        return None
    im = im.convert("RGBA")
    if im.getextrema()[3][0] == 255:                       # bez przezroczystości: tło od rogów
        w, h = im.size
        for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
            if min(im.getpixel(xy)[:3]) >= 215:
                ImageDraw.floodfill(im, xy, (255, 255, 255, 0), thresh=40)
    box = im.getbbox()
    if not box:
        return None
    im = im.crop(box)
    if pixel and im.width < WIDTH // 2:
        k = max(1, (WIDTH // 2 + im.width - 1) // im.width)
        im = im.resize((im.width * k, im.height * k), Image.NEAREST)
    im.thumbnail((WIDTH, 1000), Image.NEAREST if pixel else Image.BICUBIC)
    from PIL import ImageStat
    mask = im.getchannel("A").point(lambda a: 255 if a > 128 else 0)
    lum = ImageStat.Stat(im.convert("L"), mask=mask).mean[0] / 255 if mask.getbbox() else 0.5
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=82, lossless=pixel)          # pixel-art bez przekłamanych kolorów
    return buf.getvalue(), round(lum, 3)


def _store(es: str, data: bytes, meta: dict, pixel: bool = False) -> bool:
    from emustart import mediastore
    got = prepare(data, pixel)
    if not got:
        return False
    img, lum = got
    mediastore.put(STORE, f"{es}.console.webp", img)
    mediastore.put(STORE, f"{es}.console.json", json.dumps({**meta, "lum": lum}, ensure_ascii=False).encode())
    mediastore.delete(STORE, f"{es}.console.none")
    return True


def fetch(es: str, cand: dict) -> bool:
    """Pobiera zdjęcie z propozycji search() i zapisuje je jako konsolę systemu."""
    data = art_sources.fetch(cand["thumb"], timeout=30)
    if not data:
        return False
    return _store(es, data, {k: cand[k] for k in ("title", "credit", "license", "page") if k in cand})


def upload(es: str, data: bytes, credit: str = "") -> bool:
    """Własne zdjęcie konsoli (panel serwera)."""
    return _store(es, data, {"title": "", "credit": credit.strip(), "license": "", "page": ""})


def choose(es: str, cid: str) -> bool:
    """`commons:<File:…>` — zdjęcie z propozycji; `none` — bez zdjęcia; `auto` — wybór automatyczny."""
    from emustart import mediastore
    if cid == "none":
        for f in (f"{es}.console.webp", f"{es}.console.json"):
            mediastore.delete(STORE, f)
        mediastore.put(STORE, f"{es}.console.none", b"")
        return True
    if cid == "auto":                       # następna lista systemów (fetch_missing) pobierze od nowa
        for f in (f"{es}.console.webp", f"{es}.console.json", f"{es}.console.none"):
            mediastore.delete(STORE, f)
        return True
    if cid.startswith("commons:"):
        hit = next((c for c in search(es) if c["title"] == cid[8:]), None)     # tylko z propozycji
        return bool(hit) and fetch(es, hit)
    if cid.startswith("theme:"):
        hit = next((t for t in _theme_urls(es) if t[1] == cid[6:]), None)      # tylko adresy z motywów
        return bool(hit) and _fetch_theme(es, *hit)
    return False


# ── grafiki z motywów (THEMES): propozycje w panelu i paczka dla wszystkich systemów ──

def _exists(url: str) -> bool:
    from emustart import logos
    return logos._exists(url)


def _theme_urls(es: str, only: str = "") -> list:
    """[(paczka, adres)] — wszystkie nazwy systemu w stylu ES (jak logo: „gc”, „genesis”…)."""
    from emustart import logos
    return [(src, tpl.format(name=n)) for src, tpl, _lic, _px in THEMES if not only or src == only
            for n in logos.theme_names(es)]


def _fetch_theme(es: str, src: str, url: str) -> bool:
    _name, tpl, lic, pixel = next(t for t in THEMES if t[0] == src)
    data = art_sources.fetch(url, timeout=20)
    if not data:
        return False
    repo = "https://github.com/" + "/".join(tpl[len(GH):].split("/")[:2])
    return _store(es, data, {"title": src, "credit": f"{src} — motyw ES ({lic})", "license": lic, "page": repo}, pixel)


def theme_candidates(es: str) -> list:
    """Propozycje z motywów: [{source, url, license}] — z każdej paczki pierwsza istniejąca nazwa."""
    from concurrent.futures import ThreadPoolExecutor
    urls = _theme_urls(es)
    with ThreadPoolExecutor(max_workers=8) as ex:          # pustych (404) nie pokazujemy
        found = list(ex.map(lambda t: _exists(t[1]), urls))
    out, seen = [], set()
    for (src, url), ok in zip(urls, found):
        if ok and src not in seen:
            seen.add(src)
            out.append({"source": src, "url": url, "license": next(t[2] for t in THEMES if t[0] == src)})
    return out


def pack_sources() -> list:
    """Paczki grafik konsol do zmiany wszystkich systemów naraz: [{id, license}]."""
    return [{"id": src, "license": lic} for src, _tpl, lic, _px in THEMES]


def apply_pack(source: str, es_list) -> dict:
    """Grafiki konsol wszystkich systemów z jednej paczki (jednolity wygląd); system, którego paczka
    nie ma, zostaje przy swoim. {ok, changed, missing}."""
    if source not in {s for s, *_ in THEMES}:
        return {"ok": False, "reason": "Nie ma takiej paczki grafik konsol."}
    changed, missing = [], []
    for es in es_list:
        hit = next((u for _s, u in _theme_urls(es, source) if _exists(u)), None)
        (changed if hit and _fetch_theme(es, source, hit) else missing).append(es)
    return {"ok": True, "changed": changed, "missing": missing}


def url_for(es: str) -> dict:
    """{url, credit, lum} — url pusty, gdy zdjęcia nie ma."""
    from emustart import mediastore
    t = mediastore.mtime(STORE, f"{es}.console.webp")
    if not t:
        return {"url": "", "credit": "", "lum": 0.0}
    got = mediastore.get(STORE, f"{es}.console.json")
    try:
        meta = json.loads(got[0]) if got else {}
    except ValueError:
        meta = {}
    return {"url": f"/media/{STORE}/{es}.console.webp?v={int(t)}", "credit": meta.get("credit", ""),
            "lum": float(meta.get("lum", 0.5))}


def fetch_missing(es_list) -> None:
    """W tle: zdjęcia konsol dla systemów, które go nie mają i nie są oznaczone jako sprawdzone."""
    from emustart import mediastore
    todo = []
    with _lock:
        for es in es_list:
            if es in _busy or mediastore.exists(STORE, f"{es}.console.webp") \
                    or mediastore.exists(STORE, f"{es}.console.none"):
                continue
            _busy.add(es)
            todo.append(es)
    if todo:
        threading.Thread(target=_run, args=(todo,), daemon=True, name="konsole").start()


def _run(todo: list) -> None:
    from emustart import mediastore
    for es in todo:
        try:
            found = search(es)
            if any(fetch(es, c) for c in found[:3]):
                continue
            # znalezione, ale pobranie się nie udało (sieć) — bez znacznika, następna lista systemów spróbuje znowu
            if not found and (art_sources.online() or systems.info(es)["kind"] == "arcade"):
                mediastore.put(STORE, f"{es}.console.none", b"")          # sprawdzone — zdjęcia nie ma
        except Exception:
            log.exception("konsola %s", es)
        finally:
            with _lock:
                _busy.discard(es)
