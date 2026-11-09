"""gogsaves — zapisy i ustawienia gier GOG: reguły → lokalne pliki → synchronizacja przez serwer EmuStart.

Bez chmury GOG i bez GOG Galaxy: GOG jest tylko źródłem identyfikatora gry (productId), kopie i synchronizacja —
wyłącznie serwer EmuStart (emustart/savesync.py), tym samym mechanizmem co zapisy gier emulowanych
(emustart/syncclient.py: wersje, konflikty, kopia przed przywróceniem, offline, ponawianie).

Reguły (pierwszeństwo): 1. ręczne gracza (na tym urządzeniu, `<dane>/gog/saves/<pid>.json`), 2. poprawka serwera,
3. ludusavi-manifest (serwer: emustart/saverules.py). Dopasowanie wyłącznie po productId GOG.

Reguła = ścieżka z symbolami (<base>, <winAppData>, <winDocuments>…) i tagami save / config. Każda reguła daje
„zbiór plików”: folder bazowy (stała część ścieżki po rozwinięciu na tym komputerze) + wzorzec względny. Na
serwer idą tylko ścieżki względne wewnątrz zbioru; identyfikator zbioru = skrót szablonu reguły (ten sam na każdym
urządzeniu), więc drugi komputer odtwarza pliki w swoim folderze. Klucz paczki: platforma `gog`, gra = productId,
emulator `gog`, rdzeń = zbiór, rodzaj `saves` / `config`.

Rejestr Windows: w tym etapie tylko status „wymaga obsługi rejestru” — wpisy nie są synchronizowane.
"""

from __future__ import annotations

import fnmatch
import getpass
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path, PurePosixPath

from emustart import paths

log = logging.getLogger("emustart.gogsaves")

PLATFORM = "gog"
EMU = "gog"
RULES_MAX_AGE = 86400                 # reguły z serwera odświeżane najwyżej raz na dobę (i przy synchronizacji ręcznej)
MAX_FILES = 5000
MAX_BYTES = 1024 << 20
_PLACE = re.compile(r"<([A-Za-z]+)>")
_GLOB = re.compile(r"[*?\[]")
_lock = threading.Lock()

# FOLDERID_* (KNOWNFOLDERID) — prawdziwe położenie, także przekierowane (np. Dokumenty na innym dysku)
_KNOWN = {
    "home": "{5E6C858F-0E22-4760-9AFE-EA3317B67173}",
    "winAppData": "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}",
    "winLocalAppData": "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}",
    "winLocalAppDataLow": "{A520A1A4-1780-4FF6-BD18-167343C5AF16}",
    "winDocuments": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "winPublic": "{DFDF76A2-C82A-4D63-906A-5644AC457385}",
    "winProgramData": "{62AB5D82-FDC1-4DC3-A9DD-070D1D495D97}",
    "winDir": "{F38BF404-1D43-42F2-9305-67DE0B28FC23}",
}


class RulesError(ValueError):
    pass


# ── foldery systemu ──

def _known_folder(guid: str) -> str:
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(guid), ctypes.byref(g))
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
        return ""
    try:
        return out.value or ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def system_folders() -> dict:
    """Foldery użytkownika i systemu z mechanizmów Windows (SHGetKnownFolderPath)."""
    if os.name != "nt":
        h = str(Path.home())
        return {"home": h, "xdgData": os.environ.get("XDG_DATA_HOME") or f"{h}/.local/share",
                "xdgConfig": os.environ.get("XDG_CONFIG_HOME") or f"{h}/.config"}
    out = {}
    for k, guid in _KNOWN.items():
        try:
            v = _known_folder(guid)
        except (OSError, AttributeError):
            v = ""
        if v:
            out[k] = v
    return out


# ── reguły: ręczne gracza → serwer (poprawka / ludusavi) ──

def _cfg_path(pid: str) -> Path:
    return paths.DATA / "gog" / "saves" / f"{pid}.json"


def _cache_path(pid: str) -> Path:
    return paths.DATA / "gog" / "saverules" / f"{pid}.json"


