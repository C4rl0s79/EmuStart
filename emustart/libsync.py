"""libsync — komputer bez folderów z grami: biblioteka, grafiki i zalecane emulatory z serwera (0.38).

„Cienki klient” (etap 2 koncepcji 2.0): komputer połączony z serwerem EmuStart, bez
folderów z grami (`rom_roots`), niczego nie skanuje — wszystko dostaje od serwera:

- **biblioteka** — `GET /v1/library` (systemy z wersją = czas skanowania na serwerze),
  `GET /v1/library?es=…` (gry systemu: ścieżki, pliki, rozmiary, tagi, znaczniki grafik);
  pobierane są tylko systemy, które zmieniły się od ostatniej synchronizacji;
- **gry** — przy uruchomieniu w całości przez serwer (netsrc: wszystkie pliki z serwera);
- **grafiki** — lokalny serwer interfejsu, gdy nie ma pliku, pobiera go z serwera i zapamiętuje
  (`fetch_media`);
- **emulator dla systemu** — przy wykrywaniu emulatorów zalecany przez serwer (ten sam
  emulator / rdzeń RetroArcha co na komputerze administratora — stany zgodne).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
from pathlib import Path

from emustart import library, netsrc

log = logging.getLogger("emustart.libsync")

SERVER_SRC = "serwer"            # games.src gry z biblioteki serwera (bez lokalnego folderu)
_lock = threading.Lock()
_media_miss: dict = {}           # rel → czas (brak na serwerze — nie pytamy co chwilę)
MISS_TTL = 600


def thin(cfg: dict) -> bool:
    """EmuStart na PC bierze bibliotekę z serwera — gdy jest zalogowany do serwera."""
    return bool(cfg.get("server_url") and cfg.get("server_key"))


def _get(cfg: dict, path: str, timeout: float = 30):
    ep = netsrc.endpoint(cfg)
    if not ep:
        raise OSError("serwer nie jest ustawiony")
    st, body, _rtt = netsrc._request(ep, path, timeout, gzip=True)
    if st == 401 and ep[2].startswith(netsrc.DEV):
        netsrc.drop_token()
        st, body, _rtt = netsrc._request(ep, path, timeout, gzip=True)
    if st == 403:
        raise PermissionError(json.loads(body or b"{}").get("error") or "brak dostępu")
    if st != 200:
        raise OSError(f"serwer: {path} → {st}")
    return json.loads(body)


def sync(cfg: dict, progress=None, force: bool = False) -> dict:
    """Biblioteka z serwera → lokalna baza. {ok, systems, games, changed} albo {ok: False, reason}."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "reason": "Synchronizacja już trwa."}
    try:
        info = netsrc.info(cfg, fresh=True)
        if not info.get("ok"):
            return {"ok": False, "reason": f"Serwer niedostępny: {info.get('reason', '')}"}
        if "library" not in info.get("features", []):
            return {"ok": False, "reason": "Serwer ma starszą wersję EmuStart — zaktualizuj go (panel serwera)."}
        try:
            remote = _get(cfg, "/v1/library").get("systems") or []
        except PermissionError as ex:
            return {"ok": False, "reason": str(ex)}
        local = {r["es"]: r for r in (dict(x) for x in library.db().execute("SELECT es, scanned FROM systems"))}
        try:
            old_rec = json.loads(library.meta_get("server_emu_rec", "{}") or "{}")
        except ValueError:
            old_rec = {}
        try:
            fulls = json.loads(library.meta_get("server_full", "{}") or "{}")
        except ValueError:
            fulls = {}
        changed = games = 0
        rec = {}
        for i, s in enumerate(remote):
            es = s["es"]
            rec[es] = {"emu": s.get("emu_rec", ""), "core": s.get("core", ""),
                       "logo": s.get("logo", ""), "glow": bool(s.get("logo_glow"))}
            if progress:
                progress(f"Biblioteka z serwera: {s.get('display') or es}", i, len(remote))
            if not force and local.get(es, {}).get("scanned") == s.get("scanned"):
                games += int(s.get("games") or 0)
                continue
            have = local.get(es, {}).get("scanned")
            if not force and have and s.get("full") is not None and fulls.get(es) == s["full"]:
                # zmieniły się tylko grafiki / opisy części gier — tylko one (nie cały system)
                rows = _get(cfg, "/v1/library?" + urllib.parse.urlencode({"es": es, "since": repr(float(have))}),
                            timeout=120).get("games") or []
                _write(es, s, rows, partial=True)
                games += int(s.get("games") or 0)
            else:
                rows = _get(cfg, "/v1/library?" + urllib.parse.urlencode({"es": es}), timeout=120).get("games") or []
                _write(es, s, rows)
                games += len(rows)
            if s.get("full") is not None:
                fulls[es] = s["full"]
            changed += 1
        gone = set(local) - {s["es"] for s in remote}
        if gone:
            with library.db() as c:
                for es in gone:
                    ids = [r["id"] for r in c.execute("SELECT id FROM games WHERE es=?", (es,))]
                    for j in range(0, len(ids), 500):
                        chunk = ids[j:j + 500]
                        marks = ",".join("?" * len(chunk))
                        c.execute(f"DELETE FROM cache WHERE game_id IN ({marks})", chunk)
                    c.execute("DELETE FROM games WHERE es=?", (es,))
                    c.execute("DELETE FROM systems WHERE es=?", (es,))
        library.meta_set("server_emu_rec", json.dumps(rec))
        _drop_changed_logos(old_rec, rec)
        if progress:
            progress("Gotowe", len(remote), len(remote))
        library.meta_set("server_full", json.dumps(fulls))
        log.info("biblioteka z serwera: %d systemów (%d zmienionych), %d gier", len(remote), changed, games)
        return {"ok": True, "systems": len(remote), "games": games, "changed": changed}
    except (OSError, ValueError) as ex:
        log.warning("biblioteka z serwera: %s", ex)
        return {"ok": False, "reason": f"Biblioteka z serwera: {ex}"}
    finally:
        _lock.release()


