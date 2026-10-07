"""launchbox — baza LaunchBox Games DB: metadane, opisy i grafiki (offline).

Na podstawie PyLinksWeb (services/launchbox.py). LaunchBox udostępnia całą bazę
jednym plikiem `Metadata.zip` (~108 MB): ok. 189 tys. gier z opisem, datą,
producentem, wydawcą, gatunkami i liczbą graczy oraz ~1,3 mln grafik w
kategoriach (okładka, zrzut, Clear Logo…) pod images.launchbox-app.com.
`Mame.xml` tłumaczy nazwy setów arcade na tytuły.

W odróżnieniu od PyLinks zapisujemy też metadane gier (tabela games), bo EmuStart
używa ich do opisów i filtrów. Baza: data/launchbox.db, budowana raz; potem
wszystko działa bez sieci (sieć tylko po wybraną grafikę).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from emustart import art_sources, paths, systems

log = logging.getLogger("emustart.launchbox")

METADATA_URL = "https://gamesdb.launchbox-app.com/Metadata.zip"
IMAGE_BASE = "https://images.launchbox-app.com/"
UA = "EmuStart/0.9 (+https://github.com/C4rl0s79/EmuStart)"

# system (folder ES) → platforma w LaunchBoksie
PLATFORMS = {
    "psx": "Sony Playstation", "ps2": "Sony Playstation 2", "ps3": "Sony Playstation 3",
    "psp": "Sony PSP", "psvita": "Sony Playstation Vita",
    "nes": "Nintendo Entertainment System", "fds": "Nintendo Famicom Disk System",
    "snes": "Super Nintendo Entertainment System", "SNESMSU1": "Super Nintendo Entertainment System",
    "satellaview": "Nintendo Satellaview", "sufami": "Super Nintendo Entertainment System",
    "n64": "Nintendo 64", "n64dd": "Nintendo 64DD", "gb": "Nintendo Game Boy",
    "gbc": "Nintendo Game Boy Color", "gba": "Nintendo Game Boy Advance", "nds": "Nintendo DS",
    "gameandwatch": "Nintendo Game & Watch", "gamecube": "Nintendo GameCube", "wii": "Nintendo Wii",
    "virtualboy": "Nintendo Virtual Boy", "pokemini": "Nintendo Pokemon Mini",
    "mastersystem": "Sega Master System", "megadrive": "Sega Genesis", "gamegear": "Sega Game Gear",
    "sega32x": "Sega 32X", "segacd": "Sega CD", "saturn": "Sega Saturn", "dreamcast": "Sega Dreamcast",
    "pico": "Sega Pico", "pcengine": "NEC TurboGrafx-16", "tg16": "NEC TurboGrafx-16",
    "supergrafx": "PC Engine SuperGrafx", "pc88": "NEC PC-8801", "pc98": "NEC PC-9801",
    "atari2600": "Atari 2600", "atari5200": "Atari 5200", "atari7800": "Atari 7800",
    "atarijaguar": "Atari Jaguar", "lynx": "Atari Lynx", "atari800": "Atari 800", "atarist": "Atari ST",
    "3do": "3DO Interactive Multiplayer", "amiga": "Commodore Amiga", "c64": "Commodore 64",
    "vic20": "Commodore VIC-20", "plus4": "Commodore Plus 4", "msx": "Microsoft MSX",
    "msx2": "Microsoft MSX2", "ngp": "SNK Neo Geo Pocket", "ngpc": "SNK Neo Geo Pocket Color",
    "wswan": "WonderSwan", "wswanc": "WonderSwan Color", "odyssey2": "Magnavox Odyssey 2",
    "zxspectrum": "Sinclair ZX Spectrum", "x360": "Microsoft Xbox 360", "xbox360": "Microsoft Xbox 360",
    "colecovision": "ColecoVision", "intellivision": "Mattel Intellivision", "vectrex": "GCE Vectrex",
    "channelf": "Fairchild Channel F", "supervision": "Watara Supervision", "megaduck": "Mega Duck",
    "arcadia": "Emerson Arcadia 2001", "scv": "Epoch Super Cassette Vision",
    "gamecom": "Tiger Game.com", "gamepock": "Epoch Game Pocket Computer",
    "fbneo": "Arcade", "mame": "Arcade", "arcade": "Arcade", "neogeo": "SNK Neo Geo AES",
}

# rodzaj grafiki EmuStart → kategorie LaunchBoksa, od najlepszej
KINDS = {
    "box": ["Box - Front", "Box - Front - Reconstructed", "Fanart - Box - Front", "Box - 3D"],
    "snap": ["Screenshot - Gameplay", "Screenshot - Game Title", "Screenshot"],
    "logo": ["Clear Logo"],
}
REGION_ORDER = ["", "World", "North America", "United States", "Europe", "United Kingdom",
                "Germany", "France", "Japan"]

_NORM_RE = re.compile(r"[^a-z0-9]+")
_TAG_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
_ART_RE = re.compile(r"^(.*?),\s+(the|a|an)(?![a-z])(.*)$")
_LEAD_ART_RE = re.compile(r"^(the|an|a)\s+")


def norm_title(name: str) -> str:
    """Klucz dopasowania (z PyLinks): bez tagów, interpunkcji i przedimka.
    „Legend of Zelda, The - Ocarina of Time” = „The Legend of Zelda: Ocarina of Time”."""
    s = (name or "").lower()
    s = _TAG_RE.sub(" ", s)
    m = _ART_RE.match(s)
    if m:
        s = f"{m.group(2)} {m.group(1)} {m.group(3)}"
    s = s.replace("&", " and ")
    s = _LEAD_ART_RE.sub("", s.strip())
    return _NORM_RE.sub("", s)


def db_path() -> Path:
    return paths.DATA / "launchbox.db"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY, name TEXT, platform TEXT,
    year TEXT, overview TEXT, developer TEXT, publisher TEXT, genres TEXT,
    players TEXT, rating TEXT);
CREATE TABLE IF NOT EXISTS names(norm TEXT, gid INTEGER, platform TEXT);
CREATE TABLE IF NOT EXISTS images(gid INTEGER, type TEXT, region TEXT, fn TEXT);
CREATE TABLE IF NOT EXISTS mame(setname TEXT PRIMARY KEY, title TEXT, cloneof TEXT);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def status() -> dict:
    p = db_path()
    if not p.is_file():
        return {"ready": False}
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        meta = dict(con.execute("SELECT k, v FROM meta").fetchall())
        con.close()
    except sqlite3.Error:
        return {"ready": False}
    return {"ready": bool(meta.get("updated")), "updated": meta.get("updated", ""),
            "games": int(meta.get("games", 0) or 0), "images": int(meta.get("images", 0) or 0),
            "size_mb": round(p.stat().st_size / (1 << 20), 1)}


def ready() -> bool:
    return status().get("ready", False)


class Updater:
    """Pobranie Metadata.zip i budowa bazy w tle. UI odpytuje status()."""

    def __init__(self):
        self.cancel = threading.Event()
        self.text = ""
        self.done = self.total = 0
        self.error = ""
        self.finished = False
        self.result: dict = {}
        self.thread = threading.Thread(target=self._run, daemon=True, name="launchbox")

    def start(self) -> None:
        self.thread.start()

    def status(self) -> dict:
        return {"running": not self.finished, "text": self.text, "done": self.done,
                "total": self.total, "error": self.error, "result": self.result}

    def _run(self) -> None:
        zp = paths.DATA / "launchbox_metadata.zip"
        try:
            self._download(zp)
            self.text, self.done, self.total = "Buduję bazę (rozbiór 512 MB XML)…", 0, 0
            tmp = db_path().with_suffix(".new")
            tmp.unlink(missing_ok=True)
            self.result = build(zp, tmp, self._progress, self.cancel)
            db_path().unlink(missing_ok=True)
            tmp.replace(db_path())
            self.text = "Gotowe"
        except Exception as ex:
            log.exception("launchbox")
            self.error = str(ex)
        finally:
            for p in (zp, zp.with_suffix(".part")):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
            self.finished = True

    def _progress(self, n: int, text: str) -> None:
        self.done, self.text = n, text

    def _download(self, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(".part")
        req = urllib.request.Request(METADATA_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            self.total = int(r.headers.get("Content-Length") or 0)
            self.done = 0
            with open(part, "wb") as f:
                while True:
                    if self.cancel.is_set():
                        raise InterruptedError("przerwano")
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    self.done += len(chunk)
                    self.text = f"Pobieram bazę LaunchBox: {self.done >> 20} / {self.total >> 20} MB"
        part.replace(dest)


def build(zip_path: Path, out: Path, progress=None, cancel=None) -> dict:
    """Rozbiera Metadata.zip do SQLite (gry z metadanymi, nazwy, grafiki, sety MAME)."""
    from xml.etree.ElementTree import iterparse
    out.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(out))
    con.executescript(_SCHEMA)
    n_games = n_img = n_alt = 0
    games, names, imgs = [], [], []
    plat_of: dict = {}

    def flush():
        nonlocal games, names, imgs
        con.executemany("INSERT OR REPLACE INTO games VALUES(?,?,?,?,?,?,?,?,?,?)", games)
        con.executemany("INSERT INTO names VALUES(?,?,?)", names)
        con.executemany("INSERT INTO images VALUES(?,?,?,?)", imgs)
        games, names, imgs = [], [], []

    try:
        with zipfile.ZipFile(zip_path) as z:
            with z.open("Metadata.xml") as f:
                for _ev, el in iterparse(f, events=("end",)):
                    tag = el.tag
                    if tag == "Game":
                        gid, name = el.findtext("DatabaseID"), el.findtext("Name") or ""
                        plat = el.findtext("Platform") or ""
                        if gid and name:
                            gid = int(gid)
                            plat_of[gid] = plat
                            year = el.findtext("ReleaseYear") or (el.findtext("ReleaseDate") or "")[:4]
                            games.append((gid, name, plat, year, el.findtext("Overview") or "",
                                          el.findtext("Developer") or "", el.findtext("Publisher") or "",
                                          el.findtext("Genres") or "", el.findtext("MaxPlayers") or "",
                                          el.findtext("CommunityRating") or ""))
                            names.append((norm_title(name), gid, plat))
                            n_games += 1
                        el.clear()
                    elif tag == "GameImage":
                        gid, fn = el.findtext("DatabaseID"), el.findtext("FileName")
                        if gid and fn:
                            imgs.append((int(gid), el.findtext("Type") or "",
                                         el.findtext("Region") or "", fn))
                            n_img += 1
                        el.clear()
                    elif tag == "GameAlternateName":
                        gid, nm = el.findtext("DatabaseID"), el.findtext("AlternateName")
                        if gid and nm:
                            names.append((norm_title(nm), int(gid), plat_of.get(int(gid), "")))
                            n_alt += 1
                        el.clear()
                    else:
                        continue
                    if len(games) >= 5000 or len(imgs) >= 50000:
                        flush()
                        if progress:
                            progress(n_games, f"Buduję bazę: {n_games} gier, {n_img} grafik…")
                        if cancel is not None and cancel.is_set():
                            raise InterruptedError("przerwano")
            flush()
            rows = []
            with z.open("Mame.xml") as f:
                for _ev, el in iterparse(f, events=("end",)):
                    if el.tag == "MameFile":
                        st = el.findtext("FileName")
                        if st:
                            rows.append((st.lower(), el.findtext("Name") or "",
                                         (el.findtext("CloneOf") or "").lower()))
                        el.clear()
            con.executemany("INSERT OR REPLACE INTO mame VALUES(?,?,?)", rows)
        con.executescript("""
            CREATE INDEX IF NOT EXISTS ix_names ON names(platform, norm);
            CREATE INDEX IF NOT EXISTS ix_names_n ON names(norm);
            CREATE INDEX IF NOT EXISTS ix_images ON images(gid);""")
        con.executemany("INSERT OR REPLACE INTO meta VALUES(?,?)", [
            ("updated", time.strftime("%Y-%m-%d %H:%M")), ("games", str(n_games)),
            ("images", str(n_img)), ("alts", str(n_alt))])
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    return {"games": n_games, "images": n_img, "alts": n_alt}


# ── zapytania ──

_local = threading.local()


def _con():
    con = getattr(_local, "con", None)
    if con is None or getattr(_local, "path", None) != db_path():
        con = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        _local.con, _local.path = con, db_path()
    return con


def find_game(es: str, name: str, setname: str = "") -> dict | None:
    """Gra LaunchBoksa dla gry EmuStart (platforma + tytuł; arcade po nazwie setu)."""
    if not ready():
        return None
    con = _con()
    plat = PLATFORMS.get(es) or PLATFORMS.get(es.lower(), "")
    titles = []
    if setname:
        r = con.execute("SELECT title, cloneof FROM mame WHERE setname=?", (setname.lower(),)).fetchone()
        if r and r["title"]:
            titles.append(r["title"])
        if r and r["cloneof"]:
            p = con.execute("SELECT title FROM mame WHERE setname=?", (r["cloneof"],)).fetchone()
            if p and p["title"]:
                titles.append(p["title"])
    titles.append(name)
    for t in titles:
        n = norm_title(t)
        if not n:
            continue
        gids = [r["gid"] for r in con.execute(
            "SELECT DISTINCT gid FROM names WHERE platform=? AND norm=?", (plat, n))] if plat else []
        if not gids and plat:
            # „Final Fantasy VII (USA) (Disc 1)” / drobne różnice zapisu — ten sam początek klucza
            gids = [r["gid"] for r in con.execute(
                "SELECT DISTINCT gid FROM names WHERE platform=? AND norm LIKE ? LIMIT 20",
                (plat, n[:12] + "%"))] if len(n) >= 12 else []
        if not gids:
            continue
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM games WHERE id IN (%s)" % ",".join("?" * len(gids)), gids)]
        best = max(rows, key=lambda g: (art_sources.similarity(art_sources.plain_title(t), g["name"]),
                                        bool(g["overview"])))
        if art_sources.similarity(art_sources.plain_title(t), best["name"]) >= 0.6 or len(rows) == 1:
            return best
    return None


def metadata_of(g: dict) -> dict:
    """Pola EmuStart z rekordu gry LaunchBoksa."""
    out = {}
    if g.get("overview"):
        out["description"] = g["overview"].strip()
    if g.get("year"):
        out["year"] = g["year"][:4]
    for src, dst in (("developer", "developer"), ("publisher", "publisher"), ("players", "players")):
        if g.get(src):
            out[dst] = g[src]
    if g.get("genres"):
        out["genre"] = ", ".join(x.strip() for x in g["genres"].split(";") if x.strip())
    out["lb_id"] = g["id"]
    return out


def images(gid: int, kind: str | None = None) -> list:
    """[{url, type, region}] — dla `kind` tylko kategorie tego rodzaju, od najlepszej."""
    rows = [dict(r) for r in _con().execute("SELECT type, region, fn FROM images WHERE gid=?", (gid,))]
    order = KINDS.get(kind) if kind else None
    if order:
        rows = [r for r in rows if r["type"] in order]
        rows.sort(key=lambda r: (order.index(r["type"]),
                                 REGION_ORDER.index(r["region"]) if r["region"] in REGION_ORDER else 99))
    return [{"url": IMAGE_BASE + r["fn"], "type": r["type"], "region": r["region"]} for r in rows]
