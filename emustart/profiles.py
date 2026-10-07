"""profiles — profile graczy: osobne save'y i stany emulatorów, synchronizacja z NAS.

Zapis gry trafia tam, gdzie emulator ma swój folder save'ów (memcards, sstates…).
Tego folderu nie przestawiamy w ustawieniach emulatora — zamieniamy go na
junction (dowiązanie katalogu) wskazujący folder bieżącego profilu:

    D:\\emu\\emulatory\\DuckStation\\memcards  →  <EmuStart>\\profiles\\<id>\\duckstation\\memcards

Pierwsze podpięcie przenosi dotychczasowe save'y do PIERWSZEGO profilu (id
najmniejsze), więc nic nie ginie, a emulator uruchomiony poza EmuStart widzi
save'y ostatnio grającego profilu.

NAS: <profiles_nas>\\<nazwa NAS profilu>\\save\\<emulator>\\<folder>\\…
  przed grą: nowsze pliki z NAS → lokalnie, po grze: nowsze lokalne → NAS.
  Nadpisywany plik lokalny trafia najpierw do profiles\\<id>\\_backup\\<czas>\\.
Blokada: <profil>\\lock z nazwą komputera — ten sam profil nie gra równocześnie
na dwóch komputerach (blokada starsza niż 12 h jest ignorowana).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import time
from pathlib import Path

from emustart import library, paths

log = logging.getLogger("emustart.profiles")

LOCK_MAX_AGE = 12 * 3600
LOCAL = paths.APP / "profiles"


# ── lista profili ──

def _ensure_columns() -> None:
    con = library.db()
    cols = {r[1] for r in con.execute("PRAGMA table_info(profiles)")}
    if "nas_name" not in cols:
        with con:
            con.execute("ALTER TABLE profiles ADD COLUMN nas_name TEXT NOT NULL DEFAULT ''")
            con.execute("ALTER TABLE profiles ADD COLUMN color TEXT NOT NULL DEFAULT ''")
            con.execute("UPDATE profiles SET nas_name=name WHERE nas_name=''")


def all_profiles() -> list:
    _ensure_columns()
    return [dict(r) for r in library.db().execute("SELECT * FROM profiles ORDER BY id")]


def get(pid: int) -> dict | None:
    _ensure_columns()
    r = library.db().execute("SELECT * FROM profiles WHERE id=?", (pid,)).fetchone()
    return dict(r) if r else None


def _safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .") or "profil"


def create(name: str, color: str = "") -> dict:
    _ensure_columns()
    name = name.strip()[:32]
    if not name:
        raise ValueError("Pusta nazwa profilu.")
    with library.db() as c:
        if c.execute("SELECT 1 FROM profiles WHERE name=?", (name,)).fetchone():
            raise ValueError(f"Profil „{name}” już istnieje.")
        cur = c.execute("INSERT INTO profiles(name, nas_name, color) VALUES(?,?,?)",
                        (name, _safe_name(name), color))
    return get(cur.lastrowid)


def rename(pid: int, name: str) -> None:
    """Zmienia nazwę wyświetlaną; folder na NAS zostaje (nas_name), żeby nie
    rozjechać synchronizacji z innymi komputerami."""
    name = name.strip()[:32]
    if not name:
        raise ValueError("Pusta nazwa profilu.")
    with library.db() as c:
        c.execute("UPDATE profiles SET name=? WHERE id=?", (name, pid))


def delete(pid: int) -> None:
    """Usuwa profil z listy. Pliki save'ów zostają na dysku i NAS-ie."""
    if len(all_profiles()) <= 1:
        raise ValueError("Nie można usunąć ostatniego profilu.")
    with library.db() as c:
        c.execute("DELETE FROM profiles WHERE id=?", (pid,))
        c.execute("DELETE FROM play WHERE profile_id=?", (pid,))
        c.execute("DELETE FROM resume WHERE profile_id=?", (pid,))


def first_id() -> int:
    return all_profiles()[0]["id"]


# ── save'y: junction na folder profilu ──

def local_dir(pid: int, family: str, name: str) -> Path:
    return LOCAL / str(pid) / family / name


def _is_junction(p: Path) -> bool:
    try:
        return os.path.isjunction(p)
    except OSError:
        return False


def _junction_target(p: Path) -> Path | None:
    try:
        t = os.readlink(p)
    except OSError:
        return None
    return Path(t[4:] if t.startswith("\\\\?\\") else t)


def _make_junction(link: Path, target: Path) -> None:
    import _winapi
    target.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(target), str(link))


def _merge_move(src: Path, dst: Path) -> None:
    """Przenosi zawartość src do dst; pliki istniejące w dst zostają (konflikt =
    zachowujemy oba, nowy dostaje dopisek)."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if target.exists():
            if item.is_dir() and target.is_dir():
                _merge_move(item, target)
                continue
            target = dst / f"{item.stem}.emustart-{int(time.time())}{item.suffix}"
        shutil.move(str(item), str(target))
    src.rmdir()


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)).rstrip("\\") == os.path.normcase(str(b)).rstrip("\\")


def _adopt_old_store(cur: Path, family: str, name: str) -> None:
    """Dowiązanie wskazuje magazyn profili z innej lokalizacji EmuStart (program
    przeniesiony do innego folderu): przenosimy cały stary magazyn do bieżącego,
    zamiast zaczynać od pustych save'ów."""
    try:
        old_root = cur.parents[2]            # <stary>\profiles\<id>\<emulator>\<folder>
    except IndexError:
        return
    if (cur.parent.name != family or cur.name != name or old_root.name.lower() != "profiles"
            or _same(old_root, LOCAL) or not old_root.is_dir()):
        return
    log.info("przenoszę magazyn profili %s → %s", old_root, LOCAL)
    LOCAL.mkdir(parents=True, exist_ok=True)
    _merge_move(old_root, LOCAL)