COLS = ("rel", "name", "title", "tags", "files", "size", "is_dir", "multidisc", "parent", "hidden",
        "trans", "tbase", "art_box", "art_snap", "art_logo")


def _drop_changed_logos(old: dict, new: dict) -> None:
    """Logo systemu zmienione na serwerze (inny adres / wersja) — lokalna kopia od nowa."""
    from emustart import mediastore
    for es, r in new.items():
        before = (old.get(es) or {}).get("logo", "")
        if before and before != r.get("logo") and before.startswith("/media/"):
            where = mediastore.file_name(urllib.parse.unquote(before.split("?")[0][len("/media/"):]))
            if where:
                mediastore.delete(*where)


def _drop_changed_art(es: str, rows: list) -> int:
    """Grafiki gier zmienione na serwerze (inny czas pliku) — lokalna kopia usunięta, przy
    wyświetleniu pobierze się nowa. Zwraca liczbę usuniętych."""
    from emustart import art
    old = {r["rel"]: (r["name"], r["art_m"]) for r in library.db().execute(
        "SELECT rel, name, art_m FROM games WHERE es=?", (es,))}
    n = 0
    for r in rows:
        prev = old.get(r["rel"])
        if not prev:
            continue
        try:
            before = json.loads(prev[1] or "{}")
        except ValueError:
            before = {}
        now = r.get("art_m") or {}
        for kind in ("box", "snap", "logo"):
            if before.get(kind) != now.get(kind) or prev[0] != r["name"]:
                if art.delete(es, prev[0], kind):
                    n += 1
    return n


