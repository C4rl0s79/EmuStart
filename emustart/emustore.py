"""emustore — magazyn emulatorów EmuStart Server (źródło prawdy o wersjach).

Serwer raz dziennie (i na żądanie z panelu) sprawdza najnowsze wydania emulatorów
potrzebnych systemom w bibliotece (emustart/emucatalog.py), pobiera je z internetu i
trzyma rozpakowane:

    <magazyn>\\<pozycja>\\<wersja>\\…pliki programu…
    <magazyn>\\<pozycja>\\<wersja>\\.manifest.json   {rel: [rozmiar, sha256]}
    <magazyn>\\<pozycja>\\<wersja>\\.archive.zip     cała wersja w jednym ZIP-ie (do pobrania)
    <magazyn>\\index.json                            wersje, daty, błędy

Komputery porównują swoje pliki z manifestem; gdy czegoś brakuje albo się różni, pobierają
archiwum wersji jednym strumieniem (wznawianym) i rozpakowują u siebie tylko te pliki —
przesyłanie tysięcy małych plików było wolne (emustart/installer.py, /v1/emulators).
Zostaje bieżąca i poprzednia wersja pozycji.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from emustart import emucatalog, library, paths

log = logging.getLogger("emustart.emustore")

MANIFEST = ".manifest.json"
ARCHIVE = ".archive.zip"
DAY = 24 * 3600
_lock = threading.Lock()
_job: dict = {"running": False, "item": "", "step": 0, "steps": 0, "done": 0, "total": 0,
              "log": [], "started": 0.0, "finished": 0.0}


def store_dir(cfg: dict) -> Path:
    return Path(cfg.get("emu_store_dir") or (paths.SERVER / "emulators"))


def _safe(s: str) -> str:
    return re.sub(r"[^\w.@+-]", "_", s)[:80] or "_"


def _index_path(cfg: dict) -> Path:
    return store_dir(cfg) / "index.json"


def load_index(cfg: dict) -> dict:
    try:
        d = json.loads(_index_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("items", {})
    d.setdefault("errors", {})
    return d


def _save_index(cfg: dict, d: dict) -> None:
    p = _index_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("index.json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _library_systems(cfg: dict) -> list:
    out = []
    for r in library.db().execute("SELECT es FROM systems WHERE games > 0 ORDER BY es"):
        sc = (cfg.get("systems") or {}).get(r["es"]) or {}
        if sc.get("enabled") is not False:          # wyłączone w panelu pomijane
            out.append((r["es"], sc))
    return out


def alt_cores(idx: dict, es: str) -> list:
    """Wszystkie rdzenie RetroArcha pasujące do systemu (wg plików .info) — do wyboru na
    komputerze, np. bsnes obok zalecanego snes9x."""
    from emustart import systems
    name = systems.info(es).get("libretro") or ""
    return sorted(c for c, dbs in (idx.get("core_db") or {}).items() if name and name in dbs) if name else []


def needed(cfg: dict) -> dict:
    """{pozycja: [systemy]} — emulatory dla systemów biblioteki serwera (zalecane w panelu:
    emu_rec, core_rec) i wszystkie rdzenie RetroArcha z buildbota (do wyboru na komputerach)."""
    out: dict = {}
    idx = load_index(cfg)
    for es, sc in _library_systems(cfg):
        for item in emucatalog.plan(es, sc.get("emu_rec", ""), sc.get("core_rec", "")):
            out.setdefault(item, []).append(es)
        alts = alt_cores(idx, es)
        if alts and es not in out.setdefault("retroarch", []):
            out["retroarch"].append(es)              # rdzenie do wyboru działają tylko w RetroArchu
        for c in alts:
            out.setdefault(f"core-{c}", [])
            if es not in out[f"core-{c}"]:
                out[f"core-{c}"].append(es)
    for c in idx.get("cores") or []:
        out.setdefault(f"core-{c}", [])
    return out


def version_dir(cfg: dict, item: str, version: str) -> Path:
    return store_dir(cfg) / _safe(item) / _safe(version)


def manifest(cfg: dict, item: str, version: str) -> dict | None:
    try:
        return json.loads((version_dir(cfg, item, version) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def archive_path(cfg: dict, item: str, version: str) -> Path | None:
    p = version_dir(cfg, item, version) / ARCHIVE
    return p if p.is_file() and manifest(cfg, item, version) else None


def _make_archive(vdir: Path, files: dict) -> int:
    """ZIP wersji (kompresja — mniej do przesłania); budowany raz, przy pobraniu wersji."""
    tmp = vdir / (ARCHIVE + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        for rel in sorted(files):
            z.write(vdir / rel, rel)
    os.replace(tmp, vdir / ARCHIVE)
    return (vdir / ARCHIVE).stat().st_size


def ensure_archives(cfg: dict) -> None:
    """Wersje pobrane, zanim serwer robił archiwa — dorobienie archiwum."""
    idx = load_index(cfg)
    changed = False
    for item, meta in idx["items"].items():
        if not emucatalog.valid_item(item) or archive_path(cfg, item, meta.get("version", "")):
            continue
        m = manifest(cfg, item, meta.get("version", ""))
        if not m:
            continue
        try:
            _job["item"] = emucatalog.item_info(item)["label"] + " — archiwum"
            meta["archive_size"] = _make_archive(version_dir(cfg, item, meta["version"]), m["files"])
            changed = True
            log.info("emulatory: archiwum %s %s", item, meta["version"])
        except OSError as ex:
            log.warning("emulatory: archiwum %s: %s", item, ex)
    if changed:
        _save_index(cfg, idx)


def public_index(cfg: dict) -> dict:
    """Dla urządzeń (/v1/emulators): bieżące wersje i co potrzebuje który system."""
    idx = load_index(cfg)
    need = needed(cfg)
    items = {}
    for item, meta in idx["items"].items():
        if not emucatalog.valid_item(item):
            continue
        info = emucatalog.item_info(item)
        if not archive_path(cfg, item, meta["version"]):
            continue                         # archiwum jeszcze się robi — komputery poczekają
        items[item] = {"version": meta["version"], "size": meta.get("size", 0), "files": meta.get("files", 0),
                       "archive_size": meta.get("archive_size", 0),
                       "label": info["label"], "kind": info["kind"], "dir": info["dir"],
                       "portable": info["portable"], "keep": info["keep"]}
    systems_ = {}
    for item, ess in need.items():
        for es in ess:
            if item in items:                # tylko to, co serwer już ma
                systems_.setdefault(es, []).append(item)
    from emustart import emupolicy
    return {"items": items, "systems": systems_, "policy": emupolicy.effective(cfg)}


# ── pobieranie i budowa wersji ──

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def _build(cfg: dict, item: str, rel: dict, progress=None) -> dict:
    """Pobiera wydanie i składa folder wersji z manifestem. Zwraca wpis indeksu."""
    root = store_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    final = version_dir(cfg, item, rel["version"])
    part = final.with_name(final.name + ".part")
    shutil.rmtree(part, ignore_errors=True)
    kind = emucatalog.item_info(item)["kind"]
    # pliki tymczasowe na tym samym dysku co magazyn (duże archiwa, szybkie przenoszenie)
    with tempfile.TemporaryDirectory(prefix="emustore_", dir=root) as td:
        arch = Path(td) / _safe(rel["name"])
        emucatalog.download(rel["url"], arch, progress)
        if kind == "core":
            part.mkdir(parents=True)
            with zipfile.ZipFile(arch) as z:
                dll = f"{item[5:]}_libretro.dll"
                (part / dll).write_bytes(z.read(next(n for n in z.namelist() if Path(n).name == dll)))
        elif kind == "capsimg":
            part.mkdir(parents=True)
            data = None
            if rel["name"].endswith(".zip"):
                with zipfile.ZipFile(arch) as z:
                    data = next((z.read(m) for m in z.infolist() if Path(m.filename).name.lower() == "capsimg.dll"), None)
            else:
                with tarfile.open(arch, "r:*") as t:
                    m = next((m for m in t.getmembers() if m.isfile() and Path(m.name).name.lower() == "capsimg.dll"), None)
                    data = t.extractfile(m).read() if m else None
            if not data:
                raise OSError("w paczce capsimg nie ma capsimg.dll")
            (part / "capsimg.dll").write_bytes(data)
        else:
            src = emucatalog.unpack(arch, Path(td) / "x", rel.get("strip_root", False))
            shutil.move(str(src), str(part))
            info_url = (rel.get("extra") or {}).get("info")
            if info_url:                      # pliki info/*.info — po nich EmuStart rozpoznaje rdzenie
                ia = Path(td) / "info.zip"
                emucatalog.download(info_url, ia)
                with zipfile.ZipFile(ia) as z:
                    z.extractall(part / "info")
    files = {}
    for dirpath, _dirs, names in os.walk(part):
        for n in names:
            p = Path(dirpath) / n
            files[p.relative_to(part).as_posix()] = [p.stat().st_size, _sha256(p)]
    (part / MANIFEST).write_text(json.dumps({"item": item, "version": rel["version"], "files": files}),
                                 encoding="utf-8")
    archive_size = _make_archive(part, files)
    for f in files:                          # na serwerze nic nie działa — zostaje samo archiwum
        (part / f).unlink()
    for d in sorted((p for p in part.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        try:
            d.rmdir()
        except OSError:
            pass
    shutil.rmtree(final, ignore_errors=True)
    os.replace(part, final)
    return {"version": rel["version"], "updated": time.time(), "files": len(files),
            "size": sum(v[0] for v in files.values()), "archive_size": archive_size, "source": rel["url"]}


def _prune(cfg: dict, item: str, keep: list) -> None:
    """Zostają podane wersje (bieżąca i poprzednia), reszta znika."""
    d = store_dir(cfg) / _safe(item)
    keep_names = {_safe(v) for v in keep if v}
    for sub in d.iterdir() if d.is_dir() else []:
        if sub.is_dir() and sub.name not in keep_names:
            shutil.rmtree(sub, ignore_errors=True)


def update(cfg: dict, items: list | None = None, force: bool = False) -> dict:
    """Sprawdza i pobiera nowe wersje (w tym wątku). items=None — wszystko, czego potrzebuje
    biblioteka. Zwraca {updated: [...], errors: {...}}."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "reason": "Aktualizacja emulatorów już trwa."}
    try:
        want = sorted(items if items is not None else needed(cfg))
        _job.update(running=True, step=0, steps=len(want), item="", done=0, total=0, log=[],
                    started=time.time(), finished=0.0)
        ensure_archives(cfg)
        _refresh_core_list(cfg)
        if items is None:
            want = sorted(needed(cfg))
            _job["steps"] = len(want)         # z rdzeniami dopiero co pobranymi z buildbota
        idx = load_index(cfg)
        updated, errors = [], {}
        for item in want:
            _job["step"] += 1
            _job["item"] = emucatalog.item_info(item)["label"]
            _job["done"] = _job["total"] = 0
            cur = idx["items"].get(item) or {}
            try:
                rel = emucatalog.latest(item)
                if not force and cur.get("version") == rel["version"] and manifest(cfg, item, rel["version"]):
                    continue
                entry = _build(cfg, item, rel, lambda d, t: _job.update(done=d, total=t))
                prev = cur.get("version", "")
                entry["previous"] = prev if prev != entry["version"] else cur.get("previous", "")
                idx["items"][item] = entry
                idx["errors"].pop(item, None)
                _save_index(cfg, idx)
                _prune(cfg, item, [entry["version"], entry["previous"]])
                updated.append(item)
                _job["log"].append(f"{_job['item']}: {prev or '—'} → {entry['version']}")
                log.info("emulatory: %s %s → %s", item, prev or "—", entry["version"])
            except Exception as ex:
                errors[item] = str(ex)[:300]
                idx["errors"][item] = errors[item]
                _job["log"].append(f"{_job['item']}: błąd — {errors[item]}")
                log.warning("emulatory: %s: %s", item, ex)
        idx["checked"] = time.time()
        _save_index(cfg, idx)
        return {"ok": True, "updated": updated, "errors": errors}
    finally:
        _job.update(running=False, item="", finished=time.time())
        _lock.release()


