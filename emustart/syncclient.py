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
    # konto, na które paczka należy: niewysłane zapisy poprzedniego konta nie trafią na nowe
    if _schema.get(id(c)) is not c:
        library.add_columns(c, "save_sync", [("account", "TEXT NOT NULL DEFAULT ''")])
        _schema[id(c)] = c
    return c


_schema: dict = {}


def _account_of(pid: int) -> str:
    from emustart import profiles
    prof = profiles.get(pid) or {}
    return prof.get("nas_name") or prof.get("name") or ""


def _state(pid: int, plat: str, gid: str, b) -> dict | None:
    r = _db().execute("SELECT * FROM save_sync WHERE profile_id=? AND platform=? AND game=? AND emu=? "
                      "AND core=? AND kind=?", (pid, plat, gid, *b.key)).fetchone()
    return dict(r) if r else None


def _remember(pid: int, plat: str, gid: str, b, revision: int, content: str, dirty: bool = False,
              account: str = "") -> None:
    st = _state(pid, plat, gid, b)
    account = account or (st or {}).get("account") or _account_of(pid)
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO save_sync(profile_id, platform, game, emu, core, kind, revision, content, "
                  "dirty, spec, updated, account) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (pid, plat, gid, *b.key, int(revision), content, int(dirty), json.dumps(b.spec()), time.time(),
                   account))


def _mark_dirty(pid: int, plat: str, gid: str, b) -> None:
    st = _state(pid, plat, gid, b)
    _remember(pid, plat, gid, b, (st or {}).get("revision", 0), (st or {}).get("content", ""), True)


def game_ids(game: dict) -> tuple[str, str]:
    from emustart import savesync
    return str(game.get("es") or "").lower(), savesync.game_id(game.get("es", ""), game.get("name", ""))


# ── serwer ──

class _Client:
    def __init__(self, cfg: dict, pid: int, account: str = ""):
        from emustart import netsrc, profiles
        self.cfg = cfg
        ep = netsrc.endpoint(cfg)
        if not ep:
            raise Offline("serwer nie jest ustawiony")
        self.ep = ep
        prof = profiles.get(pid) or {}
        self.account = account or prof.get("nas_name") or prof.get("name") or ""
        if account and account.lower() != (prof.get("nas_name") or "").lower():
            prof = {}                        # paczka innego konta niż profil — tylko z tokenem tamtego konta
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


