"""metadata — producent, wydawca, rok, gatunek, gracze, opis.

Źródła:
  1. bazy RetroArcha (database/rdb/<system>.rdb) — offline, dopasowanie po nazwie
     No-Intro/Redump albo nazwie setu arcade; producent, wydawca, rok, gatunek,
     liczba graczy, numer seryjny,
  2. IGDB — opis (summary/storyline) i uzupełnienie brakujących pól,
  3. Wikipedia (pl, potem en) — streszczenie artykułu jako opis rozszerzony.

Ręczne zmiany użytkownika trzymamy osobno (`edits`) i mają pierwszeństwo przed
danymi pobranymi — ponowne pobranie ich nie nadpisze.
"""

from __future__ import annotations

import json
import logging
import re
import struct
import threading
import time
import urllib.parse
from pathlib import Path

from emustart import art_sources, library, paths, systems

log = logging.getLogger("emustart.metadata")

FIELDS = ("developer", "publisher", "year", "genre", "players", "description", "wiki")

# ── format libretrodb (.rdb): nagłówek RARCHDB + rekordy msgpack ──


class _Msgpack:
    """Minimalny dekoder msgpack — tyle, ile używa libretrodb."""

    def __init__(self, data: bytes):
        self.d, self.i = data, 0

    def _take(self, n: int) -> bytes:
        b = self.d[self.i:self.i + n]
        self.i += n
        return b

    def _uint(self, n: int) -> int:
        return int.from_bytes(self._take(n), "big")

    def read(self):
        t = self.d[self.i]
        self.i += 1
        if t <= 0x7F:
            return t
        if 0x80 <= t <= 0x8F:
            return self._map(t & 0x0F)
        if 0x90 <= t <= 0x9F:
            return [self.read() for _ in range(t & 0x0F)]
        if 0xA0 <= t <= 0xBF:
            return self._take(t & 0x1F).decode("utf-8", "replace")
        if t == 0xC0:
            return None
        if t in (0xC2, 0xC3):
            return t == 0xC3
        if t in (0xC4, 0xC5, 0xC6):
            return self._take(self._uint({0xC4: 1, 0xC5: 2, 0xC6: 4}[t]))
        if t in (0xCC, 0xCD, 0xCE, 0xCF):
            return self._uint({0xCC: 1, 0xCD: 2, 0xCE: 4, 0xCF: 8}[t])
        if t in (0xD0, 0xD1, 0xD2, 0xD3):
            n = {0xD0: 1, 0xD1: 2, 0xD2: 4, 0xD3: 8}[t]
            return int.from_bytes(self._take(n), "big", signed=True)
        if t in (0xD9, 0xDA, 0xDB):
            return self._take(self._uint({0xD9: 1, 0xDA: 2, 0xDB: 4}[t])).decode("utf-8", "replace")
        if t in (0xDC, 0xDD):
            return [self.read() for _ in range(self._uint(2 if t == 0xDC else 4))]
        if t in (0xDE, 0xDF):
            return self._map(self._uint(2 if t == 0xDE else 4))
        if t >= 0xE0:
            return t - 0x100
        raise ValueError(f"msgpack: nieobsługiwany typ 0x{t:02x}")

    def _map(self, n: int) -> dict:
        out = {}
        for _ in range(n):
            k = self.read()
            out[k] = self.read()
        return out


_KEEP = ("name", "rom_name", "developer", "publisher", "genre", "releaseyear",
         "users", "franchise", "serial")


def parse_rdb(path: Path) -> list:
    data = path.read_bytes()
    if not data.startswith(b"RARCHDB\0"):
        raise ValueError("to nie jest plik libretrodb")
    mp = _Msgpack(data)
    mp.i = 16
    out = []
    while mp.i < len(data):
        rec = mp.read()
        if not isinstance(rec, dict):
            break                     # nil kończy rekordy, dalej są metadane
        row = {}
        for k in _KEEP:
            v = rec.get(k)
            if isinstance(v, bytes):
                v = v.decode("utf-8", "replace")
            if v not in (None, ""):
                row[k] = v
        if row.get("name") or row.get("rom_name"):
            out.append(row)
    return out


