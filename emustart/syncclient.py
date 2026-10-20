"""syncclient — synchronizacja zapisów gry z serwerem EmuStart (klient; serwer: savesync.py).

Przed grą (`before`) i po grze (`after`) dla każdej paczki gry (savebundles):

    stan lokalny  = (revision, skrót zawartości) z ostatniej udanej synchronizacji
    zmiana tutaj  = skrót plików ≠ zapamiętany
    zmiana gdzie indziej = revision na serwerze ≠ zapamiętana

    nic się nie zmieniło          → nic
    tylko na serwerze             → pobranie (kopia zapasowa bieżących plików, podmiana atomowa)
    tylko tutaj (np. gra offline) → wysłanie (base_revision = zapamiętana)
    tu i tam                      → konflikt — decyduje gracz: z tego urządzenia / z serwera / pomiń

Pierwsza synchronizacja (bez stanu lokalnego): pusto tu → pobranie, pusto na serwerze →
wysłanie, różne pliki po obu stronach → konflikt (nie zgadujemy, która wersja jest dobra).
Bez połączenia gra rusza na plikach lokalnych; niewysłane zmiany wysyła `retry` (w tle)
albo następne uruchomienie gry. W logach tylko identyfikatory, bez adresów z tokenami.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import shutil
import tempfile
import threading
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from emustart import library, savebundles

log = logging.getLogger("emustart.syncclient")

TIMEOUT = 15
BACKUPS_KEEP = 20                    # kopie zapasowe plików przed pobraniem (na profil)
_lock = threading.Lock()


class Offline(OSError):
    """Serwer nieosiągalny albo nie wpuszcza (gra rusza na plikach lokalnych)."""


class ConflictError(Exception):
    def __init__(self, current: dict):
        super().__init__("konflikt")
        self.current = current or {}


@dataclass
class Conflict:
    bundle: savebundles.Bundle
    server: dict                     # revision, updated_at, updated_by_device_id, file_count
    local_time: float = 0.0

    def describe(self) -> dict:
        b = self.bundle
        return {"emulator": b.emulator_id, "core": b.core_id, "kind": b.kind,
                "server_revision": self.server.get("revision") or 0,
                "server_time": self.server.get("updated_at") or 0,
                "server_device": self.server.get("updated_by_device_name") or self.server.get("updated_by_device_id") or "",
                "local_time": self.local_time, "local_files": len(b.files)}


@dataclass
class Result:
    offline: str = ""                                   # powód, gdy bez synchronizacji
    downloaded: int = 0
    uploaded: int = 0
    conflicts: list = field(default_factory=list)


# ── stan lokalny ──

def _db():
    c = library.db()
    c.execute("""CREATE TABLE IF NOT EXISTS save_sync(
        profile_id INTEGER NOT NULL, platform TEXT NOT NULL, game TEXT NOT NULL,
        emu TEXT NOT NULL, core TEXT NOT NULL, kind TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 0, content TEXT NOT NULL DEFAULT '',
        dirty INTEGER NOT NULL DEFAULT 0, spec TEXT NOT NULL DEFAULT '', updated REAL NOT NULL DEFAULT 0,
        PRIMARY KEY(profile_id, platform, game, emu, core, kind))""")
    return c


def _state(pid: int, plat: str, gid: str, b) -> dict | None:
    r = _db().execute("SELECT * FROM save_sync WHERE profile_id=? AND platform=? AND game=? AND emu=? "
                      "AND core=? AND kind=?", (pid, plat, gid, *b.key)).fetchone()
    return dict(r) if r else None


def _remember(pid: int, plat: str, gid: str, b, revision: int, content: str, dirty: bool = False) -> None:
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO save_sync VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (pid, plat, gid, *b.key, int(revision), content, int(dirty), json.dumps(b.spec()), time.time()))


def _mark_dirty(pid: int, plat: str, gid: str, b) -> None:
    st = _state(pid, plat, gid, b)
    _remember(pid, plat, gid, b, (st or {}).get("revision", 0), (st or {}).get("content", ""), True)


def game_ids(game: dict) -> tuple[str, str]:
    from emustart import savesync
    return str(game.get("es") or "").lower(), savesync.game_id(game.get("es", ""), game.get("name", ""))


# ── serwer ──

class _Client:
    def __init__(self, cfg: dict, pid: int):
        from emustart import netsrc, profiles
        self.cfg = cfg
        ep = netsrc.endpoint(cfg)
        if not ep:
            raise Offline("serwer nie jest ustawiony")
        self.ep = ep
        prof = profiles.get(pid) or {}
        self.account = prof.get("nas_name") or prof.get("name") or ""
        mine = profiles.account_of_device(cfg)
        self.extra = {}
        if self.account and self.account.lower() != (mine or "").lower():
            # gracz z innym kontem niż konto urządzenia — tylko z tokenem swojego konta
            self.extra = {"X-EmuStart-Account": urllib.parse.quote(self.account),
                          "X-Profile-Token": prof.get("pin_token") or ""}

    def call(self, method: str, path: str, body=None, headers: dict | None = None,
             timeout: float = TIMEOUT, sink=None) -> tuple[int, bytes]:
        from emustart import netsrc
        host, port, key = self.ep
        for attempt in range(2):
            c = netsrc.conn(host, port, timeout)
            try:
                if hasattr(body, "seek"):
                    body.seek(0)
                c.request(method, path, body=body,
                          headers={**netsrc.auth_headers(key), **self.extra, **(headers or {})})
                r = c.getresponse()
                if sink is not None and r.status == 200:
                    for buf in iter(lambda: r.read(1 << 20), b""):
                        sink.write(buf)
                    data = b""
                else:
                    data = r.read()
            except (OSError, http.client.HTTPException) as ex:
                netsrc.forget_best()
                raise Offline(f"brak połączenia z serwerem ({type(ex).__name__})")
            finally:
                c.close()
            if r.status == 401 and key.startswith(netsrc.DEV) and attempt == 0:
                netsrc.drop_token()
                continue
            return r.status, data
        return 401, b""

    def json(self, method: str, path: str, d: dict | None = None) -> dict:
        st, data = self.call(method, path, json.dumps(d).encode("utf-8") if d is not None else None,
                             {"Content-Type": "application/json"} if d is not None else None)
        try:
            out = json.loads(data or b"{}")
        except ValueError:
            out = {}
        if st == 409:
            raise ConflictError((out or {}).get("current") or {})
        if st in (401, 403):
            raise Offline(f"serwer nie wydaje zapisów tego konta ({st})")
        if st == 404:
            raise Offline("serwer bez synchronizacji zapisów (starsza wersja)")
        if st != 200 or not isinstance(out, dict):
            raise Offline(f"serwer odpowiedział {st}: {(out or {}).get('reason', '') if isinstance(out, dict) else ''}")
        return out


def _server_bundle(man: dict, b) -> dict:
    e = (man.get("emulators") or {}).get(b.emulator_id) or {}
    if b.emulator_id == "retroarch":
        e = (e.get("cores") or {}).get(b.core_id) or {}
    return (e.get("bundles") or {}).get(b.kind) or {}


# ── paczka ZIP ──

def _pack(b) -> Path:
    """ZIP paczki w pliku tymczasowym (nazwy umowne, czasy plików zachowane)."""
    fd, tmp = tempfile.mkstemp(prefix="emustart-save-", suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for path, arc in b.files:
            z.write(path, arc)
    return Path(tmp)


def _sha(p: Path) -> str:
    return savebundles.file_sha(p)


def _upload(cl: _Client, plat: str, gid: str, b, base: int) -> dict:
    zp = _pack(b)
    try:
        sha, size = _sha(zp), zp.stat().st_size
        d = {"platform_id": plat, "game_id": gid, "emulator_id": b.emulator_id, "core_id": b.core_id,
             "bundle_kind": b.kind, "base_revision": int(base), "sha256": sha, "size": size}
        init = cl.json("POST", "/v1/savesync/upload/init", d)
        with open(zp, "rb") as f:
            st, data = cl.call("PUT", init["upload_url"], f, {"Content-Length": str(size),
                                                              "Content-Type": "application/zip"}, timeout=120)
        if st != 200:
            raise Offline(f"wysyłanie paczki: serwer odpowiedział {st}")
        r = cl.json("POST", "/v1/savesync/upload/commit",
                    {"upload_id": init["upload_id"], "base_revision": int(base), "sha256": sha, "size": size})
        log.info("zapisy → serwer: %s/%s %s %s %s rev %s (%d plików, %d B)", plat, gid, *b.key,
                 r.get("revision"), len(b.files), size)
        return r
    finally:
        zp.unlink(missing_ok=True)


def _fetch(cl: _Client, srv: dict) -> Path:
    """Paczka z serwera do pliku tymczasowego, sprawdzona (SHA-256 z manifestu, ZIP)."""
    fd, tmp = tempfile.mkstemp(prefix="emustart-save-", suffix=".zip")
    os.close(fd)
    tmp = Path(tmp)
    try:
        with open(tmp, "wb") as f:
            st, _ = cl.call("GET", srv["download_url"], timeout=120, sink=f)
        if st != 200:
            raise Offline(f"pobieranie paczki: serwer odpowiedział {st}")
        if srv.get("sha256") and _sha(tmp) != srv["sha256"]:
            raise Offline("pobrana paczka uszkodzona (SHA-256)")
        with zipfile.ZipFile(tmp) as z:
            if z.testzip() is not None:
                raise Offline("pobrana paczka uszkodzona (ZIP)")
        return tmp
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _zip_hash(zp: Path) -> str:
    with zipfile.ZipFile(zp) as z:
        items = [i for i in z.infolist() if not i.is_dir()]
        if not items:
            return ""
        h = hashlib.sha256()
        for i in sorted(items, key=lambda x: x.filename):
            h.update(i.filename.encode("utf-8") + b"\0" + hashlib.sha256(z.read(i)).hexdigest().encode() + b"\n")
        return h.hexdigest()


def _backup_dir(pid: int) -> Path:
    from emustart import profiles
    return profiles.LOCAL / str(pid) / "_backup" / "savesync"


def _apply(pid: int, b, zp: Path) -> None:
    """Pliki paczki z serwera zamiast bieżących: kopia zapasowa bieżących, potem każdy plik
    przez plik tymczasowy i os.replace; pliki paczki, których na serwerze już nie ma, znikają
    (do kopii zapasowej)."""
    with zipfile.ZipFile(zp) as z:
        items = [i for i in z.infolist() if not i.is_dir()]
        targets = {i.filename: b.local_path(i.filename) for i in items}      # ValueError: zła nazwa
        if b.files:
            bak = _backup_dir(pid) / time.strftime("%Y%m%d-%H%M%S") / f"{b.emulator_id}-{b.core_id or '-'}-{b.kind}"
            bak.mkdir(parents=True, exist_ok=True)
            for path, _arc in b.files:
                shutil.copy2(path, bak / Path(path).name)
            _prune_backups(pid)
        b.folder.mkdir(parents=True, exist_ok=True)
        for i in items:
            dst = targets[i.filename]
            tmp = dst.with_name(dst.name + ".emustart-new")
            with z.open(i) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
            t = time.mktime(i.date_time + (0, 0, -1))
            os.utime(tmp, (t, t))
            os.replace(tmp, dst)
    keep = {str(p).lower() for p in targets.values()}
    for path, _arc in b.files:
        if str(path).lower() not in keep:
            Path(path).unlink(missing_ok=True)
    b.collect()


def _prune_backups(pid: int) -> None:
    root = _backup_dir(pid)
    old = sorted((d for d in root.iterdir() if d.is_dir()), key=lambda d: d.name)[:-BACKUPS_KEEP]
    for d in old:
        shutil.rmtree(d, ignore_errors=True)


def _download(cl, pid, plat, gid, b, srv: dict) -> None:
    zp = _fetch(cl, srv)
    try:
        h = _zip_hash(zp)
        _apply(pid, b, zp)
        _remember(pid, plat, gid, b, srv.get("revision") or 0, h)
        log.info("zapisy ← serwer: %s/%s %s %s %s rev %s (%d plików)", plat, gid, *b.key,
                 srv.get("revision"), len(b.files))
    finally:
        zp.unlink(missing_ok=True)


def _newest(b) -> float:
    return max((Path(p).stat().st_mtime for p, _a in b.files), default=0.0)


# ── przed grą / po grze ──

def before(cfg: dict, pid: int, bundles: list, game: dict) -> Result:
    """Synchronizacja przed grą. Konflikty zwraca (decyzja gracza → resolve)."""
    res = Result()
    if not bundles:
        return res
    plat, gid = game_ids(game)
    try:
        cl = _Client(cfg, pid)
        man = cl.json("GET", f"/v1/savesync/{plat}/{gid}/manifest")
    except Offline as ex:
        res.offline = str(ex)
        log.info("zapisy %s/%s bez synchronizacji: %s", plat, gid, ex)
        return res
    with _lock:
        for b in bundles:
            try:
                _before_one(cl, pid, plat, gid, b, _server_bundle(man, b), res)
            except Offline as ex:
                res.offline = str(ex)
                log.info("zapisy %s/%s %s: %s", plat, gid, b.key, ex)
            except (OSError, ValueError, zipfile.BadZipFile) as ex:
                log.exception("zapisy %s/%s %s", plat, gid, b.key)
                res.offline = f"błąd synchronizacji ({ex})"
    return res


def _before_one(cl, pid, plat, gid, b, srv: dict, res: Result) -> None:
    rev_s = int(srv.get("revision") or 0)
    local = savebundles.content_hash(b.files)
    st = _state(pid, plat, gid, b)
    if st is None:
        if not rev_s:
            if local:
                _push(cl, pid, plat, gid, b, 0, local, res)
            return
        if not local:
            _download(cl, pid, plat, gid, b, srv)
            res.downloaded += 1
            return
        zp = _fetch(cl, srv)
        try:
            same = _zip_hash(zp) == local
        finally:
            zp.unlink(missing_ok=True)
        if same:
            _remember(pid, plat, gid, b, rev_s, local)
        else:
            res.conflicts.append(Conflict(b, srv, _newest(b)))
        return
    changed_here = local != st["content"]
    changed_there = rev_s != st["revision"]
    if changed_there and not changed_here:
        if rev_s:
            _download(cl, pid, plat, gid, b, srv)
            res.downloaded += 1
    elif changed_here and not changed_there:
        _push(cl, pid, plat, gid, b, rev_s, local, res)
    elif changed_here and changed_there:
        res.conflicts.append(Conflict(b, srv, _newest(b)))
    elif st["dirty"]:
        _remember(pid, plat, gid, b, rev_s, local)


def _push(cl, pid, plat, gid, b, base: int, local: str, res: Result) -> None:
    if not local:
        return                           # pusty zestaw nie kasuje zapisów na serwerze
    try:
        r = _upload(cl, plat, gid, b, base)
    except ConflictError as ex:
        res.conflicts.append(Conflict(b, ex.current, _newest(b)))
        return
    _remember(pid, plat, gid, b, r["revision"], local)
    res.uploaded += 1


def resolve(cfg: dict, pid: int, game: dict, conflicts: list, choice: str) -> Result:
    """Decyzja gracza: local — wersja z tego urządzenia zostaje (serwerowa do historii na
    serwerze), remote — pliki z serwera (bieżące do kopii zapasowej), skip — bez zmian
    (konflikt wróci przy następnej grze)."""
    res = Result()
    plat, gid = game_ids(game)
    if choice == "skip" or not conflicts:
        return res
    cl = _Client(cfg, pid)
    man = cl.json("GET", f"/v1/savesync/{plat}/{gid}/manifest")
    with _lock:
        for c in conflicts:
            b = c.bundle.collect()
            srv = _server_bundle(man, b)
            if choice == "local":
                _push(cl, pid, plat, gid, b, int(srv.get("revision") or 0), savebundles.content_hash(b.files), res)
            elif choice == "remote" and srv.get("download_url"):
                _download(cl, pid, plat, gid, b, srv)
                res.downloaded += 1
    log.info("zapisy %s/%s: konflikt rozstrzygnięty — %s", plat, gid, choice)
    return res


def after(cfg: dict, pid: int, bundles: list, game: dict, skip: set | None = None) -> Result:
    """Po grze: zmienione paczki na serwer. Bez połączenia — oznaczone do wysłania (retry).
    Serwer ma nowszą wersję (inne urządzenie w międzyczasie) — konflikt przy następnej grze."""
    res = Result()
    plat, gid = game_ids(game)
    todo = []
    for b in bundles:
        if b.key in (skip or set()):
            continue
        b.collect()
        local = savebundles.content_hash(b.files)
        st = _state(pid, plat, gid, b)
        if local and local != (st or {}).get("content"):
            todo.append((b, local, (st or {}).get("revision", 0)))
    if not todo:
        return res
    try:
        cl = _Client(cfg, pid)
    except Offline as ex:
        res.offline = str(ex)
        for b, _l, _r in todo:
            _mark_dirty(pid, plat, gid, b)
        return res
    with _lock:
        for b, local, base in todo:
            try:
                _push(cl, pid, plat, gid, b, base, local, res)
            except Offline as ex:
                res.offline = str(ex)
                _mark_dirty(pid, plat, gid, b)
    for c in res.conflicts:
        log.info("zapisy %s/%s %s: na serwerze nowsza wersja z innego urządzenia — decyzja przy następnej grze",
                 plat, gid, c.bundle.key)
    return res


def retry(cfg: dict) -> int:
    """Niewysłane zmiany (gra bez połączenia) → serwer. Liczba wysłanych paczek."""
    rows = [dict(r) for r in _db().execute("SELECT * FROM save_sync WHERE dirty=1")]
    sent = 0
    for r in rows:
        try:
            b = savebundles.from_spec(json.loads(r["spec"]))
        except (ValueError, KeyError, OSError):
            continue
        local = savebundles.content_hash(b.files)
        if not local or local == r["content"]:
            continue
        try:
            cl = _Client(cfg, r["profile_id"])
            with _lock:
                out = _upload(cl, r["platform"], r["game"], b, r["revision"])
            _remember(r["profile_id"], r["platform"], r["game"], b, out["revision"], local)
            sent += 1
        except ConflictError:
            continue                     # decyzja gracza przy następnej grze
        except Offline as ex:
            log.info("zapisy do wysłania czekają: %s", ex)
            break
    return sent


def status(cfg: dict, pid: int, game: dict) -> dict:
    """Stan synchronizacji gry dla opcji gry: {state: synced|pending|never|offline, bundles}."""
    plat, gid = game_ids(game)
    rows = [dict(r) for r in _db().execute(
        "SELECT emu, core, kind, revision, dirty, updated FROM save_sync WHERE profile_id=? AND platform=? AND game=?",
        (pid, plat, gid))]
    state = "never" if not rows else "pending" if any(r["dirty"] for r in rows) else "synced"
    return {"state": state, "bundles": rows, "last": max((r["updated"] for r in rows), default=0)}
