"""library — baza SQLite: systemy, gry, profile, historia gry, cache, sety arcade.

Jedno połączenie na wątek (sqlite3 nie lubi współdzielenia), WAL, żeby UI czytało
w trakcie skanu albo kopiowania.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
import threading
import time

from emustart import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS systems (
    es        TEXT PRIMARY KEY,
    display   TEXT NOT NULL,
    rom_dir   TEXT NOT NULL,
    games     INTEGER NOT NULL DEFAULT 0,
    scanned   REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS games (
    id        INTEGER PRIMARY KEY,
    es        TEXT NOT NULL,
    rel       TEXT NOT NULL,             -- ścieżka głównego pliku względem rom_dir
    name      TEXT NOT NULL,             -- pełna nazwa (z tagami) — klucz miniatur
    title     TEXT NOT NULL,             -- nazwa do wyświetlenia (bez tagów)
    tags      TEXT NOT NULL DEFAULT '',  -- „(USA) (Disc 1)” itp.
    files     TEXT NOT NULL,             -- JSON [[rel, size, mtime], …] wszystkich plików gry
    size      INTEGER NOT NULL DEFAULT 0,
    is_dir    INTEGER NOT NULL DEFAULT 0,
    multidisc INTEGER NOT NULL DEFAULT 0, -- kilka płyt bez .m3u
    parent    TEXT NOT NULL DEFAULT '',  -- arcade: set rodzica (klon)
    hidden    INTEGER NOT NULL DEFAULT 0, -- 1 arcade: BIOS/urządzenie, 2 tłumaczenie pod oryginałem
    seen      REAL NOT NULL DEFAULT 0,
    art_box   INTEGER NOT NULL DEFAULT 0, -- 0 nie sprawdzano, 1 jest, 2 brak
    art_snap  INTEGER NOT NULL DEFAULT 0,
    UNIQUE(es, rel)
);
CREATE INDEX IF NOT EXISTS idx_games_es ON games(es, title);
CREATE TABLE IF NOT EXISTS profiles (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS play (
    profile_id INTEGER NOT NULL,
    game_id    INTEGER NOT NULL,
    plays      INTEGER NOT NULL DEFAULT 0,
    seconds    INTEGER NOT NULL DEFAULT 0,
    last       REAL NOT NULL DEFAULT 0,
    favorite   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(profile_id, game_id)
);
CREATE TABLE IF NOT EXISTS cache (
    game_id   INTEGER PRIMARY KEY,
    complete  INTEGER NOT NULL DEFAULT 0,
    size      INTEGER NOT NULL DEFAULT 0,
    last_used REAL NOT NULL DEFAULT 0,
    pinned    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resume (
    profile_id INTEGER NOT NULL,
    game_id    INTEGER NOT NULL,
    family     TEXT NOT NULL,           -- rodzina emulatora, który zapisał stan
    path       TEXT NOT NULL DEFAULT '', -- kopia stanu ('' = autozapis RetroArcha)
    created    REAL NOT NULL,
    PRIMARY KEY(profile_id, game_id)
);
CREATE TABLE IF NOT EXISTS game_meta (
    game_id INTEGER PRIMARY KEY,
    data    TEXT NOT NULL DEFAULT '{}',   -- pobrane (rdb / IGDB / Wikipedia)
    edits   TEXT NOT NULL DEFAULT '{}',   -- ręczne zmiany użytkownika (wygrywają)
    online  INTEGER NOT NULL DEFAULT 0,   -- 1 = pobrano już opisy z sieci
    updated REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS game_chg (     -- serwer: ostatnia zmiana grafiki / opisu gry
    game_id INTEGER PRIMARY KEY,
    t       REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS game_emu (     -- emulator wybrany dla jednej gry
    game_id INTEGER PRIMARY KEY,
    label   TEXT NOT NULL,
    exe     TEXT NOT NULL,
    args    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS states (       -- stany zapisu poznane w sesjach EmuStart
    profile_id INTEGER NOT NULL,
    game_id    INTEGER NOT NULL,
    family     TEXT NOT NULL,
    prefix     TEXT NOT NULL,             -- wspólny początek nazw plików stanów tej gry
    PRIMARY KEY(profile_id, game_id, family)
);
CREATE TABLE IF NOT EXISTS arcade_sets (
    name     TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    parent   TEXT NOT NULL DEFAULT '',
    romof    TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT ''
);
"""

_local = threading.local()


_migrate_lock = threading.Lock()


def add_columns(con: sqlite3.Connection, table: str, cols: list, after=None) -> bool:
    """Brakujące kolumny tabeli (migracja bazy) — raz, także gdy bazę otwiera naraz kilka
    wątków albo procesów (serwer i panel): blokada w procesie + transakcja IMMEDIATE, w której
    kolumny sprawdzane są jeszcze raz. Wcześniej dwa wątki widziały brak kolumny i oba ją
    dodawały („duplicate column name”). `cols` — [(nazwa, definicja)]; `after(con)` — dodatkowa
    zmiana w tej samej transakcji, gdy coś dodano. True = coś dodano."""
    with _migrate_lock:
        if all(c in {r[1] for r in con.execute(f"PRAGMA table_info({table})")} for c, _d in cols):
            return False
        if con.in_transaction:
            con.commit()
        con.execute("BEGIN IMMEDIATE")
        try:
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            missing = [(c, d) for c, d in cols if c not in have]
            for c, d in missing:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {d}")
            if missing and after:
                after(con)
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        return bool(missing)