def _merge_states(cl, pid, plat, gid, b, srv: dict, res: Result) -> None:
    """Stany zmienione tu i na innym urządzeniu: suma slotów (savebundles.merge_states) —
    nic nie znika, kolizja tego samego slotu: starszy stan dostaje nowy numer. Wynik na
    urządzenie i na serwer (bez pytania gracza — stany się sumują, nie wykluczają)."""
    zp = _fetch(cl, srv)
    try:
        with zipfile.ZipFile(zp) as z:
            other = {}
            for i in z.infolist():
                if not i.is_dir():
                    other[i.filename] = (hashlib.sha256(z.read(i)).hexdigest(), time.mktime(i.date_time + (0, 0, -1)))
            mine = {arc: (savebundles.file_sha(Path(p)), Path(p).stat().st_mtime) for p, arc in b.files}
            merged, moves = savebundles.merge_states(mine, other)
            paths = {arc: Path(p) for p, arc in b.files}
            tmp = Path(tempfile.mkdtemp(prefix="emustart-stany-"))
            try:
                final = {}
                for arc, (src, name) in merged.items():
                    t = tmp / arc
                    if src == "mine":
                        shutil.copy2(paths[name], t)
                    else:
                        t.write_bytes(z.read(name))
                        ts = other[name][1]
                        os.utime(t, (ts, ts))
                    final[arc] = t
                if b.files:
                    bak = _backup_dir(pid) / time.strftime("%Y%m%d-%H%M%S") / f"{b.emulator_id}-{b.core_id or '-'}-{b.kind}"
                    bak.mkdir(parents=True, exist_ok=True)
                    for p, _arc in b.files:
                        shutil.copy2(p, bak / Path(p).name)
                    _prune_backups(pid)
                b.folder.mkdir(parents=True, exist_ok=True)
                for arc, t in final.items():
                    dst = paths.get(arc) or b.local_path(arc)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    new = dst.with_name(dst.name + ".emustart-new")
                    shutil.copy2(t, new)
                    os.replace(new, dst)
                for arc, p in paths.items():
                    if arc not in final:
                        p.unlink(missing_ok=True)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        server_content = _zip_hash(zp)
    finally:
        zp.unlink(missing_ok=True)
    b.collect()
    local = savebundles.content_hash(b.files)
    res.downloaded += 1
    if moves:
        log.info("stany %s/%s %s: połączone z serwerem, %d przeniesione na nowe sloty", plat, gid, b.key, len(moves))
    if local == server_content:
        _remember(pid, plat, gid, b, int(srv.get("revision") or 0), local)
    else:
        _push(cl, pid, plat, gid, b, int(srv.get("revision") or 0), local, res)


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
        elif b.kind == "states":
            _merge_states(cl, pid, plat, gid, b, srv, res)      # stany się sumują
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
        if b.kind == "states":
            _merge_states(cl, pid, plat, gid, b, srv, res)      # stany się sumują
        else:
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
    states = [c for c in res.conflicts if c.bundle.kind == "states"]
    if states:
        try:
            man = cl.json("GET", f"/v1/savesync/{plat}/{gid}/manifest")
            with _lock:
                for c in states:
                    res.conflicts.remove(c)
                    _merge_states(cl, pid, plat, gid, c.bundle, _server_bundle(man, c.bundle), res)
        except (Offline, ConflictError, OSError, ValueError, zipfile.BadZipFile) as ex:
            log.info("stany %s/%s: połączenie odłożone (%s)", plat, gid, ex)
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
            cl = _Client(cfg, r["profile_id"], r.get("account") or "")
            with _lock:
                out = _upload(cl, r["platform"], r["game"], b, r["revision"])
            _remember(r["profile_id"], r["platform"], r["game"], b, out["revision"], local, account=cl.account)
            sent += 1
        except ConflictError:
            continue                     # decyzja gracza przy następnej grze
        except Offline as ex:
            log.info("zapisy do wysłania czekają: %s", ex)
            break
    return sent


def other_core_states(cfg: dict, pid: int, game: dict, core: str) -> list:
    """Stany tej gry z innych rdzeni RetroArcha (z serwera) — do listy „Wczytaj zapis” z
    oznaczeniem, że w bieżącym rdzeniu się nie wczytają. Paczki rozpakowane do pamięci
    podręcznej profilu (pobierane, gdy na serwerze jest nowsza wersja). [{core, path, time}]."""
    plat, gid = game_ids(game)
    try:
        cl = _Client(cfg, pid)
        man = cl.json("GET", f"/v1/savesync/{plat}/{gid}/manifest")
    except (Offline, ConflictError):
        return []
    from emustart import profiles
    out = []
    cores = ((man.get("emulators") or {}).get("retroarch") or {}).get("cores") or {}
    for c, node in sorted(cores.items()):
        b = (node.get("bundles") or {}).get("states") or {}
        if c == core or not b.get("download_url"):
            continue
        d = profiles.LOCAL / str(pid) / "_inne_rdzenie" / plat / gid / c
        mark = d / ".revision"
        try:
            have = mark.read_text(encoding="utf-8").strip()
        except OSError:
            have = ""
        if have != str(b.get("revision")):
            try:
                zp = _fetch(cl, b)
            except Offline as ex:
                log.info("stany rdzenia %s: %s", c, ex)
                continue
            try:
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(zp) as z:
                    for i in z.infolist():
                        n = Path(i.filename).name
                        if i.is_dir() or n != i.filename or n.startswith("."):
                            continue
                        dst = d / n
                        with z.open(i) as src, open(dst, "wb") as f:
                            shutil.copyfileobj(src, f, 1 << 20)
                        t = time.mktime(i.date_time + (0, 0, -1))
                        os.utime(dst, (t, t))
                mark.write_text(str(b.get("revision")), encoding="utf-8")
            finally:
                zp.unlink(missing_ok=True)
        for f in sorted(d.glob("game.state*")):
            if not f.name.endswith(".png"):
                out.append({"core": c, "path": str(f), "time": f.stat().st_mtime})
    return out


