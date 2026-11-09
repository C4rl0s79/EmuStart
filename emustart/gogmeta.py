"""gogmeta — opisy i grafiki gier GOG z folderów GOG Managera obok archiwum depotów (tylko odczyt).

GOG Manager zapisuje dla każdej posiadanej gry folder `<katalog GOG>/<slug>/` (ten sam katalog, w którym leży
`_depots`): `product_<slug>.json` (opis, identyfikator), `galaxy_<slug>.json` (producent, wydawca, gatunki, data
wydania) i grafiki: `boxart_<slug>.jpg` (pionowa okładka), `background_<slug>.jpg`, `titlelogo_<slug>.png` /
`logo_<slug>.jpg`, `iconsquare_<slug>.png` / `icon_<slug>.png`. Starsze foldery mają tylko część plików.

Serwer udostępnia z tego opis i grafiki gier, które są w archiwum depotów (`/v1/gog/games…`). Ścieżki plików
budowane są wyłącznie z nazwy folderu i stałych wzorców — nic od klienta. `game-details.json` (klucze CD, linki
konta) nie jest czytany wcale.
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
from pathlib import Path

log = logging.getLogger("emustart.gogmeta")

SAFE_SLUG = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,120}$")
MAX_JSON = 8 << 20
MAX_IMAGE = 40 << 20
# rodzaj grafiki (jak w Lumen) → pliki w kolejności preferencji
ART_FILES = {
    "grid": ("boxart_{s}.jpg",),
    "hero": ("background_{s}.jpg",),
    "logo": ("titlelogo_{s}.png", "logo_{s}.jpg"),
    "icon": ("iconsquare_{s}.png", "icon_{s}.png"),
}
CTYPE = {".jpg": "image/jpeg", ".png": "image/png"}

_lock = threading.Lock()
META: dict = {"root": "", "games": {}}


def html_to_text(s: str, limit: int = 12000) -> str:
    """Opis GOG (HTML) → zwykły tekst z akapitami."""
    s = str(s or "")
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</?(p|div|h[1-6]|ul|ol)\b[^>]*>", "\n", s)
    s = re.sub(r"(?i)</li>", "\n", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "• ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:limit]


def _read_json(p: Path) -> dict | None:
    try:
        if not p.is_file() or p.stat().st_size > MAX_JSON:
            return None
        d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def _entry(folder: Path) -> tuple | None:
    slug = folder.name
    if not SAFE_SLUG.match(slug):
        return None
    prod = _read_json(folder / f"product_{slug}.json")
    if not prod:
        return None
    pid = str(prod.get("id") or "")
    if not pid.isdigit():
        return None
    gal = _read_json(folder / f"galaxy_{slug}.json") or {}
    desc = prod.get("description") if isinstance(prod.get("description"), dict) else {}
    names = lambda xs: [str(x) for x in xs or [] if isinstance(x, str)][:12]     # noqa: E731
    art = {}
    for kind, pats in ART_FILES.items():
        for pat in pats:
            f = folder / pat.format(s=slug)
            if f.is_file():
                art[kind] = f.name
                break
    return pid, {"dir": str(folder), "slug": slug, "title": str(prod.get("title") or ""),
                 "description": html_to_text(desc.get("full") or desc.get("lead") or ""),
                 "lead": html_to_text(desc.get("lead") or "", 1500),
                 "release": str(gal.get("releaseDate") or prod.get("release_date") or "")[:10],
                 "developers": names(gal.get("developers")), "publishers": names(gal.get("publishers")),
                 "tags": names(gal.get("tags")), "art": art}


def load(root: str) -> int:
    """Foldery gier w `root` (katalog nadrzędny `_depots`). Zwraca liczbę gier z metadanymi."""
    games: dict = {}
    if root:
        try:
            for d in Path(root).iterdir():
                if d.is_dir() and not d.name.startswith(("_", ".")):
                    e = _entry(d)
                    if e:
                        games[e[0]] = e[1]
        except OSError as ex:
            log.info("GOG: metadane z %s niedostępne: %s", root, ex)
    with _lock:
        META.update(root=root, games=games)
    log.info("GOG: metadane %s — gier: %d", root, len(games))
    return len(games)


def clear() -> None:
    with _lock:
        META.update(root="", games={})


def info(pid: str) -> dict:
    """Opis gry dla klienta (bez ścieżek); {} gdy brak folderu z metadanymi."""
    e = META["games"].get(str(pid))
    if not e:
        return {}
    return {k: e[k] for k in ("description", "lead", "release", "developers", "publishers", "tags")} | {
        "art": sorted(e["art"])}


def art_path(pid: str, kind: str) -> Path | None:
    """Plik grafiki gry — tylko z jej folderu i ze stałych wzorców nazw."""
    e = META["games"].get(str(pid))
    if not e or kind not in ART_FILES or kind not in e["art"]:
        return None
    folder = Path(e["dir"])
    f = folder / e["art"][kind]
    try:
        if f.resolve().parent != folder.resolve() or not f.is_file() or f.stat().st_size > MAX_IMAGE:
            return None
    except OSError:
        return None
    return f
