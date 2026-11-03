"""consoles — zdjęcia konsol platform (serwer), do tła platformy w motywach Lumena.

Źródło: Wikimedia Commons przez oficjalne API — studyjne zdjęcia Evana-Amosa (jeden styl dla prawie
każdej konsoli, domena publiczna albo CC BY-SA). Sprawdzone 2026-11-03: motywy ES-DE i Playnite nie mają
zdjęć konsol, a LaunchBox ma je tylko na stronach serwisu, bez API.

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
                "bottom", "controller", "controllers", "accessory", "packaging", "breakdown"}
_busy: set = set()
_lock = threading.Lock()


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
            + ("handheld" in p) + bool(p & {"fl", "fr"}) + t.endswith(".png"))


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
    out, seen = [], set()
    for name in dict.fromkeys([info["display"]] + [n.split(" - ")[-1] for n in systems.names(es)[1:6]] + [es]):
        q = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": "6", "gsrlimit": "30",
             "gsrsearch": f"{name} console Evan-Amos", "prop": "imageinfo", "iiprop": "url|extmetadata",
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
            if (title in seen or not ii.get("thumburl") or not _FREE.match(lic) or _parts(title) & _NOT_CONSOLE
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


def prepare(data: bytes) -> tuple[bytes, float] | None:
    """Obraz → (WebP z przezroczystym tłem, przycięty do konsoli; średnia jasność 0–1). Białe tło studia
    (od krawędzi) staje się przezroczyste. None — to nie obraz."""
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
    im.thumbnail((WIDTH, 1000))
    from PIL import ImageStat
    mask = im.getchannel("A").point(lambda a: 255 if a > 128 else 0)
    lum = ImageStat.Stat(im.convert("L"), mask=mask).mean[0] / 255 if mask.getbbox() else 0.5
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=82)
    return buf.getvalue(), round(lum, 3)


def _store(es: str, data: bytes, meta: dict) -> bool:
    from emustart import mediastore
    got = prepare(data)
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
    return False


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
