"""nasfs — folder profili na NAS-ie: przez serwer EmuStart (HTTP) albo przez SMB.

Ścieżki względem korzenia profili (`profiles_nas`, np. Z:\\emustart\\Profiles), zapisane
z „/”: `Ania/save/pcsx2/memcards/Mcd001.ps2`.

Przez serwer kopia zapasowa nadpisywanego pliku powstaje na serwerze (przez SMB
szła najpierw do komputera i z powrotem), a pliki idą skompresowane zstd (pusta
karta pamięci PS2 8 MB → kilkaset bajtów). Serwer wybieramy tylko wtedy, gdy jego
folder profili to ten sam folder, który widzimy przez SMB (znacznik `.emustart-root`),
albo gdy SMB jest niedostępny.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import shutil
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

log = logging.getLogger("emustart.nasfs")

ROOT_MARK = ".emustart-root"
NAS_BACKUPS = 10
ZSTD_MIN = 4096                 # mniejsze pliki bez kompresji
MAX_BODY = 1 << 30


def _zstd():
    try:
        from compression import zstd
        return zstd
    except ImportError:          # Python < 3.14
        return None


def clean_rel(rel: str) -> str:
    """Ścieżka względna bez wyjścia poza korzeń: bez „..”, dysków, ścieżek bezwzględnych."""
    parts = []
    for p in str(rel).replace("\\", "/").split("/"):
        if p in ("", "."):
            continue
        if p == ".." or ":" in p or p.startswith("~"):
            raise ValueError(f"niedozwolona ścieżka: {rel}")
        parts.append(p)
    return "/".join(parts)


# ── wspólne operacje na lokalnym folderze (SMB po stronie klienta, dysk po stronie serwera) ──

def scan_dir(root: Path) -> dict:
    """{ścieżka względna „/”: (rozmiar, czas)} — os.scandir (na Windows rozmiar i czas
    przychodzą z listą, bez zapytania o każdy plik)."""
    out, stack = {}, [root]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(Path(e.path))
                        elif e.is_file(follow_symlinks=False) and not e.name.endswith(".emustart-tmp"):
                            st = e.stat()
                            out[Path(e.path).relative_to(root).as_posix()] = (st.st_size, st.st_mtime)
                    except OSError:
                        continue
        except OSError:
            continue
    return out


def write_atomic(dst: Path, data: bytes, mtime: float | None = None) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.{threading.get_ident()}.emustart-tmp")
    tmp.write_bytes(data)
    if mtime:
        os.utime(tmp, (time.time(), mtime))
    for i in range(5):                       # plik chwilowo otwarty przez inny wątek/program
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"nie można zapisać {dst}")


def backup_copy(f: Path, bak: Path) -> None:
    if f.is_file():
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, bak)


def prune_backups(bdir: Path, keep: int = NAS_BACKUPS) -> None:
    try:
        dirs = sorted((d for d in bdir.iterdir() if d.is_dir()), key=lambda d: d.name)
    except OSError:
        return
    for d in dirs[:-keep]:
        shutil.rmtree(d, ignore_errors=True)


def backup_root(rel: str) -> Path | None:
    """`<profil>/_backup` dla ścieżki kopii zapasowej `<profil>/_backup/<czas>/…`."""
    parts = rel.split("/")
    return Path(*parts[:2]) if len(parts) >= 3 and parts[1] == "_backup" else None


def root_id(root: Path, create: bool) -> str:
    f = root / ROOT_MARK
    try:
        return f.read_text(encoding="utf-8").strip()
    except OSError:
        if not create:
            return ""
    try:
        root.mkdir(parents=True, exist_ok=True)
        v = uuid.uuid4().hex
        f.write_text(v, encoding="utf-8")
        return v
    except OSError:
        return ""


class SmbFs:
    """Folder profili przez SMB (albo dysk lokalny)."""

    name = "NAS"

    def __init__(self, root: Path):
        self.root = Path(root)

    def _p(self, rel: str) -> Path:
        r = clean_rel(rel)
        return self.root / r if r else self.root

    def online(self) -> bool:
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            return self.root.is_dir()
        except OSError:
            return False

    def scan(self, rel: str) -> dict:
        return scan_dir(self._p(rel))

    def listdir(self, rel: str) -> list:
        p = self._p(rel)
        try:
            return [(e.name, e.is_dir()) for e in os.scandir(p)]
        except OSError:
            return []

    def stat(self, rel: str):
        try:
            st = self._p(rel).stat()
            return (st.st_size, st.st_mtime)
        except OSError:
            return None

    def exists(self, rel: str) -> bool:
        return self._p(rel).exists()

    def read(self, rel: str) -> bytes | None:
        try:
            return self._p(rel).read_bytes()
        except OSError:
            return None

    def write(self, rel: str, data: bytes, mtime: float | None = None) -> None:
        write_atomic(self._p(rel), data, mtime)

    def unlink(self, rel: str) -> None:
        self._p(rel).unlink(missing_ok=True)

    def get_file(self, rel: str, local: Path) -> bool:
        src = self._p(rel)
        if not src.is_file():
            return False
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".emustart-tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, local)
        return True

    def put_file(self, local: Path, rel: str, backup: str = "") -> None:
        dst = self._p(rel)
        if backup:
            backup_copy(dst, self._p(backup))
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".emustart-tmp")
        shutil.copy2(local, tmp)
        os.replace(tmp, dst)

    def prune(self, rel: str) -> None:
        prune_backups(self._p(rel))


class RemoteFs:
    """Folder profili przez serwer EmuStart (/v1/nas/…)."""

    name = "serwer"

    def __init__(self, host: str, port: int, key: str):
        self.host, self.port, self.key = host, port, key
        self._local = threading.local()

    def _conn(self) -> http.client.HTTPConnection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = http.client.HTTPConnection(self.host, self.port, timeout=60)
            self._local.c = c
        return c

    def _req(self, method: str, path: str, q: dict, body: bytes | None = None,
             headers: dict | None = None) -> tuple[int, bytes, dict]:
        url = f"/v1/nas/{path}?" + urllib.parse.urlencode(q)
        h = {"Authorization": f"Bearer {self.key}", **(headers or {})}
        last = None
        for attempt in range(3):
            c = self._conn()
            try:
                c.request(method, url, body=body, headers=h)
                r = c.getresponse()
                data = r.read()
                if r.will_close:
                    c.close()
                    self._local.c = None
                return r.status, data, {k.lower(): v for k, v in r.getheaders()}
            except (OSError, http.client.HTTPException) as ex:
                last = ex
                c.close()
                self._local.c = None
                time.sleep(0.2 * (attempt + 1))
        raise OSError(f"serwer {self.host}: {last}")

    def _json(self, path: str, q: dict):
        st, data, _h = self._req("GET", path, q)
        if st == 404:
            return None
        if st != 200:
            raise OSError(f"serwer: {path} {st} {data[:200]!r}")
        return json.loads(data)

    def online(self) -> bool:
        try:
            return self._json("list", {"p": ""}) is not None
        except OSError:
            return False

    def scan(self, rel: str) -> dict:
        d = self._json("scan", {"p": clean_rel(rel)}) or {}
        return {k: tuple(v) for k, v in (d.get("files") or {}).items()}

    def listdir(self, rel: str) -> list:
        d = self._json("list", {"p": clean_rel(rel)}) or {}
        return [tuple(e) for e in d.get("entries") or []]

    def stat(self, rel: str):
        d = self._json("stat", {"p": clean_rel(rel)})
        return (d["size"], d["mtime"]) if d else None

    def exists(self, rel: str) -> bool:
        d = self._json("stat", {"p": clean_rel(rel)})
        return d is not None

    def _get(self, rel: str):
        z = _zstd()
        st, data, h = self._req("GET", "file", {"p": clean_rel(rel)},
                                headers={"Accept-Encoding": "zstd"} if z else None)
        if st == 404:
            return None, None
        if st != 200:
            raise OSError(f"serwer: odczyt {rel}: {st}")
        if h.get("content-encoding") == "zstd":
            data = z.decompress(data)
        return data, float(h.get("x-mtime") or 0) or None

    def read(self, rel: str) -> bytes | None:
        return self._get(rel)[0]

    def write(self, rel: str, data: bytes, mtime: float | None = None, backup: str = "") -> None:
        q = {"p": clean_rel(rel)}
        if mtime:
            q["mtime"] = repr(float(mtime))
        if backup:
            q["backup"] = clean_rel(backup)
        headers = {}
        z = _zstd()
        if z and len(data) >= ZSTD_MIN:
            packed = z.compress(data, 3)
            if len(packed) < len(data):
                data = packed
                headers["Content-Encoding"] = "zstd"
        st, body, _h = self._req("PUT", "file", q, body=data, headers=headers)
        if st != 200:
            raise OSError(f"serwer: zapis {rel}: {st} {body[:200]!r}")

    def unlink(self, rel: str) -> None:
        st, body, _h = self._req("DELETE", "file", {"p": clean_rel(rel)})
        if st not in (200, 404):
            raise OSError(f"serwer: usuwanie {rel}: {st}")

    def get_file(self, rel: str, local: Path) -> bool:
        data, mtime = self._get(rel)
        if data is None:
            return False
        write_atomic(local, data, mtime)
        return True

    def put_file(self, local: Path, rel: str, backup: str = "") -> None:
        self.write(rel, local.read_bytes(), local.stat().st_mtime, backup)

    def prune(self, rel: str) -> None:
        pass                    # serwer przycina kopie zapasowe sam po każdym zapisie z kopią


# ── wybór: serwer albo SMB ──

_choice: dict = {}
_choice_lock = threading.Lock()


def backend(cfg: dict):
    """RemoteFs, gdy serwer EmuStart udostępnia ten sam folder profili; inaczej SmbFs."""
    from emustart import netsrc
    from emustart import profiles
    smb = SmbFs(profiles.nas_root(cfg))
    ep = netsrc.endpoint(cfg)
    if not ep or cfg.get("server_profiles", True) is False:
        return smb
    info = netsrc.info(cfg)
    if not info.get("ok") or "nas" not in info.get("features", []) or not info.get("nas_id"):
        return smb
    key = (ep, info["nas_id"], str(smb.root))
    with _choice_lock:
        hit = _choice.get(key)
        if hit and time.monotonic() - hit[0] < 600:
            return hit[1]
    # ten sam folder? Serwer zakłada znacznik w swoim folderze profili — gdy SMB jest
    # dostępny, ten sam znacznik musi być widoczny przez SMB (inaczej dwa różne foldery)
    reachable = smb.root.is_dir()
    mine = root_id(smb.root, create=False) if reachable else ""
    if reachable and mine != info["nas_id"]:
        log.warning("folder profili serwera (%s) to nie %s — synchronizacja przez SMB", info.get("name"), smb.root)
        fs = smb
    else:
        fs = RemoteFs(*ep)
    with _choice_lock:
        _choice[key] = (time.monotonic(), fs)
    return fs


def reset() -> None:
    with _choice_lock:
        _choice.clear()
