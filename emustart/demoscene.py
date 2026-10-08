"""demoscene — dane o demach z Pouet.net i Demozoo (dema WHDLoad).

Paczka WHDLoad dema ma w nazwie tytuł i grupę („VectorBalls_v1.0_Hypnosis”).
Szukamy produkcji na Amigę o tym tytule; grupa rozstrzyga, gdy dem o tej samej
nazwie jest kilka. Pouet daje grupy, datę, party z miejscem, typ i zrzut ekranu
(oraz odnośnik do Demozoo), Demozoo — opis i zrzuty, gdy Pouet nie ma.

Publiczne API, bez kluczy; zapytania co najmniej 1 s od siebie.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request

from emustart import __version__

log = logging.getLogger("emustart.demoscene")

UA = {"User-Agent": f"EmuStart/{__version__} (+https://github.com/C4rl0s79/EmuStart)"}
POUET = "https://api.pouet.net/v1"
DEMOZOO = "https://demozoo.org/api/v1"
DZ_AMIGA = (5, 6)                     # Amiga OCS/ECS, Amiga AGA
TYPES = {"demo": "Demo", "intro": "Intro", "musicdisk": "Music Disk", "diskmag": "Diskmag",
         "slideshow": "Slideshow", "cracktro": "Cracktro", "game": "Game", "demopack": "Demopack",
         "tool": "Tool", "wild": "Wild", "64k": "Intro 64k", "40k": "Intro 40k", "4k": "Intro 4k"}

_lock = threading.Lock()
_last = [0.0]


def _get(url: str) -> dict | None:
    with _lock:                       # grzecznie: najwyżej 1 zapytanie na sekundę
        wait = 1.0 - (time.monotonic() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as ex:
        log.info("demoscena %s: %s", url.split("?")[0], ex)
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _sim(a: str, b: str) -> float:
    from emustart import art_sources
    return art_sources.similarity(a, b)


def split_whd(stem: str) -> tuple:
    """„VectorBalls_v1.0_Hypnosis” → („Vector Balls”, [„Hypnosis”])."""
    from emustart import systems
    title, tags = systems.whd_title(stem, demo=True)
    groups = []
    for t in re.findall(r"\(([^)]*)\)", tags):
        if re.fullmatch(r"v\d[\d.]*[a-z]?", t, re.I) or t in systems._WHD_TAGS.values() \
                or t.lower() in systems._WHD_LANGS:
            continue
        groups += [g.strip() for g in t.split("&") if g.strip()]
    return title, groups


def _groups_match(theirs: list, ours: list) -> bool:
    a = {_norm(g) for g in theirs if _norm(g)}
    b = {_norm(g) for g in ours if _norm(g)}
    return bool(a & b) or any(x in y or y in x for x in a for y in b if min(len(x), len(y)) >= 4)


def _pick(cands: list, title: str, groups: list):
    """cands: [(nazwa, [grupy], obiekt)] → najlepszy albo None."""
    best, score = None, 0.0
    for name, gr, obj in cands:
        s = 3.0 if _norm(name) == _norm(title) else (1.5 if _sim(name, title) >= 0.8 else 0.0)
        if not s:
            continue
        if groups and _groups_match(gr, groups):
            s += 2
        elif groups and gr:
            s -= 2                    # inna grupa — inne demo o tej nazwie (np. „Megademo”)
        if s > score:
            best, score = obj, s
    return best if score >= 2 else None


def _pouet(title: str, groups: list) -> dict | None:
    d = _get(f"{POUET}/search/prod/?q=" + urllib.parse.quote(title))
    res = (d or {}).get("results") or {}
    items = list(res.values()) if isinstance(res, dict) else list(res)
    cands = []
    for r in items:
        plats = r.get("platforms") or {}
        names = [p.get("name", "") for p in (plats.values() if isinstance(plats, dict) else plats)
                 if isinstance(p, dict)]
        if not any("amiga" in n.lower() for n in names):
            continue
        cands.append((r.get("name", ""), [g.get("name", "") for g in r.get("groups") or []], r))
    r = _pick(cands, title, groups)
    if not r:
        return None
    det = (_get(f"{POUET}/prod/?id={r['id']}") or {}).get("prod") or r
    party = det.get("party") or {}
    out = {
        "source": "pouet", "id": det.get("id"), "name": det.get("name", ""),
        "groups": [g.get("name", "") for g in det.get("groups") or []],
        "date": det.get("releaseDate") or "", "type": det.get("type") or "",
        "party": party.get("name", "") if isinstance(party, dict) else "",
        "party_year": det.get("party_year") or "", "place": det.get("party_place") or "",
        "compo": det.get("party_compo_name") or "",
        "screenshot": det.get("screenshot") or "", "description": "",
        "url": f"https://www.pouet.net/prod.php?which={det.get('id')}",
    }
    dz = str(det.get("demozoo") or "").strip()
    if dz.isdigit() and int(dz) > 0:          # Pouet daje „0”, gdy nie ma odnośnika
        z = _demozoo_detail(str(dz))
        if z:
            out["description"] = z.get("description", "")
            out["screenshot"] = out["screenshot"] or z.get("screenshot", "")
    return out


def _demozoo_detail(pid: str) -> dict | None:
    d = _get(f"{DEMOZOO}/productions/{urllib.parse.quote(pid)}/")
    if not d:
        return None
    shots = d.get("screenshots") or []
    notes = re.sub(r"\[/?[a-z]+[^\]]*\]", "", d.get("notes") or "").strip()   # znaczniki BBCode
    return {
        "source": "demozoo", "id": d.get("id"), "name": d.get("title", ""),
        "groups": [a.get("name", "") for a in d.get("author_nicks") or []],
        "date": d.get("release_date") or "", "type": ((d.get("types") or [{}])[0]).get("name", ""),
        "party": "", "party_year": "", "place": "", "compo": "",
        "screenshot": (shots[0].get("original_url") or shots[0].get("standard_url") or "") if shots else "",
        "description": notes[:2000], "url": f"https://demozoo.org/productions/{d.get('id')}/",
    }


def _demozoo(title: str, groups: list) -> dict | None:
    cands = []
    for plat in DZ_AMIGA:
        d = _get(f"{DEMOZOO}/productions/?title=" + urllib.parse.quote(title) + f"&platform={plat}")
        for r in (d or {}).get("results") or []:
            cands.append((r.get("title", ""), [a.get("name", "") for a in r.get("author_nicks") or []], r))
    r = _pick(cands, title, groups)
    return _demozoo_detail(str(r["id"])) if r else None


def find(stem: str) -> dict | None:
    """Produkcja dla paczki WHDLoad dema (nazwa pliku bez rozszerzenia)."""
    title, groups = split_whd(stem)
    if not title:
        return None
    return _pouet(title, groups) or _demozoo(title, groups)


def metadata(p: dict) -> dict:
    """Pola EmuStart z produkcji."""
    year = (p.get("date") or "")[:4] or str(p.get("party_year") or "")
    groups = " & ".join(g for g in p.get("groups") or [] if g)
    kind = TYPES.get((p.get("type") or "").lower(), (p.get("type") or "").capitalize())
    lines = []
    if p.get("party"):
        place = f", {p['place']}. miejsce" if str(p.get("place") or "").strip("0") else ""
        compo = f" ({p['compo']})" if p.get("compo") else ""
        lines.append(f"{p['party']} {p.get('party_year') or year}{compo}{place}.".replace("  ", " "))
    if p.get("description"):
        lines.append(p["description"])
    lines.append(f"Źródło: {p.get('url', '')}")
    out = {"year": year, "developer": groups, "publisher": groups, "genre": kind,
           "description": "\n\n".join(lines), "scene_url": p.get("url", "")}
    return {k: v for k, v in out.items() if v}
