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
import threading
import time
from pathlib import Path

from emustart import library, nasfs, paths

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


def rename(pid: int, name: str, cfg: dict | None = None) -> None:
    """Zmienia nazwę wyświetlaną. Folder na NAS zostaje, gdy już istnieje (żeby nie
    rozjechać synchronizacji z innymi komputerami); profil, który jeszcze nic nie
    wysłał, dostaje folder pod nową nazwą — o ile nie zajął go ktoś inny."""
    name = name.strip()[:32]
    if not name:
        raise ValueError("Pusta nazwa profilu.")
    prof = get(pid)
    with library.db() as c:
        if c.execute("SELECT 1 FROM profiles WHERE name=? AND id!=?", (name, pid)).fetchone():
            raise ValueError(f"Profil „{name}” już istnieje.")
        c.execute("UPDATE profiles SET name=? WHERE id=?", (name, pid))
    if cfg is not None and prof and nas_online(cfg):
        new = _safe_name(name)
        fs = nasfs.backend(cfg)
        taken = library.db().execute("SELECT 1 FROM profiles WHERE nas_name=? AND id!=?", (new, pid)).fetchone()
        if not fs.exists(prof["nas_name"]) and not fs.exists(new) and not taken:
            with library.db() as c:
                c.execute("UPDATE profiles SET nas_name=? WHERE id=?", (new, pid))


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


# ── profil tego komputera ──
# Save'y i konto RA zastane w emulatorach na tym komputerze należą do profilu
# wskazanego przy pierwszym uruchomieniu (nie do „pierwszego z listy” — na drugim
# komputerze pierwszy z listy to zwykle profil kogoś innego, z NAS).

def machine_owner() -> int:
    try:
        pid = int(library.meta_get("machine_profile", "0"))
    except ValueError:
        pid = 0
    return pid if get(pid) else first_id()


def set_machine_owner(pid: int) -> None:
    library.meta_set("machine_profile", str(int(pid)))


def setup_needed() -> bool:
    return library.meta_get("machine_setup", "") != "1"


def finish_setup(pid: int, ask: bool) -> None:
    set_machine_owner(pid)
    library.meta_set("ask_profile", "1" if ask else "0")
    library.meta_set("machine_setup", "1")


def ask_at_start() -> bool:
    v = library.meta_get("ask_profile", "")
    return v == "1" if v else len(all_profiles()) > 1


def adopt_existing_install() -> bool:
    """Aktualizacja ze starszej wersji: ten komputer już grał — bez pytania,
    właścicielem zostaje ostatnio grający profil."""
    if not setup_needed():
        return False
    used = library.meta_get("last_profile", "") or (LOCAL.is_dir() and any(LOCAL.iterdir()))
    if not used:
        return False
    try:
        pid = int(library.meta_get("last_profile", "0"))
    except ValueError:
        pid = 0
    set_machine_owner(pid if get(pid) else first_id())
    library.meta_set("machine_setup", "1")
    return True


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
        fid = machine_owner()
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
    """Folder profili dostępny — przez serwer EmuStart albo SMB (nasfs.backend)."""
    return nasfs.backend(cfg).online()


def via(cfg: dict) -> str:
    """„serwer” albo „NAS” — którędy idą zapisy (do komunikatów)."""
    return nasfs.backend(cfg).name


NAS_BACKUPS = 10        # tyle ostatnich kopii nadpisanych plików trzymamy na NAS


def _sig(st) -> list:
    return [st.st_size, round(st.st_mtime)]


def _same_sig(a, b) -> bool:
    return bool(a) and bool(b) and a[0] == b[0] and abs(a[1] - b[1]) <= 2


def _scan(root: Path) -> dict:
    """{ścieżka względna „/”: (rozmiar, czas)} — patrz nasfs.scan_dir."""
    return nasfs.scan_dir(root)


SKIP = (".emustart-stamp",)
PARALLEL = 4                # równoległe przesyłanie plików (przez serwer: osobne połączenia)