def _write(es: str, s: dict, rows: list, partial: bool = False) -> None:
    """Gry systemu z serwera → baza. partial — tylko zmienione gry (pozostałe zostają)."""
    now = time.time()
    _drop_changed_art(es, rows)
    data = []
    for r in rows:
        files = r.get("files")
        data.append((es, r["rel"], r["name"], r.get("title") or r["name"], r.get("tags", ""),
                     files if isinstance(files, str) else json.dumps(files or []), int(r.get("size") or 0),
                     int(r.get("is_dir") or 0), int(r.get("multidisc") or 0), r.get("parent", ""),
                     int(r.get("hidden") or 0), now, SERVER_SRC, r.get("trans", ""), r.get("tbase", ""),
                     int(r.get("art_box") or 0), int(r.get("art_snap") or 0), int(r.get("art_logo") or 0),
                     json.dumps(r.get("art_m") or {})))
    with library.db() as c:
        c.executemany("""
            INSERT INTO games(es, rel, name, title, tags, files, size, is_dir, multidisc, parent,
                              hidden, seen, src, trans, tbase, art_box, art_snap, art_logo, art_m)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(es, rel) DO UPDATE SET
              name=excluded.name, title=excluded.title, tags=excluded.tags, files=excluded.files,
              size=excluded.size, is_dir=excluded.is_dir, multidisc=excluded.multidisc,
              parent=excluded.parent, hidden=excluded.hidden, seen=excluded.seen, src=excluded.src,
              trans=excluded.trans, tbase=excluded.tbase, art_box=excluded.art_box,
              art_snap=excluded.art_snap, art_logo=excluded.art_logo, art_m=excluded.art_m""", data)
        # opisy i dane gier z serwera (serwer jest źródłem prawdy — PC sam ich nie szuka)
        ids = {r["rel"]: r["id"] for r in c.execute("SELECT id, rel FROM games WHERE es=?", (es,))}
        meta = [(ids[r["rel"]], json.dumps(r["meta"].get("data") or {}, ensure_ascii=False),
                 json.dumps(r["meta"].get("edits") or {}, ensure_ascii=False), 1, now)
                for r in rows if r.get("meta") and r["rel"] in ids]
        c.executemany("INSERT OR REPLACE INTO game_meta(game_id, data, edits, online, updated) VALUES(?,?,?,?,?)", meta)
        gone = [] if partial else [r["id"] for r in c.execute("SELECT id FROM games WHERE es=? AND seen<?", (es, now))]
        for j in range(0, len(gone), 500):
            chunk = gone[j:j + 500]
            marks = ",".join("?" * len(chunk))
            c.execute(f"DELETE FROM games WHERE id IN ({marks})", chunk)
            c.execute(f"DELETE FROM cache WHERE game_id IN ({marks})", chunk)
        c.execute("INSERT OR REPLACE INTO systems(es, display, rom_dir, games, scanned) VALUES(?,?,?,?,?)",
                  (es, s.get("display") or es, "[]", len(data), s.get("scanned") or now))


def recommended(es: str) -> dict:
    """{emu: retroarch|standalone|'', core} — zalecany przez serwer emulator systemu."""
    try:
        return (json.loads(library.meta_get("server_emu_rec", "{}") or "{}") or {}).get(es) or {}
    except ValueError:
        return {}


def bios_from_server(cfg: dict, rel: str, dst: Path) -> bool:
    """BIOS (ścieżka względem folderu system RetroArcha) z serwera do `dst`; False = serwer go nie ma."""
    ep = netsrc.endpoint(cfg)
    if not ep:
        return False
    try:
        st, body, _rtt = netsrc._request(ep, "/v1/bios?" + urllib.parse.urlencode({"name": rel}), 60)
    except OSError as ex:
        log.info("BIOS %s z serwera: %s", rel, ex)
        return False
    if st != 200 or not body:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    tmp.write_bytes(body)
    tmp.replace(dst)
    log.info("BIOS %s z serwera", rel)
    return True


def bios_list(cfg: dict, query: dict) -> list:
    """BIOS-y, które serwer ma: {"core": rdzeń} albo {"dir": folder} → [ścieżki]."""
    ep = netsrc.endpoint(cfg)
    if not ep:
        return []
    try:
        st, body, _rtt = netsrc._request(ep, "/v1/bios?" + urllib.parse.urlencode(query), 30)
        return list(json.loads(body).get("files") or []) if st == 200 else []
    except (OSError, ValueError) as ex:
        log.info("BIOS-y z serwera: %s", ex)
        return []


def logo_for(es: str) -> dict | None:
    """Logo systemu z serwera ({url, glow}); własne logo (/media/_systems/…) pobiera się samo
    przy pierwszym wyświetleniu (fetch_media)."""
    rec = recommended(es)
    return {"url": rec["logo"], "glow": rec.get("glow", False)} if rec.get("logo") else None