def db() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None:
        paths.DATA.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(paths.DB_PATH), timeout=30)
        con.row_factory = sqlite3.Row
        with _migrate_lock:              # tryb WAL i schemat przy pierwszym otwarciu — po jednym wątku
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.executescript(_SCHEMA)
        add_columns(con, "games", [
            ("src", "TEXT NOT NULL DEFAULT ''"),          # kilka folderów z grami
            ("art_logo", "INTEGER NOT NULL DEFAULT 0"),   # logo gry (Clear Logo) — od 0.9.0
            ("trans", "TEXT NOT NULL DEFAULT ''"),        # tłumaczenia jako wersje gier — od 0.30.0
            ("tbase", "TEXT NOT NULL DEFAULT ''"),
            ("art_m", "TEXT NOT NULL DEFAULT ''")])         # PC: czasy grafik na serwerze (JSON)
        with _migrate_lock:
            con.execute("BEGIN IMMEDIATE")
            if not con.execute("SELECT 1 FROM profiles").fetchone():
                con.execute("INSERT INTO profiles(name) VALUES('Gracz')")
            con.execute("COMMIT")
        _local.con = con
    return con


def meta_get(key: str, default: str = "") -> str:
    r = db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(key: str, value: str) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))


def bump_rev(es: str, full: bool = True) -> None:
    """Zmiana grafiki albo opisu w systemie (serwer): urządzenia pobiorą ten system od nowa —
    całą listę (full) albo tylko gry zmienione od ich ostatniej synchronizacji (game_chg)."""
    if es:
        now = repr(time.time())
        meta_set(f"rev:{es}", now)
        if full:
            meta_set(f"full:{es}", now)


def bump_rev_game(game_id: int) -> None:
    """Zmiana jednej gry: urządzenia pobiorą tylko ją (nie cały system)."""
    r = db().execute("SELECT es FROM games WHERE id=?", (game_id,)).fetchone()
    if r:
        with db() as c:
            c.execute("INSERT OR REPLACE INTO game_chg(game_id, t) VALUES(?,?)", (game_id, time.time()))
        bump_rev(r["es"], full=False)


def rev(es: str) -> float:
    try:
        return float(meta_get(f"rev:{es}", "0") or 0)
    except ValueError:
        return 0.0


def full_rev(es: str) -> float:
    try:
        return float(meta_get(f"full:{es}", "0") or 0)
    except ValueError:
        return 0.0


# ── gry ──

def game(game_id: int) -> dict | None:
    r = db().execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not r:
        return None
    g = dict(r)
    g["files"] = json.loads(g["files"])
    return g


def game_emu(g: dict):
    """Emulator wybrany dla tej gry; tłumaczenie bez własnego bierze wybór oryginału."""
    con = db()
    own = con.execute("SELECT * FROM game_emu WHERE game_id=?", (g["id"],)).fetchone()
    if own or not g.get("trans"):
        return own
    return con.execute("""SELECT e.* FROM game_emu e JOIN games o ON o.id=e.game_id
                          WHERE o.es=? AND o.tbase=? AND o.trans='' LIMIT 1""",
                       (g["es"], g.get("tbase", ""))).fetchone()


def system_dirs(es: str) -> list:
    """Foldery systemu (z kilku folderów z grami), w kolejności pierwszeństwa."""
    r = system_row(es)
    if not r:
        return []
    raw = r["rom_dir"]
    try:
        dirs = json.loads(raw) if raw.startswith("[") else [raw]
    except ValueError:
        dirs = [raw]
    return [Path(d) for d in dirs if d]


def game_dir(g: dict) -> Path:
    """Folder, względem którego liczy się `rel` gry."""
    if g.get("src"):
        return Path(g["src"])
    dirs = system_dirs(g["es"])
    return dirs[0] if dirs else Path(".")


def system_row(es: str) -> dict | None:
    r = db().execute("SELECT * FROM systems WHERE es=?", (es,)).fetchone()
    return dict(r) if r else None


def systems_summary() -> list:
    rows = db().execute("""
        SELECT s.es, s.display, s.rom_dir, s.scanned,
               (SELECT COUNT(*) FROM games g WHERE g.es=s.es AND g.hidden=0) AS games,
               (SELECT COUNT(*) FROM games g JOIN cache c ON c.game_id=g.id
                 WHERE g.es=s.es AND c.complete=1) AS cached
        FROM systems s ORDER BY s.display COLLATE NOCASE""").fetchall()
    return [dict(r) for r in rows]