def _run_jobs(jobs: list) -> int:
    """jobs: [(funkcja, po_sukcesie)] — przesyłanie równolegle. Zwraca liczbę udanych
    z `po_sukcesie` (kopie przegranych wersji konfliktu, bez niego, się nie liczą)."""
    if not jobs:
        return 0
    from concurrent.futures import ThreadPoolExecutor

    def one(job):
        fn, done = job
        try:
            fn()
        except (OSError, ValueError) as ex:
            log.warning("sync: %s", ex)
            return 0
        if done:
            done()
            return 1
        return 0
    if len(jobs) == 1:
        return one(jobs[0])
    with ThreadPoolExecutor(min(PARALLEL, len(jobs)), thread_name_prefix="sync") as ex:
        return sum(ex.map(one, jobs))


def _conflict(man, key: str, s_sig: list, d_sig: list) -> bool:
    base = man.get(key) if man is not None else None
    return bool(base) and not _same_sig(base, s_sig) and not _same_sig(base, d_sig)


def _push(local: Path, fs, nrel: str, bak: str, man: dict | None = None, prefix: str = "",
          conflicts: list | None = None, dst_known: bool = False) -> int:
    """Lokalnie → NAS: pliki nowsze lokalnie (albo brakujące na NAS). Nadpisywana wersja
    na NAS trafia do kopii zapasowej `bak` (przez serwer kopię robi serwer u siebie).

    man: stan plików z ostatniej synchronizacji ({klucz: [rozmiar, czas]}) — gdy od tamtej
    pory zmieniły się OBIE strony, to konflikt: wygrywa nowszy, przegrany do `bak/konflikt`.
    dst_known: NAS nie zmienił się od ostatniej synchronizacji (znacznik) — nie listujemy go;
    pliki zgodne z manifestem pomijamy od razu."""
    if not local.is_dir():
        return 0
    src_files = _scan(local)
    dst_files = None if dst_known else fs.scan(nrel)
    jobs = []
    for rel, (s_size, s_mtime) in src_files.items():
        if rel.rsplit("/", 1)[-1] in SKIP:
            continue
        f = local / rel
        key = f"{prefix}/{rel}"
        s_sig = [s_size, round(s_mtime)]
        try:
            if dst_files is None:
                if man is not None and _same_sig(man.get(key), s_sig):
                    continue                 # bez zmian od ostatniej synchronizacji
                dinfo = fs.stat(f"{nrel}/{rel}")
            else:
                dinfo = dst_files.get(rel)
        except (OSError, ValueError) as ex:
            log.warning("sync %s: %s", f, ex)
            continue
        backup = ""
        if dinfo:
            d_size, d_mtime = dinfo
            d_sig = [d_size, round(d_mtime)]
            if d_mtime >= s_mtime - 2 and d_size == s_size:
                if man is not None:
                    man[key] = d_sig
                continue
            if _conflict(man, key, s_sig, d_sig):
                src_wins = s_mtime > d_mtime + 2
                log.warning("konflikt save'ów %s: obie wersje zmienione, zostaje nowsza (%s)",
                            key, "wysyłana" if src_wins else "z NAS")
                if conflicts is not None:
                    conflicts.append(key)
                if not src_wins:             # nasza (starsza) wersja do kopii zapasowej na NAS
                    jobs.append((lambda f=f, r=f"{bak}/konflikt/{rel}": fs.put_file(f, r), None))
                    continue
                backup = f"{bak}/konflikt/{rel}"
            elif d_mtime > s_mtime + 2:
                continue                     # NAS nowszy — nie cofamy save'a
            else:
                backup = f"{bak}/{rel}"

        def done(key=key, s_sig=s_sig):
            if man is not None:
                man[key] = s_sig
        jobs.append((lambda f=f, r=f"{nrel}/{rel}", b=backup: fs.put_file(f, r, b), done))
    return _run_jobs(jobs)


