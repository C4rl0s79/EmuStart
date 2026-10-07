"""library — baza SQLite: systemy, gry, profile, historia gry, cache, sety arcade.

Jedno połączenie na wątek (sqlite3 nie lubi współdzielenia), WAL, żeby UI czytało
w trakcie skanu albo kopiowania.
"""

from __future__ import annotations

import json
import sqlite3
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
    hidden    INTEGER NOT NULL DEFAULT 0, -- arcade: BIOS/urządzenie
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
CREATE TABLE IF NOT EXISTS arcade_sets (
    name     TEXT PRIMARY KEY,
    title    TEXT NOT NULL,
    parent   TEXT NOT NULL DEFAULT '',
    romof    TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT ''
);
"""

_local = threading.local()


def db() -> sqlite3.Connection:
    con = getattr(_local, "con", None)
    if con is None:
        paths.DATA.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(paths.DB_PATH), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(_SCHEMA)
        if not con.execute("SELECT 1 FROM profiles").fetchone():
            con.execute("INSERT INTO profiles(name) VALUES('Gracz')")
            con.commit()
        _local.con = con
    return con


def meta_get(key: str, default: str = "") -> str:
    r = db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(key: str, value: str) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, value))


# ── gry ──

def game(game_id: int) -> dict | None:
    r = db().execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not r:
        return None
    g = dict(r)
    g["files"] = json.loads(g["files"])
    return g


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
               g.art_box, g.art_snap,
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


def set_art(game_id: int, kind: str, state: int) -> None:
    col = "art_box" if kind == "box" else "art_snap"
    with db() as c:
        c.execute(f"UPDATE games SET {col}=? WHERE id=?", (state, game_id))


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


def set_resume(profile_id: int, game_id: int, family: str, path: str) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO resume VALUES(?,?,?,?,?)",
                  (profile_id, game_id, family, path, time.time()))


def clear_resume(profile_id: int, game_id: int) -> None:
    with db() as c:
        c.execute("DELETE FROM resume WHERE profile_id=? AND game_id=?", (profile_id, game_id))