def pending(cfg: dict, account: str = "") -> list:
    """Paczki czekające na wysłanie (gra bez połączenia): [{platform, game, name, account}] —
    po jednej na grę; `account` — tylko tego konta."""
    rows = [dict(r) for r in _db().execute("SELECT * FROM save_sync WHERE dirty=1")]
    out, seen = [], set()
    names = None
    for r in rows:
        acc = r.get("account") or _account_of(r["profile_id"])
        if account and acc.lower() != account.lower():
            continue
        try:
            b = savebundles.from_spec(json.loads(r["spec"]))
        except (ValueError, KeyError, OSError):
            continue
        if savebundles.content_hash(b.files) in ("", r["content"]):
            continue
        k = (acc.lower(), r["platform"], r["game"])
        if k in seen:
            continue
        seen.add(k)
        if names is None:
            from emustart import savesync
            names = {(str(g["es"]).lower(), savesync.game_id(g["es"], g["name"])): g["title"] or g["name"]
                     for g in library.db().execute("SELECT es, name, title FROM games")}
        out.append({"platform": r["platform"], "game": r["game"], "account": acc,
                    "name": names.get((r["platform"], r["game"]), r["game"])})
    return out


def pull_all(cfg: dict, pid: int, skip: set | None = None) -> dict:
    """Po zalogowaniu i przy starcie: nowsze wersje paczek znanych na tym urządzeniu (tego
    konta), gdy lokalnie bez zmian. Konfliktów nie rozstrzyga — zostają do decyzji przy
    uruchomieniu gry. `skip` — {(platforma, gra)} gier, które właśnie działają / wysyłają."""
    account = _account_of(pid)
    out = {"downloaded": 0, "conflicts": 0, "offline": ""}
    rows = [dict(r) for r in _db().execute("SELECT * FROM save_sync WHERE profile_id=?", (pid,))]
    games: dict = {}
    for r in rows:
        if (r.get("account") or account).lower() != account.lower():
            continue                           # paczki innego konta tego profilu
        if (r["platform"], r["game"]) in (skip or set()):
            continue
        games.setdefault((r["platform"], r["game"]), []).append(r)
    if not games:
        return out
    try:
        cl = _Client(cfg, pid)
    except Offline as ex:
        out["offline"] = str(ex)
        return out
    for (plat, gid), rs in games.items():
        try:
            man = cl.json("GET", f"/v1/savesync/{plat}/{gid}/manifest")
        except Offline as ex:
            out["offline"] = str(ex)
            break
        except ConflictError:
            continue
        with _lock:
            for r in rs:
                try:
                    b = savebundles.from_spec(json.loads(r["spec"]))
                except (ValueError, KeyError, OSError):
                    continue
                srv = _server_bundle(man, b)
                rev_s = int(srv.get("revision") or 0)
                if not rev_s or rev_s == r["revision"]:
                    continue
                if savebundles.content_hash(b.files) != r["content"]:
                    out["conflicts"] += 1          # zmiany tu i tam — decyzja przy uruchomieniu gry
                    continue
                try:
                    _download(cl, pid, plat, gid, b, srv)
                    out["downloaded"] += 1
                except (Offline, OSError, ValueError, zipfile.BadZipFile) as ex:
                    log.info("zapisy %s/%s %s: %s", plat, gid, b.key, ex)
    if out["downloaded"] or out["conflicts"]:
        log.info("zapisy z serwera po zalogowaniu / starcie: pobrano %d, konfliktów %d",
                 out["downloaded"], out["conflicts"])
    return out


def status(cfg: dict, pid: int, game: dict) -> dict:
    """Stan synchronizacji gry dla opcji gry: {state: synced|pending|never|offline, bundles}."""
    plat, gid = game_ids(game)
    rows = [dict(r) for r in _db().execute(
        "SELECT emu, core, kind, revision, dirty, updated FROM save_sync WHERE profile_id=? AND platform=? AND game=?",
        (pid, plat, gid))]
    state = "never" if not rows else "pending" if any(r["dirty"] for r in rows) else "synced"
    return {"state": state, "bundles": rows, "last": max((r["updated"] for r in rows), default=0)}