_rdb_lock = threading.Lock()
_rdb_mem: dict = {}


def _rdb_dir(cfg: dict) -> Path | None:
    root = Path(cfg.get("emu_root") or "")
    for d in (root / "RetroArch" / "database" / "rdb", root / "retroarch" / "database" / "rdb"):
        if d.is_dir():
            return d
    return None


def rdb_index(cfg: dict, es: str) -> dict:
    """{'by_name': {nazwa: rekord}, 'by_rom': {plik: rekord}, 'names': [...]}"""
    sysname = systems.info(es)["libretro"]
    with _rdb_lock:
        if sysname in _rdb_mem:
            return _rdb_mem[sysname]
        cache = paths.DATA / "rdbcache" / (re.sub(r"[^\w-]+", "_", sysname) + ".json")
        d = _rdb_dir(cfg)
        src = d / f"{sysname}.rdb" if d else None
        rows = None
        try:
            if src and src.is_file() and cache.stat().st_mtime >= src.stat().st_mtime:
                rows = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if rows is None:
            rows = []
            if src and src.is_file():
                try:
                    rows = parse_rdb(src)
                    cache.parent.mkdir(parents=True, exist_ok=True)
                    cache.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    log.exception("rdb %s", src)
        idx = {"by_name": {}, "by_rom": {}}
        for r in rows:
            if r.get("name"):
                idx["by_name"].setdefault(r["name"], r)
            if r.get("rom_name"):
                idx["by_rom"].setdefault(r["rom_name"].lower(), r)
        idx["names"] = list(idx["by_name"])
        # indeks po kluczu tytułu (bez tagów) i jego początku — dopasowanie przybliżone
        # przegląda tylko kilka nazw zamiast całej bazy (FBNeo/PSX: ~10 tys.)
        by_key: dict = {}
        for n in idx["names"]:
            by_key.setdefault(art_sources.base_key(n), []).append(n)
        by_pre: dict = {}
        for k, names in by_key.items():
            by_pre.setdefault(k[:6], []).extend(names)
        idx["by_key"], idx["by_pre"] = by_key, by_pre
        _rdb_mem[sysname] = idx
        return idx


def rdb_lookup(cfg: dict, game: dict) -> dict | None:
    idx = rdb_index(cfg, game["es"])
    if not idx["by_name"] and not idx["by_rom"]:
        return None
    rel = Path(game["rel"]).name.lower()
    hit = idx["by_rom"].get(rel) or idx["by_name"].get(game["name"]) \
        or idx["by_name"].get(game["name"] + " (Disc 1)")
    if not hit:
        key = art_sources.base_key(game["name"])
        pool = idx["by_key"].get(key) or idx["by_pre"].get(key[:6], [])
        best = art_sources.libretro_match(game["name"], pool) if pool else None
        hit = idx["by_name"].get(best) if best else None
    return hit


def from_rdb(rec: dict) -> dict:
    out = {}
    for src, dst in (("developer", "developer"), ("publisher", "publisher"),
                     ("genre", "genre"), ("releaseyear", "year"), ("users", "players")):
        if rec.get(src) not in (None, ""):
            out[dst] = str(rec[src])
    if rec.get("serial"):
        out["serial"] = str(rec["serial"])
    return out


# ── IGDB ──

def from_igdb(igdb: art_sources.Igdb, title: str, plat: str) -> dict:
    pid = art_sources.IGDB_PLATFORMS.get(plat)
    esc = title.replace('"', '\\"')
    where = f" where platforms = ({pid});" if pid else ""
    games = igdb._query(
        f'search "{esc}"; fields name,summary,storyline,first_release_date,genres.name,'
        f'involved_companies.company.name,involved_companies.developer,'
        f'involved_companies.publisher,game_modes.name;{where} limit 10;')
    games = [g for g in games if art_sources.similarity(title, g.get("name", "")) >= art_sources.MATCH_MIN]
    if not games:
        return {}
    g = max(games, key=lambda g: art_sources.similarity(title, g.get("name", "")))
    out = {}
    text = g.get("summary") or ""
    if g.get("storyline") and g["storyline"] not in text:
        text = (text + "\n\n" + g["storyline"]).strip()
    if text:
        out["description"] = text
    if g.get("first_release_date"):
        out["year"] = time.strftime("%Y", time.gmtime(g["first_release_date"]))
    comps = g.get("involved_companies") or []
    dev = [c["company"]["name"] for c in comps if c.get("developer") and c.get("company")]
    pub = [c["company"]["name"] for c in comps if c.get("publisher") and c.get("company")]
    if dev:
        out["developer"] = ", ".join(dev[:2])
    if pub:
        out["publisher"] = ", ".join(pub[:2])
    if g.get("genres"):
        out["genre"] = ", ".join(x["name"] for x in g["genres"][:3])
    return out