def list_games(es: str, profile_id: int, hide_clones: bool) -> list:
    q = """
        SELECT g.id, g.name, g.title, g.tags, g.size, g.is_dir, g.parent,
               g.art_box, g.art_snap, g.art_logo,
               COALESCE(c.complete,0) AS cached, COALESCE(c.pinned,0) AS pinned,
               COALESCE(p.last,0) AS last, COALESCE(p.seconds,0) AS seconds,
               COALESCE(p.favorite,0) AS favorite
        FROM games g
        LEFT JOIN cache c ON c.game_id=g.id
        LEFT JOIN play p ON p.game_id=g.id AND p.profile_id=?
        WHERE g.es=? AND g.hidden=0 {clones}
        ORDER BY g.title COLLATE NOCASE, g.name COLLATE NOCASE"""
    q = q.format(clones="AND g.parent=''" if hide_clones else "")
    return [dict(r) for r in db().execute(q, (profile_id, es)).fetchall()]


# ── ukrywanie wersji wg oznaczeń w nazwie (No-Intro / Redump / MAME) ──
# Grupa pasuje, gdy któraś część nawiasu „(Beta 2, Proto)” / „[BIOS]” zaczyna się
# od wzorca (pirate/hack/bootleg — także w środku, np. „Joystick hack bootleg”).
HIDE_GROUPS = {
    "beta": re.compile(r"^(beta|alpha|proto(type)?|possible proto|pre-?production|pre-?release|debug|"
                       r"location test|test version|work in progress|wip)\b", re.I),
    "demo": re.compile(r"^(demo|auto demo|tech demo|rolling demo|playable demo|kiosk|promo|sample|trial|"
                       r"preview|taikenban|not for resale)\b", re.I),
    "pirate": re.compile(r"^pirate\b|\b(hack|bootleg)\b", re.I),
    "unl": re.compile(r"^(unl|unlicensed|aftermarket|homebrew)\b", re.I),
    "program": re.compile(r"^(program|test program|bios|utility|enhancement chip)\b", re.I),
}
_BRACKETS = re.compile(r"[\(\[]([^\)\]]+)[\)\]]")


def tag_groups(name: str) -> set:
    out = set()
    for inner in _BRACKETS.findall(name or ""):
        for part in inner.split(","):
            part = part.strip()
            for grp, rx in HIDE_GROUPS.items():
                if rx.search(part):
                    out.add(grp)
    return out


def is_hidden(name: str, groups) -> bool:
    return bool(groups) and bool(tag_groups(name) & set(groups))


_count_cache: dict = {}


def visible_counts(groups, clone_systems) -> dict:
    """{es: liczba gier} po ukryciu grup oznaczeń (i klonów w systemach arcade).
    Liczone w Pythonie, pamiętane do zmiany biblioteki albo ustawień."""
    groups, clone_systems = tuple(sorted(groups)), tuple(sorted(clone_systems))
    con = db()
    stamp = tuple(con.execute("SELECT COUNT(*), COALESCE(MAX(id),0), COALESCE(SUM(hidden),0) FROM games").fetchone())
    key = (groups, clone_systems, stamp)
    if key in _count_cache:
        return _count_cache[key]
    out: dict = {}
    for r in con.execute("SELECT es, name, parent FROM games WHERE hidden=0"):
        if r["parent"] and r["es"] in clone_systems:
            continue
        if groups and is_hidden(r["name"], groups):
            continue
        out[r["es"]] = out.get(r["es"], 0) + 1
    _count_cache.clear()
    _count_cache[key] = out
    return out


def set_art(game_id: int, kind: str, state: int) -> None:
    col = {"box": "art_box", "snap": "art_snap", "logo": "art_logo"}[kind]
    with db() as c:
        c.execute(f"UPDATE games SET {col}=? WHERE id=?", (state, game_id))
    bump_rev_game(game_id)


# ── historia gry ──

def record_play(profile_id: int, game_id: int, seconds: int) -> None:
    with db() as c:
        c.execute("""INSERT INTO play(profile_id, game_id, plays, seconds, last)
                     VALUES(?,?,1,?,?)
                     ON CONFLICT(profile_id, game_id) DO UPDATE SET
                       plays=plays+1, seconds=seconds+excluded.seconds, last=excluded.last""",
                  (profile_id, game_id, max(0, int(seconds)), time.time()))


def default_profile() -> int:
    return db().execute("SELECT id FROM profiles ORDER BY id LIMIT 1").fetchone()["id"]


# ── stan „wznowienia” (quicksave i wyjdź) ──

def get_resume(profile_id: int, game_id: int) -> dict | None:
    r = db().execute("SELECT * FROM resume WHERE profile_id=? AND game_id=?",
                     (profile_id, game_id)).fetchone()
    return dict(r) if r else None


def set_resume(profile_id: int, game_id: int, family: str, path: str, created: float | None = None) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO resume VALUES(?,?,?,?,?)",
                  (profile_id, game_id, family, path, created or time.time()))


def clear_resume(profile_id: int, game_id: int) -> None:
    with db() as c:
        c.execute("DELETE FROM resume WHERE profile_id=? AND game_id=?", (profile_id, game_id))