def _pull(fs, nrel: str, local: Path, bak: Path, man: dict | None = None, prefix: str = "",
          conflicts: list | None = None) -> int:
    """NAS → lokalnie: pliki nowsze na NAS. Nadpisywany plik lokalny → `bak`."""
    src_files = fs.scan(nrel)
    if not src_files:
        return 0
    dst_files = _scan(local) if local.is_dir() else {}
    jobs = []
    for rel, (s_size, s_mtime) in src_files.items():
        if rel.rsplit("/", 1)[-1] in SKIP:
            continue
        d = local / rel
        key = f"{prefix}/{rel}"
        s_sig = [s_size, round(s_mtime)]
        dinfo = dst_files.get(rel)
        try:
            if dinfo:
                d_size, d_mtime = dinfo
                d_sig = [d_size, round(d_mtime)]
                if d_mtime >= s_mtime - 2 and d_size == s_size:
                    if man is not None:
                        man[key] = d_sig
                    continue
                if _conflict(man, key, s_sig, d_sig):
                    src_wins = s_mtime > d_mtime + 2
                    log.warning("konflikt save'ów %s: obie wersje zmienione, zostaje nowsza (%s)",
                                key, "z NAS" if src_wins else "lokalna")
                    if conflicts is not None:
                        conflicts.append(key)
                    if not src_wins:         # wersja z NAS (starsza) do lokalnej kopii zapasowej
                        jobs.append((lambda r=f"{nrel}/{rel}", t=bak / "konflikt" / rel: fs.get_file(r, t), None))
                        continue
                    nasfs.backup_copy(d, bak / "konflikt" / rel)
                elif d_mtime > s_mtime + 2:
                    continue                 # lokalny nowszy — nie cofamy save'a
                else:
                    nasfs.backup_copy(d, bak / rel)
        except OSError as ex:
            log.warning("sync %s: %s", d, ex)
            continue

        def done(key=key, s_sig=s_sig):
            if man is not None:
                man[key] = s_sig
        jobs.append((lambda r=f"{nrel}/{rel}", t=d: fs.get_file(r, t), done))
    return _run_jobs(jobs)


def _manifest(pid: int) -> tuple[Path, dict]:
    f = LOCAL / str(pid) / "_sync.json"
    try:
        return f, json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return f, {}


def _manifest_save(f: Path, data: dict) -> None:
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(data), encoding="utf-8")
    except OSError as ex:
        log.warning("manifest %s: %s", f, ex)


def _note_conflicts(pid: int, keys: list) -> None:
    if not keys:
        return
    prof = get(pid) or {}
    data = json.loads(library.meta_get("sync_conflicts", "[]"))
    data += [{"profile": prof.get("name", "?"), "file": k, "time": time.time()} for k in keys]
    library.meta_set("sync_conflicts", json.dumps(data[-50:]))


def pop_conflicts() -> list:
    data = json.loads(library.meta_get("sync_conflicts", "[]"))
    if data:
        library.meta_set("sync_conflicts", "[]")
    return data