def fetch_media(cfg: dict, rel: str, target: Path) -> bool:
    """Grafika z serwera do lokalnego folderu grafik (lokalny serwer interfejsu jej nie ma)."""
    if not thin(cfg) and not cfg.get("server_media", False):
        return False
    hit = _media_miss.get(rel)
    if hit and time.time() - hit < MISS_TTL:
        return False
    ep = netsrc.endpoint(cfg)
    if not ep:
        return False
    try:
        st, body, _rtt = netsrc._request(ep, "/media/" + urllib.parse.quote(rel), 20)
        if st == 401 and ep[2].startswith(netsrc.DEV):     # token sesji wygasł — nowy
            netsrc.drop_token()
            st, body, _rtt = netsrc._request(ep, "/media/" + urllib.parse.quote(rel), 20)
    except OSError:
        return False
    if st != 200 or not body:
        if st == 404:
            _media_miss[rel] = time.time()
        return False
    from emustart import mediastore
    where = mediastore.file_name(rel)
    if where:                                   # grafika gry — do pliku systemu (lokalna kopia)
        mediastore.put(where[0], where[1], body)
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(target)
    return True


def fetch_preview(cfg: dict, path: str):
    """Podgląd z serwera (logo z paczki serwera, grafika) dla okna wyboru — (dane, typ) albo None."""
    if not path.startswith("/media/") or ".." in path.split("/"):
        return None
    ep = netsrc.endpoint(cfg)
    if not ep:
        return None
    try:
        st, body, _rtt = netsrc._request(ep, urllib.parse.quote(path), 20)
    except OSError:
        return None
    if st != 200 or not body:
        return None
    import mimetypes
    return body, mimetypes.guess_type(path)[0] or "application/octet-stream"


# ── po stronie serwera ──
def server_systems() -> list:
    from emustart import remote
    out = []
    for r in library.db().execute("SELECT es, display, games, scanned FROM systems ORDER BY display COLLATE NOCASE"):
        sc = ((remote.API._cfg.get("systems") or {}).get(r["es"]) or {}) if remote.API else {}
        from emustart import logos
        lg = logos.url_for(r["es"], remote.API._cfg) if remote.API else {"url": "", "glow": False}
        # wersja systemu: skan albo ostatnia zmiana grafiki / opisu — urządzenia pobiorą go od nowa
        out.append({"es": r["es"], "display": sc.get("name") or r["display"], "games": r["games"],
                    "scanned": max(float(r["scanned"] or 0), library.rev(r["es"])),
                    # pełna lista potrzebna, gdy zmienił się skan albo cały system (nie pojedyncze gry)
                    "full": max(float(r["scanned"] or 0), library.full_rev(r["es"])),
                    "emu_rec": sc.get("emu_rec", ""), "core": sc.get("core_rec", ""),
                    "logo": lg["url"], "logo_glow": bool(lg["glow"])})
    return out


def server_games(es: str, since: float = 0) -> list:
    """Gry systemu dla PC: kolumny biblioteki, czasy grafik (PC podmienia zmienione) i opisy.
    since — tylko gry zmienione później (grafika / opis, game_chg)."""
    from emustart import art, savesync
    cols = ", ".join(f"g.{c}" for c in COLS)
    out = []
    where, args = "WHERE g.es=?", [es]
    if since:
        where += " AND g.id IN (SELECT game_id FROM game_chg WHERE t > ?)"
        args.append(since)
    for r in library.db().execute(f"SELECT {cols}, m.data AS m_data, m.edits AS m_edits FROM games g "
                                  f"LEFT JOIN game_meta m ON m.game_id=g.id {where}", args):
        d = {c: r[c] for c in COLS}
        art_m = {}
        for kind in ("box", "snap", "logo"):
            if r[f"art_{kind}"] == art.HAS:
                t = art.mtime(es, r["name"], kind)
                if t:
                    art_m[kind] = int(t)
        d["art_m"] = art_m
        d["game_id"] = savesync.game_id(es, r["name"])     # stały identyfikator (zapisy gier)
        if r["m_data"] or r["m_edits"]:
            d["meta"] = {"data": json.loads(r["m_data"] or "{}"), "edits": json.loads(r["m_edits"] or "{}")}
        out.append(d)
    return out