def settings(pid: str) -> dict:
    """Ustawienia synchronizacji gry na tym urządzeniu: enabled (zapisy, domyślnie tak), config (ustawienia,
    domyślnie nie), rules (ręczne reguły gracza albo None), pick (tytuł wybrany z niejednoznacznych)."""
    try:
        d = json.loads(_cfg_path(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"enabled": bool(d.get("enabled", True)), "config": bool(d.get("config", False)),
            "rules": d.get("rules") if isinstance(d.get("rules"), dict) else None, "pick": str(d.get("pick") or "")}


def save_settings(pid: str, **kw) -> dict:
    cur = settings(pid)
    for k in ("enabled", "config"):
        if k in kw:
            cur[k] = bool(kw[k])
    if "pick" in kw:
        cur["pick"] = str(kw["pick"] or "")[:300]
    if "rules" in kw:
        cur["rules"] = validate_user_rules(kw["rules"]) if kw["rules"] else None
    p = _cfg_path(pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    return cur


def validate_user_rules(r) -> dict:
    """Ręczne reguły gracza: {"files": [{"path", "tags": ["save"|"config"]}]} — tylko znane symbole, bez ścieżek
    względnych; rejestru ręcznie nie ustawiamy."""
    if not isinstance(r, dict):
        raise RulesError("reguły: zły format")
    files = []
    for f in (r.get("files") or [])[:50]:
        path = str((f or {}).get("path") or "").strip()
        tags = [t for t in (f or {}).get("tags") or ["save"] if t in ("save", "config")] or ["save"]
        if not path:
            continue
        unknown = [p for p in _PLACE.findall(path) if p not in KNOWN_PLACES]
        if unknown:
            raise RulesError(f"nieznany symbol w ścieżce: <{unknown[0]}>")
        if path.startswith(("./", "../", ".\\", "..\\")) or ".." in re.split(r"[\\/]", path):
            raise RulesError("ścieżki względne i „..” są niedozwolone")
        files.append({"path": path, "tags": tags, "when": []})
    if not files:
        raise RulesError("podaj co najmniej jedną ścieżkę")
    return {"files": files}


def rules(pid: str, source=None, refresh: bool = False) -> dict:
    """Reguły gry z opisem źródła: user | server | ludusavi | ambiguous | none (offline: kopia z serwera)."""
    st = settings(pid)
    if st["rules"]:
        return {"source": "user", "title": "", "files": st["rules"]["files"], "registry": [], "version": "ręczne"}
    cache = _cache_path(pid)
    cached = None
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    fresh = cached and cached.get("pick", "") == st["pick"] and time.time() - float(cached.get("at") or 0) < RULES_MAX_AGE
    if source is not None and (refresh or not fresh):
        try:
            r = source.save_rules(pid, st["pick"])
            cached = {**r, "at": time.time(), "pick": st["pick"]}
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_name(cache.name + ".tmp")
            tmp.write_text(json.dumps(cached, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, cache)
        except Exception as ex:                          # serwer niedostępny — ostatnia znana wersja reguł
            log.info("GOG %s: reguły zapisów z serwera niedostępne: %s", pid, ex)
            if cached:
                cached = {**cached, "offline": str(ex)}
    if not cached:
        return {"source": "none", "files": [], "registry": [], "reason": "brak reguł (serwer niedostępny)"}
    return cached


# ── rozwijanie ścieżek ──

KNOWN_PLACES = {"root", "game", "base", "home", "storeGameId", "storeUserId", "osUserName", "winAppData",
                "winLocalAppData", "winLocalAppDataLow", "winDocuments", "winPublic", "winProgramData", "winDir",
                "xdgData", "xdgConfig"}


def applies(when: list) -> bool:
    """Warunki reguły: bez warunków — zawsze; inaczej któryś pasuje do Windows (także DOS — gry DOS na Windows
    przez DOSBox z paczki GOG) i sklepu GOG (albo bez sklepu)."""
    if not when:
        return True
    for w in when:
        o, s = (w or {}).get("os"), (w or {}).get("store")
        if o not in (None, "windows", "dos"):
            continue
        if s not in (None, "gog"):
            continue
        return True
    return False


def kind_of(tags: list) -> str:
    """save (także bez tagów i z oboma) → saves; tylko config → config."""
    t = set(tags or [])
    return "config" if t == {"config"} else "saves"


def context(install_dir: str, pid: str, folders: dict | None = None) -> dict:
    base = Path(install_dir)
    f = dict(system_folders() if folders is None else folders)
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    f.update(base=str(base), root=str(base.parent), game=base.name, storeGameId=str(pid),
             storeUserId="*", osUserName=user)
    return f


def expand(template: str, ctx: dict) -> str:
    """Symbole → ścieżka (ze wzorcami) na tym komputerze; nieznany / niedostępny symbol → RulesError."""
    def sub(m):
        k = m.group(1)
        v = ctx.get(k)
        if k not in KNOWN_PLACES or not v:
            raise RulesError(f"symbol <{k}> niedostępny na tym komputerze")
        return v.replace("\\", "/")
    s = _PLACE.sub(sub, template.replace("\\", "/"))
    if ".." in s.split("/"):
        raise RulesError("ścieżka z „..”")
    return s


def _protected(ctx: dict) -> set:
    """Foldery, których nigdy nie synchronizujemy w całości (cały profil, Dokumenty, AppData, katalog gier…)."""
    keys = ("home", "winAppData", "winLocalAppData", "winLocalAppDataLow", "winDocuments", "winPublic",
            "winProgramData", "winDir", "root", "xdgData", "xdgConfig")
    out = {os.path.normcase(os.path.abspath(ctx[k])) for k in keys if ctx.get(k)}
    if ctx.get("home"):
        out.add(os.path.normcase(os.path.abspath(os.path.join(ctx["home"], "AppData"))))
    out |= {os.path.normcase(os.path.abspath(os.path.splitdrive(v)[0] + os.sep)) for v in ctx.values()
            if isinstance(v, str) and os.path.splitdrive(v)[0]}
    return out


def split_pattern(expanded: str) -> tuple:
    """(folder bazowy, części wzorca) — stała część ścieżki i reszta ze znakami wzorca."""
    parts = [p for p in expanded.split("/") if p != ""]
    drive = ""
    if expanded.startswith("/"):
        drive = "/"
    fixed = []
    for i, p in enumerate(parts):
        if _GLOB.search(p):
            return (drive + "/".join(fixed)) if fixed else drive, parts[i:]
        fixed.append(p)
    return drive + "/".join(fixed), []


def _is_link(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        return os.name == "nt" and bool(os.lstat(p).st_file_attributes & 0x400)   # punkt połączenia / reparse
    except (OSError, AttributeError):
        return False


class GogBundle:
    """Zbiór plików jednej reguły — interfejs paczki syncclient (key, folder, files, collect, local_path, spec)."""

    emulator_id = EMU

    def __init__(self, set_id: str, kind: str, folder: Path, parts: list, template: str, pid: str):
        self.core_id, self.kind, self.folder, self.parts, self.template, self.pid = set_id, kind, Path(folder), list(parts), template, pid
        self.files: list = []
        self.error = ""

    @property
    def key(self) -> tuple:
        return self.emulator_id, self.core_id, self.kind

    def spec(self) -> dict:
        return {"emu": EMU, "core": self.core_id, "kind": self.kind, "folder": str(self.folder),
                "parts": self.parts, "template": self.template, "pid": self.pid}

    @classmethod
    def from_spec(cls, s: dict) -> "GogBundle":
        return cls(s["core"], s["kind"], Path(s["folder"]), list(s.get("parts") or []), s.get("template", ""),
                   s.get("pid", "")).collect()

    def _match(self, rel: list) -> bool:
        """Czy względna ścieżka (części) pasuje do wzorca (** = dowolna głębokość); bez wielkości liter."""
        def m(pi: int, ri: int) -> bool:
            if pi == len(self.parts):
                return ri == len(rel)
            p = self.parts[pi]
            if p == "**":
                return any(m(pi + 1, k) for k in range(ri, len(rel) + 1))
            return ri < len(rel) and fnmatch.fnmatch(rel[ri].lower(), p.lower()) and m(pi + 1, ri + 1)
        return m(0, 0)

    def collect(self) -> "GogBundle":
        """Pliki zbioru (od nowa). Folder pasujący do wzorca — cały, rekurencyjnie. Dowiązania i punkty
        połączenia pomijane. Limit liczby i rozmiaru plików — przekroczenie = błąd zbioru, nie obcięcie."""
        self.files, self.error = [], ""
        root = self.folder
        if not root.is_dir() or _is_link(root):
            return self
        out, total = [], 0

        def add_tree(d: Path, rel: list):
            nonlocal total
            for dirpath, dirnames, filenames in os.walk(d):
                dp = Path(dirpath)
                dirnames[:] = [n for n in dirnames if not _is_link(dp / n)]
                for n in filenames:
                    f = dp / n
                    if _is_link(f) or n.endswith(".emustart-new"):
                        continue
                    arc = PurePosixPath(*rel, *f.relative_to(d).parts).as_posix()
                    out.append((str(f), arc))
                    total += f.stat().st_size
        if not self.parts:
            add_tree(root, [])
        else:
            for dirpath, dirnames, filenames in os.walk(root):
                dp = Path(dirpath)
                relbase = list(dp.relative_to(root).parts)
                keep = []
                for n in dirnames:
                    if _is_link(dp / n):
                        continue
                    if self._match(relbase + [n]):
                        add_tree(dp / n, relbase + [n])          # pasujący folder — w całości
                    else:
                        keep.append(n)
                dirnames[:] = keep
                for n in filenames:
                    f = dp / n
                    if not _is_link(f) and not n.endswith(".emustart-new") and self._match(relbase + [n]):
                        out.append((str(f), PurePosixPath(*relbase, n).as_posix()))
                        total += f.stat().st_size
        if len(out) > MAX_FILES or total > MAX_BYTES:
            self.error = f"zbiór za duży ({len(out)} plików, {total >> 20} MB) — nie synchronizuję"
            return self
        self.files = sorted(out, key=lambda x: x[1])
        return self

    def local_path(self, arc: str) -> Path:
        """Plik z paczki serwera → ścieżka w tym zbiorze; nic poza folderem zbioru i nic przez dowiązania."""
        parts = [p for p in str(arc).replace("\\", "/").split("/")]
        if not parts or any(p in ("", ".", "..") or ":" in p for p in parts) or str(arc).startswith(("/", "\\")):
            raise ValueError(f"niedozwolona nazwa w paczce: {arc!r}")
        if self.parts and not self._match_prefix(parts):
            raise ValueError(f"plik spoza wzorca zbioru: {arc!r}")
        dst = self.folder.joinpath(*parts)
        cur = self.folder
        for p in parts[:-1]:
            cur = cur / p
            if _is_link(cur):
                raise ValueError(f"dowiązanie w ścieżce: {arc!r}")
        root = os.path.normcase(os.path.abspath(self.folder))
        if not os.path.normcase(os.path.abspath(dst)).startswith(root + os.sep):
            raise ValueError(f"ścieżka poza zbiorem: {arc!r}")
        return dst

    def _match_prefix(self, parts: list) -> bool:
        return any(self._match(parts[:k]) for k in range(1, len(parts) + 1))


def plan(pid: str, install_dir: str, r: dict, with_config: bool, folders: dict | None = None) -> dict:
    """Reguły → zbiory plików na tym komputerze. {"sets": [GogBundle], "skipped": [{path, reason}],
    "registry": [{path, kind, reason}]}."""
    ctx = context(install_dir, pid, folders)
    prot = _protected(ctx)
    sets, skipped, reg = [], [], []
    for f in r.get("files") or []:
        tpl = str(f.get("path") or "")
        if not applies(f.get("when") or []):
            continue
        kind = kind_of(f.get("tags") or [])
        if kind == "config" and not with_config:
            skipped.append({"path": tpl, "kind": kind, "reason": "ustawienia wyłączone"})
            continue
        try:
            exp = expand(tpl, ctx)
        except RulesError as ex:
            skipped.append({"path": tpl, "kind": kind, "reason": str(ex)})
            continue
        folder, parts = split_pattern(exp)
        if not parts:                         # bez wzorca: plik (ostatnia część z rozszerzeniem) albo folder
            p = Path(folder)
            if p.is_file() or (not p.exists() and "." in p.name):
                folder, parts = str(p.parent), [p.name]
        fold = os.path.normcase(os.path.abspath(folder))
        if fold in prot and (not parts or parts[0] in ("*", "**")):
            skipped.append({"path": tpl, "kind": kind, "reason": "reguła obejmuje cały folder systemu — pomijam"})
            continue
        set_id = "s" + hashlib.sha1(f"{kind}|{tpl.strip().lower()}".encode("utf-8")).hexdigest()[:16]
        if any(b.core_id == set_id for b in sets):
            continue
        sets.append(GogBundle(set_id, kind, Path(folder), parts, tpl, pid).collect())
    for g in r.get("registry") or []:
        if not applies(g.get("when") or []):
            continue
        kind = kind_of(g.get("tags") or [])
        if kind == "config" and not with_config:
            continue
        reg.append({"path": g.get("path") or "", "kind": kind, "reason": "wymaga obsługi rejestru — wpisy nie są synchronizowane"})
    return {"sets": sets, "skipped": skipped, "registry": reg}


# ── synchronizacja (syncclient: te same wersje, konflikty i kopie co gry emulowane) ──

def before(cfg: dict, profile: int, pid: str, bundles: list):
    """Przed grą: pobranie nowszych zapisów / wysłanie niewysłanych / konflikty (decyzja gracza)."""
    from emustart import syncclient as sc
    res = sc.Result()
    if not bundles:
        return res
    try:
        cl = sc._Client(cfg, profile)
        man = cl.manifest(PLATFORM, pid)
    except sc.Offline as ex:
        res.offline = str(ex)
        return res
    with sc._lock:
        for b in bundles:
            if b.error:
                continue
            try:
                sc._before_one(cl, profile, PLATFORM, pid, b, sc._server_bundle(man, b, 1), res, 1)
            except sc.Offline as ex:
                res.offline = str(ex)
            except (OSError, ValueError) as ex:
                log.exception("GOG %s: zapisy %s", pid, b.key)
                res.offline = f"błąd synchronizacji ({ex})"
    return res


def resolve(cfg: dict, profile: int, pid: str, conflicts: list, choice: str):
    """local — wersja z tego urządzenia (serwerowa zostaje w historii serwera); remote — z serwera (bieżące pliki
    do kopii zapasowej); both — z serwera, a bieżące dodatkowo zachowane w kopii zapasowej (jak remote, kopia
    jest zawsze); skip — bez zmian (konflikt wróci przy następnej grze)."""
    from emustart import syncclient as sc
    res = sc.Result()
    if choice == "skip" or not conflicts:
        return res
    cl = sc._Client(cfg, profile)
    man = cl.manifest(PLATFORM, pid)
    with sc._lock:
        for c in conflicts:
            b = c.bundle.collect()
            srv = sc._server_bundle(man, b, 1)
            if choice == "local":
                sc._push(cl, profile, PLATFORM, pid, b, int(srv.get("revision") or 0), sc.savebundles.content_hash(b.files), res, 1)
            elif choice in ("remote", "both") and srv.get("download_url"):
                sc._download(cl, profile, PLATFORM, pid, b, srv, 1)
                res.downloaded += 1
    return res


def after(cfg: dict, profile: int, pid: str, bundles: list, skip: set | None = None):
    """Po grze: zmienione zbiory na serwer. Pusty skan (zbiór zniknął / dysk niedostępny) niczego nie kasuje."""
    from emustart import syncclient as sc
    res = sc.Result()
    todo = []
    for b in bundles:
        if b.key in (skip or set()):
            continue
        b.collect()
        if b.error:
            continue
        local = sc.savebundles.content_hash(b.files)
        st = sc._state(profile, PLATFORM, pid, b, 1)
        if local and local != (st or {}).get("content"):
            todo.append((b, local, (st or {}).get("revision", 0)))
    if not todo:
        return res
    try:
        cl = sc._Client(cfg, profile)
    except sc.Offline as ex:
        res.offline = str(ex)
        for b, _l, _r in todo:
            sc._mark_dirty(profile, PLATFORM, pid, b, 1)
        return res
    with sc._lock:
        for b, local, base in todo:
            try:
                sc._push(cl, profile, PLATFORM, pid, b, base, local, res, 1)
            except sc.Offline as ex:
                res.offline = str(ex)
                sc._mark_dirty(profile, PLATFORM, pid, b, 1)
    return res


def local_state(profile: int, pid: str, bundles: list) -> dict:
    """Ostatnia udana synchronizacja i oczekujące zmiany (z lokalnej bazy syncclient)."""
    from emustart import syncclient as sc
    last, pending = 0.0, 0
    for b in bundles:
        st = sc._state(profile, PLATFORM, pid, b, 1)
        if not st:
            if b.files:
                pending += 1
            continue
        last = max(last, float(st.get("updated") or 0)) if not st["dirty"] else last
        if st["dirty"] or (b.files and sc.savebundles.content_hash(b.files) != st["content"]):
            pending += 1
    return {"last_sync": last, "pending": pending}


def backups(profile: int, pid: str) -> list:
    """Kopie zapasowe zrobione przed przywróceniem plików z serwera (najnowsze pierwsze): [{id, at, sets}]."""
    from emustart import syncclient as sc
    root = sc._backup_dir(profile)
    out = []
    if not root.is_dir():
        return out
    for d in sorted((x for x in root.iterdir() if x.is_dir()), key=lambda x: x.name, reverse=True):
        sets = [s.name for s in d.iterdir() if s.is_dir() and s.name.startswith(f"{EMU}-")]
        if sets:
            out.append({"id": d.name, "sets": sets})
    return out


def restore_backup(profile: int, pid: str, backup_id: str, bundles: list) -> int:
    """Kopia zapasowa z powrotem do zbiorów tej gry (bieżące pliki najpierw do nowej kopii). Potem zwykła
    synchronizacja wyśle ją jako zmianę z tego urządzenia. Zwraca liczbę przywróconych plików."""
    import shutil
    from emustart import syncclient as sc
    if not re.match(r"^\d{8}-\d{6}$", backup_id or ""):
        raise ValueError("zła kopia")
    import tempfile
    orig = sc._backup_dir(profile) / backup_id
    if not orig.is_dir():
        raise ValueError("nie ma takiej kopii")
    n = 0
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = Path(tempfile.mkdtemp(prefix="emustart-gog-restore-"))
    try:
        src = work / "kopia"
        shutil.copytree(orig, src)            # najpierw kopia kopii: nowa kopia bieżących plików może mieć tę samą nazwę
        n = _restore_from(src, profile, bundles, stamp)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return n


def _restore_from(src: Path, profile: int, bundles: list, stamp: str) -> int:
    import shutil
    from emustart import syncclient as sc
    n = 0
    for b in bundles:
        d = src / f"{EMU}-{b.core_id}-{b.kind}"
        if not d.is_dir():
            continue
        b.collect()
        if b.files:                           # bieżące pliki — najpierw do nowej kopii
            bak = sc._backup_dir(profile) / stamp / f"{EMU}-{b.core_id}-{b.kind}"
            for path, arc in b.files:
                dst = bak / arc
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, dst)
        for f in d.rglob("*"):
            if f.is_file():
                arc = f.relative_to(d).as_posix()
                dst = b.local_path(arc)
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(dst.name + ".emustart-new")
                shutil.copy2(f, tmp)
                os.replace(tmp, dst)
                n += 1
        b.collect()
    return n