def _sync(cfg: dict, pid: int, kind: str, family: str, names: list, up: bool) -> int:
    r"""kind: save | settings. Lokalnie ↔ NAS (przez serwer EmuStart albo SMB), z manifestem
    i kopiami zapasowymi: w dół — nadpisywane pliki lokalne do profiles\<id>\_backup,
    w górę — nadpisywane pliki na NAS do <profil NAS>\_backup (ostatnie 10)."""
    prof = get(pid)
    fs = nasfs.backend(cfg)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    mf, man = _manifest(pid)
    fam_key = f"{kind}/{family}"
    fam_man = man.setdefault(fam_key, {})
    base = f"{prof['nas_name']}/{kind}/{family}"
    # znacznik zmian na NAS: nowy przy każdym wysłaniu. Ten sam co przy naszej
    # ostatniej synchronizacji = nikt inny nic nie wysłał — NAS-a nie listujemy
    # (raz na dobę i tak pełne porównanie)
    stamps = man.setdefault("_stamps", {})
    stamp_rel = f"{base}/.emustart-stamp"
    try:
        nas_stamp = (fs.read(stamp_rel) or b"").decode("utf-8", "replace").strip()
    except OSError:
        nas_stamp = ""
    mine = stamps.get(fam_key) or {}
    unchanged = bool(nas_stamp) and mine.get("stamp") == nas_stamp and time.time() - mine.get("full", 0) < 86400
    locals_ = [local_dir(pid, family, nm) if kind == "save" else LOCAL / str(pid) / "settings" / family
               for nm in names]
    if not up and unchanged and all(d.is_dir() for d in locals_):
        return 0
    conflicts: list = []
    n = 0
    for name, local in zip(names, locals_):
        nrel = f"{base}/{name}" if kind == "save" else base
        if up:
            bak = f"{prof['nas_name']}/_backup/{stamp}-{socket.gethostname()}/{kind}/{family}/{name}"
            n += _push(local, fs, nrel, bak, fam_man, name, conflicts, dst_known=unchanged)
        else:
            bak = LOCAL / str(pid) / "_backup" / stamp / kind / family / name
            n += _pull(fs, nrel, local, bak, fam_man, name, conflicts)
    full = mine.get("full", 0) if unchanged else time.time()
    if up and (n or not nas_stamp):
        new = f"{time.time():.3f}-{socket.gethostname()}"
        try:
            fs.write(stamp_rel, new.encode("utf-8"))
            if nas_stamp and mine.get("stamp") != nas_stamp:
                stamps.pop(fam_key, None)    # ktoś inny też coś wysłał — następnym razem pełne porównanie
            else:
                stamps[fam_key] = {"stamp": new, "full": full}
        except OSError as ex:
            log.warning("znacznik synchronizacji: %s", ex)
    elif nas_stamp and (unchanged or not up):
        stamps[fam_key] = {"stamp": nas_stamp, "full": full}
    _manifest_save(mf, man)
    _note_conflicts(pid, conflicts)
    if up and n:
        fs.prune(f"{prof['nas_name']}/_backup")
    return n


def sync_down(cfg: dict, pid: int, family: str, names: list) -> int:
    prof = get(pid)
    if not prof or not nas_online(cfg):
        return 0
    return _sync(cfg, pid, "save", family, names, up=False)


def sync_up(cfg: dict, pid: int, family: str, names: list) -> int:
    prof = get(pid)
    if not prof or not nas_online(cfg):
        _pending(pid, family, names, add=True)
        return 0
    n = _sync(cfg, pid, "save", family, names, up=True)
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
    if nas_online(cfg):
        resolve_moves(cfg)
    data = json.loads(library.meta_get("sync_pending", "{}"))
    n = 0
    for k, names in data.items():
        pid, family = k.split("|", 1)
        n += sync_up(cfg, int(pid), family, names)
    return n


def local_families(pid: int) -> dict:
    """{emulator: [foldery save'ów]} zapisane lokalnie dla profilu."""
    out = {}
    base = LOCAL / str(pid)
    try:
        for fam in base.iterdir():
            if fam.is_dir() and not fam.name.startswith(("_", ".")) and fam.name != "settings":
                names = [d.name for d in fam.iterdir() if d.is_dir()]
                if names:
                    out[fam.name] = names
    except OSError:
        pass
    return out


def push_all(cfg: dict) -> int:
    """Przy starcie: wszystkie lokalne save'y i ustawienia profili → NAS/serwer (tylko
    zmienione i brakujące). Np. po przejściu na serwer albo po grze bez połączenia."""
    if not nas_online(cfg):
        return 0
    resolve_moves(cfg)
    n = 0
    for prof in all_profiles():
        pid = prof["id"]
        for family, names in local_families(pid).items():
            try:
                n += _sync(cfg, pid, "save", family, names, up=True)
            except Exception:
                log.exception("wysyłanie save'ów %s/%s", prof["name"], family)
        sdir = LOCAL / str(pid) / "settings"
        try:
            fams = [d.name for d in sdir.iterdir() if d.is_dir()] if sdir.is_dir() else []
        except OSError:
            fams = []
        for family in fams:
            try:
                n += _sync(cfg, pid, "settings", family, ["-"], up=True)
            except Exception:
                log.exception("wysyłanie ustawień %s/%s", prof["name"], family)
    library.meta_set("sync_pending", "{}")
    if n:
        log.info("start: wysłano %d plików profili (%s)", n, via(cfg))
    return n


