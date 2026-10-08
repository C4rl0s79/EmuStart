"""art_sources — źródła okładek (box) i zrzutów (snap), od najpewniejszego.

1. libretro, dokładna nazwa      — nazwy No-Intro/Redump, trafienie = tożsamość
2. libretro, lista plików        — ta sama gra pod inną wersją nazwy
                                   („(USA) (Rev 1)” → „(USA)”, „, The”, „(Disc 1)”)
3. SteamGridDB (box)             — klucz z PyLinks
4. IGDB (box + snap)             — client id/secret z PyLinks, filtr platformy
5. TheGamesDB (box + snap)       — klucz z PyLinks, filtr platformy

Logika klientów SGDB/IGDB/TGDB pochodzi z PyLinksWeb (services/art.py,
services/extra_art.py). Przy automatycznym pobieraniu nikt nie zatwierdza wyboru,
więc próg podobieństwa nazw jest wyższy niż w PyLinks (0,8 zamiast 0,5).
"""

from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from emustart import paths, systems

log = logging.getLogger("emustart.art_sources")

MATCH_MIN = 0.8
UA = {"User-Agent": "EmuStart/0.16 (+https://github.com/C4rl0s79/EmuStart)"}
INDEX_TTL = 30 * 86400

# platformy IGDB / TheGamesDB dla kodów z systems.py
IGDB_PLATFORMS = {
    "PS1": 7, "PS2": 8, "PS3": 9, "PSP": 38, "PSVITA": 46, "NES": 18, "SNES": 19,
    "SNESMSU1": 19, "N64": 4, "GB": 33, "GBC": 22, "GBA": 24, "NDS": 20, "GCN": 21,
    "WII": 5, "MD": 29, "SMS": 64, "GG": 35, "32X": 30, "SEGACD": 78, "SATURN": 32,
    "DC": 23, "PCENGINE": 86, "ATARI2600": 59, "ATARI5200": 66, "ATARI7800": 60,
    "JAGUAR": 62, "LYNX": 61, "3DO": 50, "AMIGA": 16, "C64": 15, "MSX": 27,
    "MSX2": 53, "NGP": 119, "WSWAN": 57, "X360": 12, "MAME": 52, "FBNEO": 52,
}
TGDB_PLATFORMS = {
    "PS1": 10, "PS2": 11, "PS3": 12, "PSP": 13, "PSVITA": 39, "NES": 7, "SNES": 6,
    "SNESMSU1": 6, "N64": 3, "GB": 4, "GBC": 41, "GBA": 5, "NDS": 8, "GCN": 2,
    "WII": 9, "MD": 18, "SMS": 35, "GG": 20, "32X": 33, "SEGACD": 21, "SATURN": 17,
    "DC": 16, "PCENGINE": 34, "ATARI2600": 22, "ATARI5200": 26, "ATARI7800": 27,
    "JAGUAR": 28, "LYNX": 4924, "3DO": 25, "AMIGA": 4911, "C64": 40, "MSX": 4929,
    "NGP": 4922, "WSWAN": 4925, "X360": 15, "MAME": 23, "FBNEO": 23,
}


