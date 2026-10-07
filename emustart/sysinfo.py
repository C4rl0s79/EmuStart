"""sysinfo — informacje o platformie do karuzeli systemów.

Źródła:
  * LaunchBox (Platforms.xml): producent, premiera, nośnik, procesor, pamięć,
    liczba kontrolerów, opis (po angielsku),
  * Wikidata: lata produkcji (P577/P571 premiera, P2669 koniec produkcji),
    producent (P176) z polską nazwą,
  * Wikipedia: opis po polsku (artykuł z Wikidaty), gdy go nie ma — po angielsku;
    gdy nie ma żadnego — opis z LaunchBoksa.
Wynik trzymany w bazie (tabela system_info), pobierany raz, w tle.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse

from emustart import art_sources, launchbox, library, systems

log = logging.getLogger("emustart.sysinfo")

_SCHEMA = """CREATE TABLE IF NOT EXISTS system_info(es TEXT PRIMARY KEY, data TEXT NOT NULL,
                                                  fetched REAL NOT NULL)"""
_busy: set = set()
_lock = threading.Lock()
RETRY_AFTER = 7 * 86400                     # brak danych (np. offline) — ponów po tygodniu


def _json(url: str) -> dict:
    try:
        return json.loads(art_sources.fetch(url, timeout=15) or b"{}")
    except ValueError:
        return {}


def get(es: str) -> dict | None:
    con = library.db()
    con.execute(_SCHEMA)
    r = con.execute("SELECT data, fetched FROM system_info WHERE es=?", (es,)).fetchone()
    if not r:
        return None
    d = json.loads(r["data"])
    if d.get("_empty") and time.time() - r["fetched"] > RETRY_AFTER:
        return None
    return d


def _save(es: str, data: dict) -> None:
    con = library.db()
    with con:
        con.execute(_SCHEMA)
        con.execute("INSERT OR REPLACE INTO system_info VALUES(?,?,?)",
                    (es, json.dumps(data, ensure_ascii=False), time.time()))


def ensure(es_list) -> None:
    """Pobiera w tle brakujące informacje dla podanych systemów."""
    todo = []
    with _lock:
        for es in es_list:
            if es not in _busy and get(es) is None:
                _busy.add(es)
                todo.append(es)
    if todo:
        threading.Thread(target=_run, args=(todo,), daemon=True, name="sysinfo").start()


_current: set = set()


def ensure_now(es: str) -> None:
    """System oglądany w karuzeli — pobierz od razu, poza kolejką."""
    with _lock:
        if es in _current or get(es) is not None:
            return
        _current.add(es)
    threading.Thread(target=_one, args=(es,), daemon=True, name="sysinfo-now").start()


def _one(es: str) -> None:
    try:
        if get(es) is None:
            _save(es, collect(es))
    except Exception:
        log.exception("sysinfo %s", es)
    finally:
        with _lock:
            _current.discard(es)


def _run(todo: list) -> None:
    for es in todo:
        with _lock:
            if es in _current:               # właśnie pobiera go ensure_now
                continue
            _current.add(es)
        try:
            if get(es) is not None:
                continue
            _save(es, collect(es))
            time.sleep(0.5)                  # grzecznie wobec Wikipedii (limity zapytań)
        except Exception:
            log.exception("sysinfo %s", es)
        finally:
            with _lock:
                _busy.discard(es)
                _current.discard(es)


# ── zbieranie ──

def _year(claims: dict, *props) -> str:
    for p in props:
        for c in claims.get(p, []):
            t = (((c.get("mainsnak") or {}).get("datavalue") or {}).get("value") or {}).get("time", "")
            m = re.match(r"[+-](\d{4})", t)
            if m:
                return m.group(1)
    return ""


def _label(qid: str) -> str:
    d = _json("https://www.wikidata.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "wbgetentities", "ids": qid, "props": "labels", "languages": "pl|en",
         "format": "json"}))
    labels = ((d.get("entities") or {}).get(qid) or {}).get("labels") or {}
    return (labels.get("pl") or labels.get("en") or {}).get("value", "")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9+]+", "", s.lower())     # „+” zostaje: Atari 2600 ≠ Atari 2600+


def _wiki_page(queries: list, names: list) -> dict:
    """Artykuł angielskiej Wikipedii o platformie (do powiązania z Wikidatą).

    Spośród wyników wszystkich zapytań wybieramy NAJBARDZIEJ podobny tytuł
    (dokładna zgodność wygrywa) — pierwszy „dość podobny” bywał sąsiednią
    platformą: SNES → NES, Game Boy Advance → Game Boy Advance SP."""
    want = {_norm(n) for n in names if n}
    best, best_score = None, 0.0
    seen = set()
    for q in queries:
        d = _json("https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": q, "srlimit": 8, "format": "json"}))
        for hit in (d.get("query") or {}).get("search", []):
            title = hit.get("title", "")
            if title in seen:
                continue
            seen.add(title)
            clean = re.sub(r"\s*\(.*?\)\s*$", "", title)
            score = 2.0 if _norm(clean) in want else max(art_sources.similarity(clean, n) for n in names)
            if score > best_score:
                best, best_score = title, score
    if not best or best_score < 0.8:
        return {}
    s = _json("https://en.wikipedia.org/api/rest_v1/page/summary/"
              + urllib.parse.quote(best.replace(" ", "_")))
    return s if s.get("type") != "disambiguation" and s.get("wikibase_item") else {}


def collect(es: str) -> dict:
    info = systems.info(es)
    out: dict = {"name": info["display"]}
    lb = launchbox.platform(es)
    if lb:
        out.update({"manufacturer": lb.get("manufacturer") or lb.get("developer") or "",
                    "release": (lb.get("release") or "")[:4], "media": lb.get("media", ""),
                    "cpu": lb.get("cpu", ""), "memory": lb.get("memory", ""),
                    "controllers": lb.get("controllers", ""), "lb_notes": lb.get("notes", "")})
    names = [info["display"], info["libretro"].replace(" - ", " ")] + ([lb["name"]] if lb else [])
    if info["kind"] == "arcade":
        # systemy arcade (FBNeo, MAME) opisujemy jako automaty, nie jako emulator
        page = _json("https://en.wikipedia.org/api/rest_v1/page/summary/Arcade_video_game")
    else:
        kind = "video game console"
        page = _wiki_page([f"{names[-1]} {kind}", f"{info['display']} {kind}", info["display"]], names)
    qid = page.get("wikibase_item", "")
    if qid:
        ent = (_json(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
               .get("entities") or {}).get(qid) or {}
        claims = ent.get("claims") or {}
        start = _year(claims, "P577", "P571")
        end = _year(claims, "P2669", "P576", "P730")
        if start:
            out["release"] = start
        if end:
            out["end"] = end
        mans = [((c.get("mainsnak") or {}).get("datavalue") or {}).get("value", {}).get("id")
                for c in claims.get("P176", [])]
        mans = [m for m in mans if m]
        if mans:
            out["manufacturer"] = ", ".join(x for x in (_label(m) for m in mans[:2]) if x) \
                or out.get("manufacturer", "")
        pl = ((ent.get("sitelinks") or {}).get("plwiki") or {}).get("title")
        if pl:
            s = _json("https://pl.wikipedia.org/api/rest_v1/page/summary/"
                      + urllib.parse.quote(pl.replace(" ", "_")))
            if s.get("extract"):
                out.update(description=s["extract"], description_lang="pl")
        if not out.get("description") and page.get("extract"):
            out.update(description=page["extract"], description_lang="en")
    if not out.get("description") and out.get("lb_notes"):
        out.update(description=out["lb_notes"], description_lang="en")
    if not any(out.get(k) for k in ("description", "manufacturer", "release")):
        out["_empty"] = 1
    return out