# ── blokada ──

def lock(cfg: dict, pid: int) -> str:
    """'' = zablokowano; inaczej nazwa komputera, na którym profil właśnie gra."""
    prof = get(pid)
    if not prof or not nas_online(cfg):
        return ""
    fs = nasfs.backend(cfg)
    rel = f"{prof['nas_name']}/lock"
    me = socket.gethostname()
    try:
        cur = json.loads(fs.read(rel) or b"{}")
        if cur.get("host") != me and time.time() - cur.get("time", 0) < LOCK_MAX_AGE:
            return cur.get("host", "?")
    except (OSError, ValueError):
        pass
    try:
        fs.write(rel, json.dumps({"host": me, "time": time.time()}).encode("utf-8"))
    except OSError as ex:
        log.warning("blokada profilu: %s", ex)
    return ""


def unlock(cfg: dict, pid: int) -> None:
    prof = get(pid)
    if not prof:
        return
    fs = nasfs.backend(cfg)
    rel = f"{prof['nas_name']}/lock"
    try:
        if json.loads(fs.read(rel) or b"{}").get("host") == socket.gethostname():
            fs.unlink(rel)
    except (OSError, ValueError):
        pass


# ── profile z NAS (czysta instalacja / drugi komputer) ──

def import_from_nas(cfg: dict) -> list:
    """Zakłada profile dla folderów na NAS, których nie ma w bazie. Zwraca nazwy."""
    resolve_moves(cfg)
    fs = nasfs.backend(cfg)
    try:
        dirs = [name for name, is_dir in fs.listdir("") if is_dir and not name.startswith((".", "_"))]
    except (OSError, ValueError):
        return []
    have = {p["nas_name"].lower() for p in all_profiles()} | {p["name"].lower() for p in all_profiles()}
    added = []
    for d in sorted(dirs, key=str.lower):
        if d.lower() in have:
            continue
        try:
            if not any(fs.exists(f"{d}/{sub}") for sub in ("save", "settings", "emustart.json", "retroachievements.json")):
                continue
            with library.db() as c:
                c.execute("INSERT INTO profiles(name, nas_name, color) VALUES(?,?,?)", (d[:32], d, ""))
            added.append(d)
        except Exception:
            log.exception("profil z NAS %s", d)
    if added:
        log.info("profile z NAS: %s", ", ".join(added))
    return added


# ── małe pliki profilu (emustart.json, retroachievements.json): nowszy wygrywa ──

def _json_paths(cfg: dict, pid: int, name: str):
    """(plik lokalny, ścieżka na NAS względem folderu profili albo None)."""
    prof = get(pid)
    local = LOCAL / str(pid) / name
    return local, (f"{prof['nas_name']}/{name}" if prof else None)


_json_lock = threading.Lock()


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.emustart-tmp")
    tmp.write_text(text, encoding="utf-8")
    for i in range(5):                       # plik chwilowo otwarty przez inny wątek
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.1 * (i + 1))
    tmp.unlink(missing_ok=True)
    raise PermissionError(f"nie można zapisać {path}")


def json_get(cfg: dict, pid: int, name: str) -> dict | None:
    with _json_lock:
        return _json_get(cfg, pid, name)