def _refresh_core_list(cfg: dict) -> None:
    """Lista rdzeni z buildbota i ich systemy (pliki .info) — raz na dobę."""
    idx = load_index(cfg)
    if idx.get("cores") and time.time() - float(idx.get("cores_at") or 0) < DAY - 600:
        return
    try:
        cores = emucatalog.all_cores()
        store_dir(cfg).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="emustore_", dir=store_dir(cfg)) as td:
            info = Path(td) / "info.zip"
            emucatalog.download(emucatalog.INFO_URL, info)
            dbs = emucatalog.core_databases(info)
    except Exception as ex:
        log.warning("emulatory: lista rdzeni z buildbota: %s", ex)
        return
    idx = load_index(cfg)
    idx.update(cores=cores, core_db=dbs, cores_at=time.time())
    _save_index(cfg, idx)
    log.info("emulatory: rdzeni RetroArcha na buildbocie: %d (z opisem systemów: %d)", len(cores), len(dbs))


def missing(cfg: dict) -> list:
    """Pozycje potrzebne, których magazyn jeszcze nie ma (np. system doszedł do biblioteki)."""
    idx = load_index(cfg)
    return sorted(i for i in needed(cfg) if i not in idx["items"] and i not in idx["errors"])


def start_update(cfg: dict, items: list | None = None, force: bool = False) -> dict:
    if _job["running"]:
        return {"ok": False, "reason": "Aktualizacja emulatorów już trwa."}
    threading.Thread(target=update, args=(cfg, items, force), daemon=True, name="emulatory").start()
    return {"ok": True}