def fetch(url: str, hdrs: dict | None = None, data: bytes | None = None,
          timeout: int = 20) -> bytes | None:
    try:
        req = urllib.request.Request(url, data=data, headers={**UA, **(hdrs or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


_online = {"t": 0.0, "ok": True}


def online() -> bool:
    """Czy serwer miniatur odpowiada (wynik ważny 60 s) — odróżnia „brak grafiki”
    od „brak sieci”, żeby offline nie oznaczać gier jako bez okładki."""
    if time.monotonic() - _online["t"] > 60:
        _online.update(t=time.monotonic(), ok=fetch(systems.LIBRETRO_THUMBS + "/", timeout=8) is not None)
    return _online["ok"]


def is_image(data: bytes | None) -> bool:
    return bool(data) and (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff"
                           or data[:4] == b"RIFF" or data[:6] in (b"GIF87a", b"GIF89a"))


# ── porównywanie nazw ──

def similarity(a: str, b: str) -> float:
    """Współczynnik Dice'a na bigramach (jak name_similarity w PyLinks)."""
    def bigrams(s):
        s = re.sub(r"[^a-z0-9 ]", "", s.lower())
        return {s[i:i + 2] for i in range(len(s) - 1)}
    ba, bb = bigrams(a), bigrams(b)
    if not ba or not bb:
        return 1.0 if ba == bb else 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


_TAGS = re.compile(r"\s*[(\[][^)\]]*[)\]]")


def base_key(name: str) -> str:
    """Klucz tytułu bez tagów: 'Legend of Zelda, The - X (USA)' → 'thelegendofzeldax'."""
    t = _TAGS.sub("", name).strip()
    m = re.match(r"^(.*?), (The|A|An|Die|Der|Das|Le|La|Les|El|Il)( - .*|: .*)?$", t, re.I)
    if m:
        t = f"{m.group(2)} {m.group(1)}{m.group(3) or ''}"
    t = t.replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def tags(name: str) -> set:
    return {t.strip("()[] ").lower() for t in re.findall(r"[(\[][^)\]]*[)\]]", name)}


def plain_title(name: str) -> str:
    return _TAGS.sub("", name).strip()


# ── 1–2. libretro ──

_index_lock = threading.Lock()
_index_mem: dict = {}


def _index(system_name: str, folder: str) -> list:
    """Nazwy plików (bez .png) z listingu katalogu na serwerze miniatur libretro."""
    key = f"{system_name}|{folder}"
    with _index_lock:
        if key in _index_mem:
            return _index_mem[key]
        cache = paths.DATA / "thumbindex" / (re.sub(r"[^\w-]+", "_", key) + ".json")
        names = None
        try:
            if time.time() - cache.stat().st_mtime < INDEX_TTL:
                names = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if names is None:
            url = "/".join((systems.LIBRETRO_THUMBS, urllib.parse.quote(system_name), folder, ""))
            raw = fetch(url, timeout=60)
            if raw is None:
                return []           # offline — nie zapamiętujemy pustej listy
            names = [html.unescape(urllib.parse.unquote(h))[:-4]
                     for h in re.findall(r'href="([^"?/][^"]*\.png)"', raw.decode("utf-8", "replace"))]
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
        _index_mem[key] = names
        return names


def libretro_match(name: str, candidates: list) -> str | None:
    """Najlepsza nazwa z listy libretro dla gry `name` (bez dokładnego trafienia)."""
    want = base_key(name)
    if not want:
        return None
    want_tags = tags(name)
    best, best_score = None, 0.0
    for c in candidates:
        ck = base_key(c)
        if ck == want:
            score = 2.0
        elif ck[:6] == want[:6] and abs(len(ck) - len(want)) < 12:
            s = similarity(plain_title(name), plain_title(c))
            if s < 0.9:
                continue
            score = 1.0 + s - 0.9
        else:
            continue
        ct = tags(c)
        score += 0.1 * len(want_tags & ct) - 0.02 * len(ct - want_tags)
        if any(t.startswith("disc 1") for t in ct):
            score += 0.05           # gra wielopłytowa: okładka pierwszej płyty
        if score > best_score:
            best, best_score = c, score
    return best


def libretro_url(system_name: str, folder: str, file_title: str) -> str:
    return "/".join((systems.LIBRETRO_THUMBS, urllib.parse.quote(system_name), folder,
                     urllib.parse.quote(systems.thumb_name(file_title)) + ".png"))


def from_libretro(es: str, name: str, kind: str, fuzzy: bool = True) -> bytes | None:
    sysname = systems.info(es)["libretro"]
    folder = {"box": "Named_Boxarts", "snap": "Named_Snaps"}[kind]
    data = fetch(libretro_url(sysname, folder, name))
    if is_image(data):
        return data
    if not fuzzy:
        return None
    hit = libretro_match(name, _index(sysname, folder))
    if hit and hit != name:
        data = fetch(libretro_url(sysname, folder, hit))
        if is_image(data):
            log.info("libretro: %s → %s", name, hit)
            return data
    return None


# ── 3. SteamGridDB ──

class Sgdb:
    BASE = "https://www.steamgriddb.com/api/v2"

    def __init__(self, key: str):
        self.key = (key or "").strip()

    def _get(self, path: str):
        d = fetch(f"{self.BASE}/{path}", {"Authorization": f"Bearer {self.key}"})
        try:
            obj = json.loads(d) if d else {}
        except ValueError:
            return []
        return obj.get("data", []) if obj.get("success", True) else []

    def logo(self, title: str) -> bytes | None:
        """Przezroczyste logo gry (SteamGridDB „logos”)."""
        if not self.key:
            return None
        res = [r for r in self._get("search/autocomplete/" + urllib.parse.quote(title))
               if similarity(title, r.get("name", "")) >= MATCH_MIN]
        if not res:
            return None
        best = max(res, key=lambda r: similarity(title, r.get("name", "")))
        for g in self._get(f"logos/game/{best['id']}?limit=3"):
            data = fetch(g.get("url", ""))
            if is_image(data):
                return data
        return None

    def box(self, title: str) -> bytes | None:
        if not self.key:
            return None
        res = self._get("search/autocomplete/" + urllib.parse.quote(title))
        res = [r for r in res if similarity(title, r.get("name", "")) >= MATCH_MIN]
        if not res:
            return None
        best = max(res, key=lambda r: similarity(title, r.get("name", "")))
        for g in self._get(f"grids/game/{best['id']}?dimensions=600x900,342x482,660x930&limit=5"):
            data = fetch(g.get("url", ""))
            if is_image(data):
                return data
        return None


# ── 4. IGDB ──

class Igdb:
    API = "https://api.igdb.com/v4"

    def __init__(self, client_id: str, secret: str):
        self.cid, self.secret = (client_id or "").strip(), (secret or "").strip()
        self._tok, self._exp = "", 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def _token(self) -> str:
        with self._lock:
            if self._tok and time.time() < self._exp - 60:
                return self._tok
            # sekret w treści POST (adresy trafiają do logów serwerów pośrednich)
            body = urllib.parse.urlencode({"client_id": self.cid, "client_secret": self.secret,
                                           "grant_type": "client_credentials"}).encode()
            d = fetch("https://id.twitch.tv/oauth2/token", {"Content-Type": "application/x-www-form-urlencoded"},
                      data=body)
            try:
                obj = json.loads(d or b"{}")
                self._tok, self._exp = obj["access_token"], time.time() + obj.get("expires_in", 3600)
            except (ValueError, KeyError):
                self._tok = ""
            return self._tok

    def _query(self, body: str) -> list:
        tok = self._token() if self.cid and self.secret else ""
        if not tok:
            return []
        with self._lock:                 # limit IGDB: 4 zapytania/s
            wait = self._last + 0.3 - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
        d = fetch(f"{self.API}/games", {"Client-ID": self.cid, "Authorization": f"Bearer {tok}",
                                        "Content-Type": "text/plain"}, data=body.encode())
        try:
            return json.loads(d) if d else []
        except ValueError:
            return []

    def images(self, title: str, plat: str) -> dict:
        """{'box': url, 'snap': url} dla najlepiej pasującej gry."""
        pid = IGDB_PLATFORMS.get(plat)
        esc = title.replace('"', '\\"')
        where = f" where platforms = ({pid});" if pid else ""
        games = self._query(f'search "{esc}"; fields name,cover.url,screenshots.url;{where} limit 10;')
        games = [g for g in games if similarity(title, g.get("name", "")) >= MATCH_MIN]
        if not games:
            return {}
        g = max(games, key=lambda g: similarity(title, g.get("name", "")))
        out = {}
        if (g.get("cover") or {}).get("url"):
            out["box"] = "https:" + g["cover"]["url"].replace("t_thumb", "t_cover_big")
        if g.get("screenshots"):
            out["snap"] = "https:" + g["screenshots"][0]["url"].replace("t_thumb", "t_screenshot_big")
        return out


# ── 5. TheGamesDB ──

class Tgdb:
    """TheGamesDB ma miesięczny limit zapytań na klucz (u Ciebie ~1000) —
    poniżej RESERVE przestajemy go używać, żeby zostało na ręczne wyszukiwania."""
    API = "https://api.thegamesdb.net/v1"
    RESERVE = 100
    remaining: int | None = None

    def __init__(self, key: str):
        self.key = (key or "").strip()

    def usable(self) -> bool:
        return bool(self.key) and (Tgdb.remaining is None or Tgdb.remaining > self.RESERVE)

    def _get(self, url: str) -> dict:
        try:
            obj = json.loads(fetch(url) or b"{}")
        except ValueError:
            return {}
        if isinstance(obj.get("remaining_monthly_allowance"), int):
            Tgdb.remaining = obj["remaining_monthly_allowance"]
        return obj

    def game(self, title: str, plat: str, fields: str = "") -> dict | None:
        if not self.usable():
            return None
        pid = TGDB_PLATFORMS.get(plat)
        url = (f"{self.API}/Games/ByGameName?apikey={self.key}&name={urllib.parse.quote(title)}"
               + (f"&filter[platform]={pid}" if pid else "") + (f"&fields={fields}" if fields else ""))
        games = self._get(url).get("data", {}).get("games", [])
        games = [g for g in games if similarity(title, g.get("game_title", "")) >= MATCH_MIN]
        return max(games, key=lambda g: similarity(title, g.get("game_title", ""))) if games else None

    def images(self, title: str, plat: str) -> dict:
        g = self.game(title, plat)
        if not g:
            return {}
        gid = str(g["id"])
        data = self._get(f"{self.API}/Games/Images?apikey={self.key}&games_id={gid}").get("data", {})
        base = data.get("base_url", {}).get("original", "https://cdn.thegamesdb.net/images/original/")
        out = {}
        for img in data.get("images", {}).get(gid, []):
            t, fn = img.get("type"), img.get("filename")
            if t == "boxart" and img.get("side") in ("front", None, "") and "box" not in out:
                out["box"] = base + fn
            elif t in ("screenshot", "titlescreen") and "snap" not in out:
                out["snap"] = base + fn
        return out


# ── klucze z PyLinksWeb ──

PYLINKS_CONFIGS = (r"D:\py\PyLinksWeb\config.json",)
KEY_NAMES = ("sgdb_key", "igdb_client_id", "igdb_client_secret", "tgdb_key")


def import_pylinks_keys(path: str = "") -> dict:
    """Klucze SGDB / IGDB / TGDB z config.json PyLinksWeb.

    PyLinks trzyma je zaszyfrowane (TPM/DPAPI) — odszyfrowujemy kluczem PyLinks
    i szyfrujemy ponownie kluczem EmuStart, więc w naszym config.json też nie
    ma ich jawnie."""
    from emustart import secure
    for p in ([path] if path else []) + list(PYLINKS_CONFIGS):
        try:
            c = json.loads(Path(p).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        e = c.get("extra_sources") or {}
        raw = {"sgdb_key": (c.get("api_keys") or {}).get("sgdb_key", ""),
               "igdb_client_id": e.get("igdb_client_id", ""),
               "igdb_client_secret": e.get("igdb_client_secret", ""),
               "tgdb_key": e.get("tgdb_key", "")}
        out = {"source": p}
        for k, v in raw.items():
            plain = secure.unprotect(v, secure.KEY_PYLINKS)
            out[k] = secure.protect(plain) if plain else ""
        return out
    return {}


def keys(cfg: dict) -> dict:
    """Odszyfrowane klucze z config.json EmuStart."""
    from emustart import secure
    k = cfg.get("art_keys") or {}
    return {n: secure.unprotect(k.get(n, "")) for n in KEY_NAMES}


class Sources:
    """Łańcuch źródeł skonfigurowany kluczami z config.json EmuStart."""

    def __init__(self, cfg: dict):
        k = keys(cfg)
        self.sgdb = Sgdb(k.get("sgdb_key", ""))
        self.igdb = Igdb(k.get("igdb_client_id", ""), k.get("igdb_client_secret", ""))
        self.tgdb = Tgdb(k.get("tgdb_key", ""))

    def enabled(self) -> dict:
        return {"libretro": True, "sgdb": bool(self.sgdb.key),
                "igdb": bool(self.igdb.cid and self.igdb.secret), "tgdb": bool(self.tgdb.key),
                "tgdb_remaining": Tgdb.remaining}

    def find(self, es: str, name: str, need: set, setname: str = "") -> dict:
        """{kind: (bytes, źródło)} dla rodzajów z `need` ({'box','snap','logo'})."""
        from emustart import launchbox
        out = {}
        for kind in need - {"logo"}:
            data = from_libretro(es, name, kind)
            if data:
                out[kind] = (data, "libretro")
        rest = need - set(out)
        if rest and launchbox.ready():
            # LaunchBox: grafiki posegregowane (okładka, zrzut, Clear Logo), baza lokalna
            g = launchbox.find_game(es, name, setname)
            for kind in list(rest) if g else []:
                for img in launchbox.images(g["id"], kind)[:3]:
                    data = fetch(img["url"])
                    if is_image(data):
                        out[kind] = (data, "launchbox")
                        break
        rest = need - set(out)
        if not rest:
            return out
        title = plain_title(name)
        plat = systems.info(es)["plat"]
        if "box" in rest:
            data = self.sgdb.box(title)
            if data:
                out["box"] = (data, "sgdb")
        if "logo" in rest:
            data = self.sgdb.logo(title)
            if data:
                out["logo"] = (data, "sgdb")
        need = need - {"logo"}          # IGDB/TGDB nie mają logo gier
        for src, client in (("igdb", self.igdb), ("tgdb", self.tgdb)):
            rest = need - set(out)
            if not rest:
                break
            urls = client.images(title, plat)
            for kind in rest:
                data = fetch(urls[kind]) if urls.get(kind) else None
                if is_image(data):
                    out[kind] = (data, src)
        return out


# ── ręczny wybór grafiki (opcje gry) ──

def libretro_ranked(name: str, names: list, limit: int = 8) -> list:
    """Nazwy z listy libretro najbardziej podobne do `name` (do ręcznego wyboru)."""
    want = base_key(name)
    title = plain_title(name)
    scored = []
    for c in names:
        ck = base_key(c)
        if ck == want:
            sc = 2.0
        elif want[:4] and ck[:4] == want[:4]:
            sc = similarity(title, plain_title(c))
            if sc < 0.6:
                continue
        else:
            continue
        sc += 0.05 * len(tags(name) & tags(c))
        scored.append((sc, c))
    scored.sort(reverse=True)
    return [c for _s, c in scored[:limit]]


def candidates(cfg: dict, es: str, name: str, kind: str, query: str = "",
               setname: str = "") -> list:
    """[{url, thumb, source, label}] — propozycje grafiki do wyboru padem."""
    from emustart import launchbox
    out = []
    if launchbox.ready():
        g = launchbox.find_game(es, query or name, setname)
        for img in launchbox.images(g["id"], kind)[:24] if g else []:
            out.append({"url": img["url"], "thumb": img["url"], "source": "LaunchBox",
                        "label": img["type"] + (f" · {img['region']}" if img["region"] else "")})
    if kind == "logo":
        k = keys(cfg)
        if k.get("sgdb_key"):
            sg = Sgdb(k["sgdb_key"])
            for game in sg._get("search/autocomplete/" + urllib.parse.quote(query or plain_title(name)))[:2]:
                for lg in sg._get(f"logos/game/{game['id']}?limit=8"):
                    out.append({"url": lg.get("url", ""), "thumb": lg.get("thumb") or lg.get("url", ""),
                                "source": "SteamGridDB", "label": game.get("name", "")})
        return [c for c in out if c["url"]]
    sysname = systems.info(es)["libretro"]
    folder = {"box": "Named_Boxarts", "snap": "Named_Snaps"}[kind]
    for n in libretro_ranked(query or name, _index(sysname, folder)):
        u = libretro_url(sysname, folder, n)
        out.append({"url": u, "thumb": u, "source": "libretro", "label": n})
    title = query or plain_title(name)
    plat = systems.info(es)["plat"]
    k = keys(cfg)
    if kind == "box" and k.get("sgdb_key"):
        sg = Sgdb(k["sgdb_key"])
        for game in sg._get("search/autocomplete/" + urllib.parse.quote(title))[:3]:
            for g in sg._get(f"grids/game/{game['id']}?dimensions=600x900,342x482,660x930&limit=6"):
                out.append({"url": g.get("url", ""), "thumb": g.get("thumb") or g.get("url", ""),
                            "source": "SteamGridDB", "label": game.get("name", "")})
    tg = Tgdb(k.get("tgdb_key", ""))
    imgs = tg.images(title, plat) if tg.usable() else {}
    if imgs.get(kind):
        out.append({"url": imgs[kind], "thumb": imgs[kind], "source": "TheGamesDB", "label": title})
    ig = Igdb(k.get("igdb_client_id", ""), k.get("igdb_client_secret", ""))
    if ig.cid and ig.secret:
        u = ig.images(title, plat).get(kind)
        if u:
            out.append({"url": u, "thumb": u, "source": "IGDB", "label": title})
    return [c for c in out if c["url"]]