def attach(pid: int, family: str, emu_dir: Path) -> None:
    """Podpina folder save'ów emulatora pod profil `pid`."""
    target = local_dir(pid, family, emu_dir.name)
    if _is_junction(emu_dir):
        cur = _junction_target(emu_dir)
        if cur and _same(cur, target):
            return
        if cur:
            _adopt_old_store(cur, family, emu_dir.name)
        os.rmdir(emu_dir)                    # usuwa samo dowiązanie, nie zawartość
    elif emu_dir.is_dir():
        # pierwszy raz: dotychczasowe save'y należą do pierwszego profilu
        owner = local_dir(first_id(), family, emu_dir.name)
        log.info("przenoszę save'y %s → %s", emu_dir, owner)
        _merge_move(emu_dir, owner)
    elif emu_dir.exists():
        raise OSError(f"{emu_dir} nie jest katalogiem")
    emu_dir.parent.mkdir(parents=True, exist_ok=True)
    _make_junction(emu_dir, target)


# ── NAS ──

def nas_root(cfg: dict) -> Path:
    return Path(cfg.get("profiles_nas") or r"Z:\emustart\Profiles")


def _nas_dir(cfg: dict, prof: dict) -> Path:
    return nas_root(cfg) / prof["nas_name"]


def nas_online(cfg: dict) -> bool:
    root = nas_root(cfg)
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root.is_dir()
    except OSError:
        return False


def _copy_newer(src: Path, dst: Path, backup: Path | None) -> int:
    """Kopiuje pliki, które w src są nowsze (albo ich brak w dst). Zwraca liczbę."""
    n = 0
    if not src.is_dir():
        return 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        d = dst / rel
        try:
            st = f.stat()
            if d.exists():
                dt = d.stat()
                if dt.st_mtime >= st.st_mtime - 2 and dt.st_size == st.st_size:
                    continue
                if dt.st_mtime > st.st_mtime + 2:
                    continue                 # cel nowszy — nie cofamy save'a
                if backup:
                    b = backup / rel
                    b.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(d, b)
            d.parent.mkdir(parents=True, exist_ok=True)
            tmp = d.with_name(d.name + ".emustart-tmp")
            shutil.copy2(f, tmp)
            os.replace(tmp, d)
            n += 1
        except OSError as ex:
            log.warning("sync %s: %s", f, ex)
    return n


def sync_down(cfg: dict, pid: int, family: str, names: list) -> int:
    prof = get(pid)
    if not prof or not nas_online(cfg):
        return 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 0
    for name in names:
        n += _copy_newer(_nas_dir(cfg, prof) / "save" / family / name, local_dir(pid, family, name),
                         LOCAL / str(pid) / "_backup" / stamp / family / name)
    return n


def sync_up(cfg: dict, pid: int, family: str, names: list) -> int:
    prof = get(pid)
    if not prof or not nas_online(cfg):
        _pending(pid, family, names, add=True)
        return 0
    n = 0
    for name in names:
        n += _copy_newer(local_dir(pid, family, name), _nas_dir(cfg, prof) / "save" / family / name, None)
    _pending(pid, family, names, add=False)
    return n


def _pending(pid: int, family: str, names: list, add: bool) -> None:
    key = "sync_pending"
    data = json.loads(library.meta_get(key, "{}"))
    k = f"{pid}|{family}"
    if add:
        data[k] = sorted(set(data.get(k, [])) | set(names))
    else:
        data.pop(k, None)
    library.meta_set(key, json.dumps(data))


def sync_pending(cfg: dict) -> int:
    """Wysyła save'y, których nie udało się wysłać (NAS był niedostępny)."""
    data = json.loads(library.meta_get("sync_pending", "{}"))
    n = 0
    for k, names in data.items():
        pid, family = k.split("|", 1)
        n += sync_up(cfg, int(pid), family, names)
    return n


# ── blokada ──

def lock(cfg: dict, pid: int) -> str:
    """'' = zablokowano; inaczej nazwa komputera, na którym profil właśnie gra."""
    prof = get(pid)
    if not prof or not nas_online(cfg):
        return ""
    f = _nas_dir(cfg, prof) / "lock"
    me = socket.gethostname()
    try:
        cur = json.loads(f.read_text(encoding="utf-8"))
        if cur.get("host") != me and time.time() - cur.get("time", 0) < LOCK_MAX_AGE:
            return cur.get("host", "?")
    except (OSError, ValueError):
        pass
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"host": me, "time": time.time()}), encoding="utf-8")
    return ""


def unlock(cfg: dict, pid: int) -> None:
    prof = get(pid)
    if not prof:
        return
    f = _nas_dir(cfg, prof) / "lock"
    try:
        if json.loads(f.read_text(encoding="utf-8")).get("host") == socket.gethostname():
            f.unlink()
    except (OSError, ValueError):
        pass