def _json_get(cfg: dict, pid: int, name: str) -> dict | None:
    local, nas = _json_paths(cfg, pid, name)
    fs = nasfs.backend(cfg) if nas else None
    try:
        lst = local.stat().st_mtime if local.is_file() else None
    except OSError:
        lst = None
    nst = None
    if fs is not None:
        try:
            st = fs.stat(nas)
            nst = st[1] if st else None
        except OSError:
            fs = None                         # NAS/serwer niedostępny — kopia lokalna
    if lst is None and nst is None:
        return None
    try:
        if nst is not None and (lst is None or nst > lst + 1):
            raw = fs.read(nas)
            if raw is None:
                return None
            text = raw.decode("utf-8")
            _write_atomic(local, text)        # kopia lokalna na wypadek braku NAS
            os.utime(local, (nst, nst))
        else:
            text = local.read_text(encoding="utf-8")
            if fs is not None and (nst is None or nst < lst - 1):
                # zapis lokalny nie doszedł na NAS (był niedostępny) — dosyłamy
                fs.write(nas, text.encode("utf-8"), lst)
                log.info("dosłano %s profilu %s na NAS", name, pid)
        data = json.loads(text)
    except (OSError, ValueError) as ex:
        log.warning("synchronizacja %s: %s", name, ex)
        try:
            data = json.loads(local.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return data if isinstance(data, dict) else None


def json_set(cfg: dict, pid: int, name: str, data: dict) -> None:
    with _json_lock:
        local, nas = _json_paths(cfg, pid, name)
        text = json.dumps(data, ensure_ascii=False, indent=1)
        _write_atomic(local, text)
        if nas and nas_online(cfg):
            try:
                nasfs.backend(cfg).write(nas, text.encode("utf-8"), local.stat().st_mtime)
            except OSError as ex:
                log.warning("zapis %s na NAS: %s (dośle się przy następnym odczycie)", name, ex)


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
        _sync(cfg, pid, "settings", family, ["-"], up=False)
    files = list(_files_under(snap, rels))
    if not files:
        return 0
    # wartości tego komputera (ścieżki, GPU, audio) i sekrety (puste w kopii) zostają
    rules_by = _merge_rules(adapter.machine_keys(), adapter.secret_keys())
    keep = {rel: ingame.ini_pick(base / rel, rules) for rel, rules in rules_by.items()}
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
    secrets = {Path(k).as_posix(): v for k, v in adapter.secret_keys().items()}
    n = 0
    for rel, f in _files_under(base, rels):
        dst = snap / rel
        rules = secrets.get(Path(rel).as_posix())
        if rules:
            # kopia bez haseł i tokenów (trafia też na NAS i do kopii zapasowych)
            from emustart import ingame
            try:
                text = ingame.blank_secrets(f.read_text(encoding="utf-8-sig", errors="replace"), rules)
            except OSError:
                continue
            if dst.is_file() and dst.read_text(encoding="utf-8", errors="replace") == text:
                continue
            _write_atomic(dst, text)
            n += 1
            continue
        if dst.is_file() and _same_file(f, dst):
            continue
        _copy_file(f, dst)
        n += 1
    prof = get(pid)
    if prof and nas_online(cfg):
        _sync(cfg, pid, "settings", family, ["-"], up=True)
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
    if rec is None and pid == machine_owner() and not setup_needed():
        found = adapter.read_cheevos()
        if found:
            rec = {**found, "hardcore": False}
            ra_set(cfg, pid, rec)
            log.info("RetroAchievements: konto %s z %s przypisane do profilu %s", found["user"], adapter.family, pid)
    return rec or {"user": "", "token": ""}


# ── stan wznowienia („quicksave i wyjdź”) między komputerami ──
# <profil NAS>\resume\index.json: {"es/nazwa gry": {family, file, created, host}
#   albo {"dropped": czas}} + pliki stanów. Gra jest rozpoznawana po systemie
#   i nazwie (identyfikatory w lokalnych bazach różnią się między komputerami).

def _rkey(g: dict) -> str:
    return f"{g['es']}/{g['name']}"


def _resume_index(cfg: dict, prof: dict) -> tuple[str, dict]:
    d = f"{prof['nas_name']}/resume"
    try:
        return d, json.loads(nasfs.backend(cfg).read(f"{d}/index.json") or b"{}")
    except (OSError, ValueError):
        return d, {}


def _resume_write(cfg: dict, d: str, idx: dict) -> None:
    nasfs.backend(cfg).write(f"{d}/index.json", json.dumps(idx, ensure_ascii=False, indent=1).encode("utf-8"))


def resume_put(cfg: dict, pid: int, g: dict, family: str, path: str, created: float) -> bool:
    prof = get(pid)
    if not prof or not nas_online(cfg):
        return False
    import hashlib
    fs = nasfs.backend(cfg)
    d, idx = _resume_index(cfg, prof)
    key = _rkey(g)
    fname = ""
    try:
        if path:
            fname = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + Path(path).suffix
            fs.put_file(Path(path), f"{d}/{fname}")
        old = _rfile(idx.get(key, {}).get("file"))
        if old and old != fname:
            fs.unlink(f"{d}/{old}")
        idx[key] = {"family": family, "file": fname, "created": created, "host": socket.gethostname()}
        _resume_write(cfg, d, idx)
        return True
    except OSError as ex:
        log.warning("wznowienie na NAS: %s", ex)
        return False


def _rfile(name) -> str:
    """Nazwa pliku stanu z indeksu na NAS — tylko „abc123.ext”, bez ścieżek."""
    name = str(name or "")
    return name if re.fullmatch(r"[0-9a-f]{16}\.[A-Za-z0-9_.-]{1,16}", name) and ".." not in name else ""


def resume_drop(cfg: dict, pid: int, g: dict) -> None:
    """Stan wznowienia zużyty — inne komputery nie mogą go już wczytać."""
    prof = get(pid)
    if not prof or not nas_online(cfg):
        return
    d, idx = _resume_index(cfg, prof)
    old = _rfile(idx.get(_rkey(g), {}).get("file"))
    try:
        if old:
            nasfs.backend(cfg).unlink(f"{d}/{old}")
        idx[_rkey(g)] = {"dropped": time.time()}
        _resume_write(cfg, d, idx)
    except OSError as ex:
        log.warning("wznowienie na NAS: %s", ex)


def resume_pull(cfg: dict, pid: int, g: dict) -> dict | None:
    """Wpis z NAS: {"family", "path" (pobrany plik lub ''), "created"} albo
    {"dropped": czas}; None = brak informacji (NAS niedostępny, brak wpisu)."""
    prof = get(pid)
    if not prof or not nas_online(cfg):
        return None
    d, idx = _resume_index(cfg, prof)
    e = idx.get(_rkey(g))
    if not e:
        return None
    if "dropped" in e:
        return {"dropped": e["dropped"]}
    path = ""
    if e.get("file"):
        from emustart import ingame
        if not _rfile(e["file"]):
            log.warning("wznowienie: niepoprawna nazwa pliku w indeksie NAS — pomijam")
            return None
        dst_dir = ingame.resume_dir(pid)
        dst_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(e["file"]).suffix
        dst = dst_dir / f"{g['id']}{suffix}"
        tmp = dst_dir / f"{g['id']}.pobieranie{suffix}"
        try:
            if not nasfs.backend(cfg).get_file(f"{d}/{e['file']}", tmp):
                return None
        except OSError as ex:
            log.warning("wznowienie z NAS: %s", ex)
            return None
        for old in dst_dir.glob(f"{g['id']}.*"):
            if old != tmp:
                old.unlink(missing_ok=True)
        os.replace(tmp, dst)
        path = str(dst)
    return {"family": e.get("family", ""), "path": path, "created": e.get("created", 0)}


# ── zmiana folderu profilu na NAS (np. po zmianie nazwy) ──
# Stary folder zostaje z plikiem moved.json {"to": nowa nazwa}; inne komputery
# przy następnej synchronizacji same przepinają się na nowy folder.

MOVED = "moved.json"


def nas_wanted(prof: dict) -> str:
    return _safe_name(prof["name"])


def resolve_moves(cfg: dict) -> int:
    """Profile, których folder na NAS przeniesiono z innego komputera → nowy folder."""
    fs = nasfs.backend(cfg)
    n = 0
    for prof in all_profiles():
        name, seen = prof["nas_name"], set()
        while name not in seen and len(seen) < 5:
            seen.add(name)
            try:
                raw = fs.read(f"{name}/{MOVED}")
                if raw is None:
                    break
                data = json.loads(raw)
            except (OSError, ValueError):
                break
            to = str(data.get("to") or "")
            if not to or _safe_name(to) != to:
                break
            name = to
        if name != prof["nas_name"]:
            with library.db() as c:
                c.execute("UPDATE profiles SET nas_name=? WHERE id=?", (name, prof["id"]))
            log.info("profil %s: folder na NAS przeniesiony %s → %s", prof["name"], prof["nas_name"], name)
            n += 1
    return n


def nas_rename(cfg: dict, pid: int) -> str:
    """Przenosi folder profilu na NAS pod nazwę zgodną z nazwą profilu. '' = OK,
    inaczej powód odmowy."""
    prof = get(pid)
    if not prof:
        return "Nie ma takiego profilu."
    new = nas_wanted(prof)
    if new == prof["nas_name"]:
        return ""
    root = nas_root(cfg)
    if not nasfs.SmbFs(root).online():
        return "Zmiana folderu profilu wymaga dostępu do NAS-a przez sieć (SMB)."
    old_dir, new_dir = root / prof["nas_name"], root / new
    if new_dir.exists():
        return f"Na NAS jest już folder „{new}”."
    if library.db().execute("SELECT 1 FROM profiles WHERE nas_name=? AND id!=?", (new, pid)).fetchone():
        return f"Folder „{new}” należy do innego profilu."
    try:
        cur = json.loads((old_dir / "lock").read_text(encoding="utf-8"))
        if cur.get("host") != socket.gethostname() and time.time() - cur.get("time", 0) < LOCK_MAX_AGE:
            return f"Profil właśnie gra na komputerze {cur.get('host', '?')}."
    except (OSError, ValueError):
        pass
    try:
        if old_dir.exists():
            os.rename(old_dir, new_dir)
            old_dir.mkdir()
            (old_dir / MOVED).write_text(json.dumps({"to": new, "time": time.time(),
                                                     "host": socket.gethostname()}), encoding="utf-8")
        else:
            new_dir.mkdir(parents=True)
    except OSError as ex:
        return f"Nie udało się przenieść folderu: {ex}"
    with library.db() as c:
        c.execute("UPDATE profiles SET nas_name=? WHERE id=?", (new, pid))
    log.info("profil %s: folder na NAS %s → %s", prof["name"], old_dir.name, new)
    return ""


def _merge_rules(*maps) -> dict:
    out: dict = {}
    for m in maps:
        for rel, rules in m.items():
            out.setdefault(rel, []).extend(rules)
    return out


# ── jednorazowe czyszczenie: sekrety w kopiach ustawień zapisanych przed 0.16.5 ──

SECRET_FILES = {
    "retroarch.cfg": [("", r"cheevos_password|cheevos_token|netplay_password|netplay_spectate_password|"
                           r".*_api_key|.*_auth_token")],
    "settings.ini": [("Cheevos", "Token")],
    "PCSX2.ini": [("Achievements", "Token")],
}


def scrub_settings_copies(cfg: dict) -> int:
    """Usuwa hasła/tokeny z kopii ustawień profili (lokalnie i na NAS, także
    w _backup). Pliki emulatorów i profiles/_machine (ten komputer) zostają."""
    from emustart import ingame
    roots = [LOCAL]
    if nasfs.SmbFs(nas_root(cfg)).online():
        roots.append(nas_root(cfg))
    n = 0
    for root in roots:
        try:
            files = [f for name in SECRET_FILES for f in root.rglob(name)]
        except OSError:
            continue
        for f in files:
            parts = {p.lower() for p in f.parts}
            if "settings" not in parts or "_machine" in parts:
                continue
            try:
                text = f.read_text(encoding="utf-8-sig", errors="replace")
                new = ingame.blank_secrets(text, SECRET_FILES[f.name])
                if new != text:
                    st = f.stat()
                    _write_atomic(f, new)
                    os.utime(f, (st.st_mtime, st.st_mtime))
                    n += 1
            except OSError as ex:
                log.warning("czyszczenie %s: %s", f, ex)
    if n:
        log.info("usunięto hasła/tokeny z %d kopii ustawień", n)
    return n