# ── Wikipedia ──

_WIKI_HINT = {"pl": "gra", "en": "video game"}


def from_wikipedia(title: str) -> dict:
    for lang in ("pl", "en"):
        q = urllib.parse.urlencode({"action": "query", "list": "search", "format": "json",
                                    "srlimit": 3, "srsearch": f"{title} {_WIKI_HINT[lang]}"})
        raw = art_sources.fetch(f"https://{lang}.wikipedia.org/w/api.php?{q}", timeout=12)
        try:
            hits = json.loads(raw or b"{}").get("query", {}).get("search", [])
        except ValueError:
            continue
        for h in hits:
            page = h.get("title", "")
            clean = re.sub(r"\s*\(.*?\)\s*$", "", page)
            if art_sources.similarity(title, clean) < 0.75:
                continue
            raw = art_sources.fetch(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                                    + urllib.parse.quote(page.replace(" ", "_")), timeout=12)
            try:
                s = json.loads(raw or b"{}")
            except ValueError:
                continue
            if s.get("type") == "disambiguation" or not s.get("extract"):
                continue
            return {"wiki": s["extract"], "wiki_lang": lang,
                    "wiki_url": s.get("content_urls", {}).get("desktop", {}).get("page", "")}
    return {}


# ── baza ──

def get(game_id: int) -> dict:
    r = library.db().execute("SELECT * FROM game_meta WHERE game_id=?", (game_id,)).fetchone()
    if not r:
        return {}
    auto = json.loads(r["data"] or "{}")
    edits = json.loads(r["edits"] or "{}")
    out = {**auto, **{k: v for k, v in edits.items() if v not in (None,)}}
    out["_auto"], out["_edits"] = auto, edits
    out["_online"] = bool(r["online"])
    return out


def _store(game_id: int, data: dict | None = None, edits: dict | None = None,
           online: bool | None = None) -> None:
    with library.db() as c:
        cur = c.execute("SELECT data, edits, online FROM game_meta WHERE game_id=?", (game_id,)).fetchone()
        d = json.loads(cur["data"]) if cur else {}
        e = json.loads(cur["edits"]) if cur else {}
        o = cur["online"] if cur else 0
        if data:
            d.update({k: v for k, v in data.items() if v not in (None, "")})
        if edits is not None:
            e = edits
        if online is not None:
            o = 1 if online else 0
        c.execute("INSERT OR REPLACE INTO game_meta(game_id, data, edits, online, updated) VALUES(?,?,?,?,?)",
                  (game_id, json.dumps(d, ensure_ascii=False), json.dumps(e, ensure_ascii=False),
                   o, time.time()))


def set_edits(game_id: int, edits: dict) -> None:
    clean = {k: str(v).strip() for k, v in edits.items() if k in FIELDS + ("title",)
             and v is not None and str(v).strip() != ""}
    _store(game_id, edits=clean)


def ensure_local(cfg: dict, game: dict) -> dict:
    """Dane z rdb (offline, natychmiast) — przy pierwszym podglądzie gry."""
    m = get(game["id"])
    if "_auto" in m and m["_auto"].get("_rdb_checked"):
        return m
    rec = rdb_lookup(cfg, game)
    _store(game["id"], {**(from_rdb(rec) if rec else {}), "_rdb_checked": 1})
    return get(game["id"])