def status(cfg: dict) -> dict:
    """Dla panelu: pozycje potrzebne bibliotece, wersje w magazynie, błędy, przebieg."""
    idx = load_index(cfg)
    need = needed(cfg)
    rows = []
    for item in sorted(set(need) | set(idx["items"])):
        if not emucatalog.valid_item(item):
            continue
        meta = idx["items"].get(item) or {}
        rows.append({"item": item, "label": emucatalog.item_info(item)["label"],
                     "systems": need.get(item, []), "version": meta.get("version", ""),
                     "previous": meta.get("previous", ""), "updated": meta.get("updated", 0),
                     "size": meta.get("size", 0), "error": idx["errors"].get(item, "")})
    return {"ok": True, "items": rows, "checked": idx.get("checked", 0), "dir": str(store_dir(cfg)),
            "job": dict(_job, log=list(_job["log"][-30:]))}


def scheduler(get_cfg) -> None:
    """Wątek serwera: raz dziennie sprawdza nowe wersje (pierwszy raz kilka minut po starcie,
    gdy magazyn jeszcze nic nie sprawdzał)."""
    def loop():
        try:
            with _lock:
                ensure_archives(get_cfg())
        except Exception:
            log.exception("emulatory: archiwa")
        time.sleep(300)
        while True:
            try:
                cfg = get_cfg()
                if time.time() - float(load_index(cfg).get("checked") or 0) >= DAY - 600:
                    update(cfg)
                else:
                    lack = missing(cfg)       # co godzinę: nowe systemy / rdzenie — od razu
                    if lack:
                        update(cfg, lack)
            except Exception:
                log.exception("emulatory: sprawdzanie codzienne")
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name="emulatory-codziennie").start()
