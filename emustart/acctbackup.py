"""acctbackup — przeglądanie kont graczy i ich kopie zapasowe (panel EmuStart Server).

Kopia konta to jeden ZIP w folderze kopii (`backup_dir`, domyślnie `<serwer>\\backup`):
`<konto>\\<konto>-<RRRRMMDD-GGMMSS>.zip` z całym folderem profilu konta (zapisy, stany
gry, ustawienia emulatorów, wybrane wersje gier, RetroAchievements) i wpisem konta
(`_konto.json`: skrót hasła, administrator). Bez `_backup` (kopie pojedynczych plików
robione przy zapisie — szybko się zmieniają) i bez urządzeń (klucze urządzeń są w nich).

Kopie automatyczne: raz dziennie każde konto, którego profil zmienił się od ostatniej
kopii; zostaje `backup_keep` najnowszych (domyślnie 14). Przywrócenie zaczyna się od
kopii obecnego stanu — nic nie ginie.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import zipfile
from pathlib import Path

log = logging.getLogger("emustart.acctbackup")

SKIP = {"_backup"}
ACCOUNT_FILE = "_konto.json"
DAY = 86400
_lock = threading.Lock()
_NAME = re.compile(r"^[^\\/:*?\"<>|]{1,64}$")


def _root() -> Path | None:
    from emustart import remote
    return remote.NAS_ROOT


def _dir(cfg: dict) -> Path:
    from emustart import srvconfig
    return srvconfig.backup_dir(cfg)


def _profile(name: str) -> Path:
    root = _root()
    if not root or not _NAME.match(name or "") or name.startswith((".", "_")):
        raise ValueError("Nie ma takiego konta.")
    p = root / name
    if not p.is_dir():
        raise ValueError(f"Konto {name} nie ma folderu profilu.")
    return p


def _files(p: Path):
    for dirpath, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            yield Path(dirpath) / f


def browse(name: str) -> dict:
    """Co jest w profilu konta: foldery główne (pliki, rozmiar, ostatnia zmiana) i ostatnio
    zmienione pliki."""
    p = _profile(name)
    parts: dict = {}
    recent = []
    for f in _files(p):
        try:
            st = f.stat()
        except OSError:
            continue
        rel = f.relative_to(p)
        top = rel.parts[0] if len(rel.parts) > 1 else "(pliki konta)"
        d = parts.setdefault(top, {"name": top, "files": 0, "size": 0, "changed": 0.0})
        d["files"] += 1
        d["size"] += st.st_size
        d["changed"] = max(d["changed"], st.st_mtime)
        recent.append((st.st_mtime, rel.as_posix(), st.st_size))
    recent.sort(reverse=True)
    return {"ok": True, "name": name, "parts": sorted(parts.values(), key=lambda d: d["name"].lower()),
            "recent": [{"path": r, "size": s, "changed": t} for t, r, s in recent[:25]]}


def backups(cfg: dict, name: str) -> list:
    d = _dir(cfg) / name
    out = []
    for f in d.glob(f"{name}-*.zip") if d.is_dir() else []:
        try:
            st = f.stat()
        except OSError:
            continue
        out.append({"file": f.name, "size": st.st_size, "time": st.st_mtime, "_ns": st.st_mtime_ns})
    out.sort(key=lambda b: (b["_ns"], b["file"]), reverse=True)          # najnowsza pierwsza
    for b in out:
        del b["_ns"]
    return out


def _latest_change(p: Path) -> float:
    t = 0.0
    for f in _files(p):
        try:
            t = max(t, f.stat().st_mtime)
        except OSError:
            pass
    return t


def backup(cfg: dict, name: str, why: str = "ręcznie") -> dict:
    """Kopia konta teraz → {ok, file, size}."""
    from emustart import accounts
    p = _profile(name)
    with _lock:
        d = _dir(cfg) / name
        d.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = d / f"{name}-{stamp}.zip"
        n = 1
        while dst.exists():                        # dwie kopie w tej samej sekundzie
            n += 1
            dst = d / f"{name}-{stamp}-{n}.zip"
        tmp = dst.with_suffix(".tmp")
        rec = accounts.record(name) or {}
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            z.writestr(ACCOUNT_FILE, json.dumps({"name": name, "record": rec, "created": time.time(),
                                                 "why": why}, ensure_ascii=False, indent=1))
            for f in _files(p):
                try:
                    z.write(f, "profil/" + f.relative_to(p).as_posix())
                except OSError as ex:
                    log.warning("kopia %s: %s — %s", name, f, ex)
        os.replace(tmp, dst)
        size = dst.stat().st_size
    prune(cfg, name, keep_file=dst.name)
    log.info("kopia konta %s (%s): %s, %d B", name, why, dst, size)
    return {"ok": True, "file": dst.name, "size": size}


def prune(cfg: dict, name: str, keep_file: str = "") -> None:
    keep = max(1, int(cfg.get("backup_keep") or 14))
    for b in backups(cfg, name)[keep:]:
        if b["file"] == keep_file:              # właśnie zrobiona — zawsze zostaje
            continue
        try:
            (_dir(cfg) / name / b["file"]).unlink()
        except OSError:
            pass


def restore(cfg: dict, name: str, file: str) -> dict:
    """Przywraca pliki profilu z kopii (najpierw kopia obecnego stanu). Pliki, których
    w kopii nie ma, zostają. Wpis konta (hasło) — tylko gdy konto go nie ma."""
    from emustart import accounts
    if not re.fullmatch(r"[^\\/]+\.zip", file or ""):
        return {"ok": False, "reason": "Zła nazwa kopii."}
    src = _dir(cfg) / name / file
    if not src.is_file():
        return {"ok": False, "reason": "Nie ma takiej kopii."}
    p = _profile(name)
    before = backup(cfg, name, why=f"przed przywróceniem {file}")
    base = p.resolve()
    n = 0
    with zipfile.ZipFile(src) as z:
        if z.testzip():
            return {"ok": False, "reason": "Kopia jest uszkodzona."}
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith("profil/"):
                continue
            dst = (p / info.filename[len("profil/"):]).resolve()
            if base not in dst.parents:
                return {"ok": False, "reason": f"Niedozwolona ścieżka w kopii: {info.filename}"}
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(dst.name + ".przywracanie")
            with z.open(info) as i, open(tmp, "wb") as o:
                o.write(i.read())
            os.replace(tmp, dst)
            ts = time.mktime(info.date_time + (0, 0, -1))
            os.utime(dst, (ts, ts))
            n += 1
        try:
            meta = json.loads(z.read(ACCOUNT_FILE))
        except (KeyError, ValueError):
            meta = {}
    rec = meta.get("record") or {}
    if rec.get("hash") and not accounts.has_password(name):
        accounts.set_record(name, rec, by="przywrócenie kopii")
    log.info("konto %s przywrócone z %s (%d plików; kopia przed: %s)", name, file, n, before["file"])
    return {"ok": True, "files": n, "before": before["file"]}


def auto_once(cfg: dict) -> int:
    """Kopie automatyczne: konta zmienione od ostatniej kopii, nie częściej niż raz dziennie."""
    from emustart import remote
    if cfg.get("backup_auto") is False or not _root():
        return 0
    n = 0
    for name in remote.profile_names():
        try:
            last = backups(cfg, name)
            if last and time.time() - last[0]["time"] < DAY:
                continue
            if last and _latest_change(_profile(name)) <= last[0]["time"]:
                continue                           # nic się nie zmieniło
            backup(cfg, name, why="automatycznie")
            n += 1
        except Exception:
            log.exception("kopia automatyczna %s", name)
    return n


def start_auto(cfg_ref) -> None:
    """Wątek kopii automatycznych serwera (sprawdzenie co godzinę)."""
    def run():
        time.sleep(300)                            # po starcie serwera — bez pośpiechu
        while True:
            try:
                auto_once(cfg_ref())
            except Exception:
                log.exception("kopie automatyczne")
            time.sleep(3600)
    threading.Thread(target=run, daemon=True, name="kopie-kont").start()