def prepare_system(cfg: dict, es: str) -> int:
    """Dane z rdb dla wszystkich gier systemu, które ich jeszcze nie mają —
    jedna transakcja (filtrowanie listy gier potrzebuje metadanych od razu).
    Zwraca liczbę uzupełnionych gier."""
    con = library.db()
    rows = con.execute("""SELECT g.id, g.es, g.name, g.rel, m.data, m.edits, m.online
                          FROM games g LEFT JOIN game_meta m ON m.game_id=g.id
                          WHERE g.es=? AND g.hidden=0""", (es,)).fetchall()
    todo = []
    now = time.time()
    for r in rows:
        data = json.loads(r["data"] or "{}") if r["data"] else {}
        if data.get("_rdb_checked"):
            continue
        rec = rdb_lookup(cfg, {"es": r["es"], "name": r["name"], "rel": r["rel"]})
        if rec:
            for k, v in from_rdb(rec).items():
                data.setdefault(k, v)
        data["_rdb_checked"] = 1
        todo.append((r["id"], json.dumps(data, ensure_ascii=False), r["edits"] or "{}",
                     r["online"] or 0, now))
    if todo:
        with con:
            con.executemany("INSERT OR REPLACE INTO game_meta(game_id, data, edits, online, updated) "
                            "VALUES(?,?,?,?,?)", todo)
    return len(todo)


FIELDS_FILTER = ("genre", "year", "players", "developer", "publisher", "title")


def system_fields(es: str) -> dict:
    """{game_id: {genre, year, players, developer, publisher}} — ręczne zmiany wygrywają.
    Arcade: podstawą są dane z MAME (rok, producent, gracze) i catver.ini (gatunek)."""
    con = library.db()
    out = {}
    if systems.info(es)["kind"] == "arcade":
        from emustart import arcade
        arcade._ensure_columns()
        for r in con.execute("""SELECT g.id, a.year, a.maker, a.players, a.genre
                                FROM games g JOIN arcade_sets a
                                  ON a.name = substr(g.rel, 1, length(g.rel) - 4)
                                WHERE g.es=?""", (es,)):
            out[r["id"]] = {"genre": r["genre"], "year": r["year"], "players": r["players"],
                            "developer": r["maker"], "publisher": r["maker"], "title": ""}
    for r in con.execute("""SELECT m.game_id, m.data, m.edits FROM game_meta m
                            JOIN games g ON g.id=m.game_id WHERE g.es=?""", (es,)):
        base = out.get(r["game_id"], {})
        d = {**base, **{k: v for k, v in json.loads(r["data"] or "{}").items() if v},
             **{k: v for k, v in json.loads(r["edits"] or "{}").items() if v}}
        out[r["game_id"]] = {k: d.get(k, "") for k in FIELDS_FILTER}
    return out


def fetch_online(cfg: dict, game: dict, igdb: art_sources.Igdb | None = None,
                 tgdb: bool = False) -> dict:
    """Opis z IGDB i Wikipedii (sieć). TheGamesDB tylko na żądanie (`tgdb`) —
    ma miesięczny limit zapytań, a podgląd pobiera opisy przy samym przeglądaniu.
    Zapisuje i zwraca scalone metadane."""
    ensure_local(cfg, game)
    title = art_sources.plain_title(game["name"])
    plat = systems.info(game["es"])["plat"]
    if igdb is None:
        k = art_sources.keys(cfg)
        igdb = art_sources.Igdb(k.get("igdb_client_id", ""), k.get("igdb_client_secret", ""))
    got = {}
    if igdb.cid and igdb.secret:
        got = from_igdb(igdb, title, plat)
        local = get(game["id"]).get("_auto", {})
        # rdb (No-Intro/Redump) jest pewniejszy niż wyszukiwanie po nazwie — IGDB
        # tylko uzupełnia brakujące pola, opis bierzemy zawsze
        got = {k: v for k, v in got.items() if k == "description" or not local.get(k)}
    if tgdb and not got.get("description"):
        tg = art_sources.Tgdb(art_sources.keys(cfg).get("tgdb_key", ""))
        g = tg.game(title, plat, "overview,players")
        if g and g.get("overview"):
            got["description"] = g["overview"]
        if g and g.get("release_date") and not get(game["id"]).get("year"):
            got["year"] = g["release_date"][:4]
    got.update(from_wikipedia(title))
    _store(game["id"], got, online=True)
    return get(game["id"])
