"""profiles — profile graczy: osobne save'y i stany emulatorów, synchronizacja z NAS.

Zapis gry trafia tam, gdzie emulator ma swój folder save'ów (memcards, sstates…).
Tego folderu nie przestawiamy w ustawieniach emulatora — zamieniamy go na
junction (dowiązanie katalogu) wskazujący folder bieżącego profilu:

    D:\\emu\\emulatory\\DuckStation\\memcards  →  <EmuStart>\\profiles\\<id>\\duckstation\\memcards

Pierwsze podpięcie przenosi dotychczasowe save'y do PIERWSZEGO profilu (id
najmniejsze), więc nic nie ginie, a emulator uruchomiony poza EmuStart widzi
save'y ostatnio grającego profilu.

Ustawienia profilu (kopie plików, nie dowiązania — emulator czyta je ze swojego
folderu): profiles\\<id>\\settings\\<emulator>\\… — przed grą wgrywane do emulatora
(z zachowaniem wartości zależnych od komputera: ścieżki, karta grafiki, audio),
po grze zbierane z powrotem. Do tego emustart.json (wygląd, ustawienia EmuStart)
i retroachievements.json (konto RA: nazwa + token, bez hasła).

NAS: <profiles_nas>\\<nazwa NAS profilu>\\save\\<emulator>\\<folder>\\…
     <profiles_nas>\\<nazwa NAS profilu>\\settings\\<emulator>\\…
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


def _merge_move(src: Path, dst: Path, conflicts: Path | None = None) -> None:
    """Przenosi zawartość src do dst; pliki istniejące w dst zostają. Konflikt:
    plik z src trafia do `conflicts` (kopia zapasowa), a gdy jej brak — obok,
    z dopiskiem w nazwie."""
    dst.mkdir(parents=True, exist_ok=True)
    for item in list(src.iterdir()):
        target = dst / item.name
        if target.exists():
            if item.is_dir() and target.is_dir():
                _merge_move(item, target, conflicts / item.name if conflicts else None)
                continue
            if conflicts:
                conflicts.mkdir(parents=True, exist_ok=True)
                target = conflicts / item.name
                log.info("konflikt: %s już jest w profilu — stara wersja w %s", item.name, conflicts)
            else:
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
        # pierwszy raz (albo emulator zainstalowany od nowa): dotychczasowe save'y
        # należą do pierwszego profilu; pliki, które profil już ma (np. pusta
        # karta pamięci założona przez świeży emulator), idą do kopii zapasowej
        fid = first_id()
        owner = local_dir(fid, family, emu_dir.name)
        log.info("przenoszę save'y %s → %s", emu_dir, owner)
        _merge_move(emu_dir, owner, LOCAL / str(fid) / "_backup" / time.strftime("%Y%m%d-%H%M%S")
                    / "emulator" / family / emu_dir.name)
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


# ── profile z NAS (czysta instalacja / drugi komputer) ──

def import_from_nas(cfg: dict) -> list:
    """Zakłada profile dla folderów na NAS, których nie ma w bazie. Zwraca nazwy."""
    root = nas_root(cfg)
    try:
        if not root.is_dir():
            return []
        dirs = [d for d in root.iterdir() if d.is_dir() and not d.name.startswith((".", "_"))]
    except OSError:
        return []
    have = {p["nas_name"].lower() for p in all_profiles()} | {p["name"].lower() for p in all_profiles()}
    added = []
    for d in sorted(dirs, key=lambda x: x.name.lower()):
        if d.name.lower() in have:
            continue
        if not any((d / sub).exists() for sub in ("save", "settings", "emustart.json", "retroachievements.json")):
            continue
        try:
            with library.db() as c:
                c.execute("INSERT INTO profiles(name, nas_name, color) VALUES(?,?,?)", (d.name[:32], d.name, ""))
            added.append(d.name)
        except Exception:
            log.exception("profil z NAS %s", d.name)
    if added:
        log.info("profile z NAS: %s", ", ".join(added))
    return added


# ── małe pliki profilu (emustart.json, retroachievements.json): nowszy wygrywa ──

def _json_paths(cfg: dict, pid: int, name: str):
    prof = get(pid)
    local = LOCAL / str(pid) / name
    nas = _nas_dir(cfg, prof) / name if prof else None
    return local, nas


def json_get(cfg: dict, pid: int, name: str) -> dict | None:
    local, nas = _json_paths(cfg, pid, name)
    best = None
    for f in (local, nas):
        try:
            if f and f.is_file():
                mt = f.stat().st_mtime
                if best is None or mt > best[0] + 1:
                    best = (mt, f)
        except OSError:
            pass
    if not best:
        return None
    try:
        data = json.loads(best[1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if best[1] != local:                     # kopia lokalna na wypadek braku NAS
        try:
            local.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(best[1], local)
        except OSError:
            pass
    return data if isinstance(data, dict) else None


def json_set(cfg: dict, pid: int, name: str, data: dict) -> None:
    local, nas = _json_paths(cfg, pid, name)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(text, encoding="utf-8")
    if nas and nas_online(cfg):
        try:
            nas.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, nas)
        except OSError as ex:
            log.warning("zapis %s na NAS: %s", name, ex)


# ── ustawienia emulatorów per profil ──

def _settings_local(pid: int, family: str) -> Path:
    return LOCAL / str(pid) / "settings" / family


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def _files_under(base: Path, rels: list):
    """(ścieżka względna, plik) dla plików i zawartości folderów z listy."""
    for rel in rels:
        p = base / rel
        if p.is_file():
            yield Path(rel), p
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and not f.name.endswith(".emustart-tmp"):
                    yield f.relative_to(base), f


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".emustart-tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def settings_load(cfg: dict, pid: int, adapter) -> int:
    """Przed grą: ustawienia profilu (NAS → lokalnie → emulator). Profil bez
    zapisanych ustawień gra na bieżących (po grze staną się jego)."""
    from emustart import ingame
    family, base, rels = adapter.family, adapter.settings_base(), adapter.settings_files()
    if not rels:
        return 0
    snap = _settings_local(pid, family)
    prof = get(pid)
    if prof and nas_online(cfg):
        _copy_newer(_nas_dir(cfg, prof) / "settings" / family, snap,
                    LOCAL / str(pid) / "_backup" / time.strftime("%Y%m%d-%H%M%S") / "settings" / family)
    files = list(_files_under(snap, rels))
    if not files:
        return 0
    # wartości tego komputera (ścieżki, GPU, audio) zostają
    keep = {rel: ingame.ini_pick(base / rel, rules) for rel, rules in adapter.machine_keys().items()}
    machine_bak = LOCAL / "_machine" / family
    n = 0
    for rel, f in files:
        dst = base / rel
        if dst.is_file() and _same_file(f, dst):
            continue
        if dst.is_file():
            _copy_file(dst, machine_bak / rel)  # ostatnia wersja sprzed podmiany
        _copy_file(f, dst)
        n += 1
    for rel, vals in keep.items():
        if vals and (base / rel).is_file():
            cur = ingame.ini_pick(base / rel, [(re.escape(s or ""), re.escape(k)) for s, k in vals])
            diff = {k: v for k, v in vals.items() if cur.get(k) != v}
            if diff:
                ingame.ini_set(base / rel, {((s or None), k): v for (s, k), v in diff.items()})
    if n:
        log.info("ustawienia profilu %s → %s: %d plików", pid, family, n)
    return n


def settings_save(cfg: dict, pid: int, adapter) -> int:
    """Po grze: ustawienia emulatora → profil (i NAS)."""
    family, base, rels = adapter.family, adapter.settings_base(), adapter.settings_files()
    if not rels:
        return 0
    snap = _settings_local(pid, family)
    n = 0
    for rel, f in _files_under(base, rels):
        dst = snap / rel
        if dst.is_file() and _same_file(f, dst):
            continue
        _copy_file(f, dst)
        n += 1
    prof = get(pid)
    if prof and nas_online(cfg):
        _copy_newer(snap, _nas_dir(cfg, prof) / "settings" / family, None)
    return n


# ── RetroAchievements ──

RA_FILE = "retroachievements.json"


def ra_get(cfg: dict, pid: int) -> dict | None:
    """{"user", "token", "hardcore"}; {"user": ""} = świadomie bez konta;
    None = jeszcze nie ustawiono."""
    return json_get(cfg, pid, RA_FILE)


def ra_set(cfg: dict, pid: int, data: dict) -> None:
    json_set(cfg, pid, RA_FILE, {"user": data.get("user", ""), "token": data.get("token", ""),
                                 "hardcore": bool(data.get("hardcore"))})


def ra_for_launch(cfg: dict, pid: int, adapter) -> dict:
    """Konto RA profilu na tę grę. Pierwszy profil bez zapisanego wyboru przejmuje
    konto zalogowane w emulatorze (żeby nie zgubić istniejącego logowania)."""
    rec = ra_get(cfg, pid)
    if rec is None and pid == first_id():
        found = adapter.read_cheevos()
        if found:
            rec = {**found, "hardcore": False}
            ra_set(cfg, pid, rec)
            log.info("RetroAchievements: konto %s z %s przypisane do profilu %s", found["user"], adapter.family, pid)
    return rec or {"user": "", "token": ""}
