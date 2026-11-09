"""goginstall — gry GOG z archiwum depotów serwera EmuStart: plan, instalacja, wznowienie, weryfikacja, usuwanie.

Przepływ (żadnego pobierania całego archiwum, żadnej drugiej kopii gry w TEMP, żadnego GOG CDN):

    manifest buildu z serwera (przypięty hashem) + wybór języków i DLC
    → plan (depoty, kolizje plików między językami, rozmiar, miejsce)
    → chunk z serwera → MD5 skompresowanych danych → dekompresja (limit = rozmiar z planu) → MD5 danych
    → zapis wprost w pliku docelowym (offset z planu)
    → weryfikacja wszystkich plików (MD5 każdego fragmentu) → finalizacja (emustart/gogfinal.py)
    → stan „installed” w lokalnym rejestrze.

Pamięć: najwyżej `WORKERS * INFLIGHT` chunków naraz (chunk GOG ≤ ~10 MB). Jedyny plik tymczasowy to kontener
małych plików (SFC) jednego depotu — tylko dla plików, które nie mają własnych chunków (w archiwach GOG Managera
zwykle wszystkie mają); jego rozmiar wchodzi do sprawdzania miejsca.

Rejestr: `<dane>/gog/installs/<pid>.json` (stan, build, hash, katalog, języki, DLC, pliki, finalizacja,
uruchamianie), kopia manifestu: `<dane>/gog/manifests/<pid>/<build>.json` (weryfikacja i odinstalowanie bez
serwera). Gry GOG są trwałe: cache gier emulowanych (emustart/cache.py) ich nie dotyczy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import zlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from emustart import gogfinal, paths
from emustart.gogarchive import (BUILD_ID, HEX32, PRODUCT_ID, UnsafePath, depot_langs, norm_lang, safe_join,
                                 safe_rel)

log = logging.getLogger("emustart.goginstall")

WORKERS = 4                 # równoległe pobieranie chunków
INFLIGHT = 2                # chunków w toku na wątek (ograniczenie pamięci)
SPACE_MARGIN = 64 << 20
SFC_TMP = ".emustart-gog-sfc-{}.tmp"
_SFC_TMP_RE = re.compile(r"\.emustart-gog-sfc-\d+\.tmp")

ST_INSTALLING = "installing"
ST_INSTALLED = "installed"
ST_NEEDS_ADMIN = "needs_admin"
ST_FAILED = "failed"
ST_CANCELLED = "cancelled"
ST_DAMAGED = "damaged"
RESUMABLE = (ST_INSTALLING, ST_FAILED, ST_CANCELLED, ST_DAMAGED)


class GogError(Exception):
    pass


class Cancelled(GogError):
    pass


class SourceChanged(GogError):
    pass


class NoSpace(GogError):
    pass


class BadObject(GogError):
    """Obiekt z serwera nie zgadza się z manifestem (MD5, rozmiar, dekompresja) albo go brak."""


# ── źródło danych: serwer EmuStart ──────────────────────────────────────────────

class ServerSource:
    """Manifesty i chunki z serwera EmuStart (to samo połączenie i logowanie urządzenia co gry emulowane)."""

    def __init__(self, cfg: dict):
        from emustart import netsrc
        self.netsrc = netsrc
        self.ep = netsrc.endpoint(cfg)
        if not self.ep:
            raise GogError("To urządzenie nie jest połączone z serwerem EmuStart.")
        self._local = threading.local()

    def _get_json(self, path: str, timeout: float = 60) -> dict:
        st, body, _t = self.netsrc._request(self.ep, path, timeout=timeout, gzip=True)
        data = _loads(body)
        if st == 409:
            raise SourceChanged(data.get("error") or "build na serwerze zmienił się")
        if st == 403:
            raise GogError(data.get("error") or "To konto nie pobiera gier z serwera.")
        if st != 200:
            raise GogError(data.get("error") or f"serwer: HTTP {st}")
        return data

    def catalog(self) -> list:
        return self._get_json("/v1/gog/games", 30).get("games") or []

    def art(self, pid: str, kind: str) -> tuple:
        """(bajty, typ) grafiki gry z serwera albo (None, "") — brak grafiki / błąd."""
        st, body, _t = self.netsrc._request(self.ep, f"/v1/gog/games/{pid}/art/{kind}", timeout=60)
        if st != 200 or not body:
            return None, ""
        return body, ("image/png" if body[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg")

    def manifest(self, pid: str, build: str) -> dict:
        return self._get_json(f"/v1/gog/games/{pid}/builds/{build}/manifest", 120)

    def chunk(self, pid: str, build: str, h: str, md5: str) -> bytes:
        path = f"/v1/gog/chunks/{md5}?pid={pid}&build={build}&hash={h}"
        last: Exception | None = None
        for attempt in range(3):
            c = getattr(self._local, "conn", None)
            if c is None:
                c = self._local.conn = self.netsrc.conn(self.ep[0], self.ep[1], 60)
            try:
                c.request("GET", path, headers=self.netsrc.auth_headers(self.ep[2]))
                r = c.getresponse()
                body = r.read()
                if r.will_close:
                    self._drop()
            except OSError as ex:
                last = ex
                self._drop()
                time.sleep(0.3 * (attempt + 1))
                continue
            if r.status == 200:
                return body
            err = _loads(body).get("error") or f"HTTP {r.status}"
            if r.status == 409:
                raise SourceChanged(err)
            if r.status == 404:
                raise BadObject(f"serwer nie ma obiektu {md5[:12]}…: {err}")
            if r.status == 401 and attempt < 2:
                self.netsrc.drop_token()
                continue
            raise GogError(f"serwer: {err}")
        raise GogError(f"serwer nie odpowiada: {last}")

    def _drop(self) -> None:
        c = getattr(self._local, "conn", None)
        self._local.conn = None
        if c is not None:
            try:
                c.close()
            except OSError:
                pass


def _loads(body: bytes) -> dict:
    try:
        d = json.loads(body or b"{}")
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


# ── rejestr instalacji ──────────────────────────────────────────────────────────

def gog_dir() -> Path:
    return paths.DATA / "gog"


def _entry_path(pid: str) -> Path:
    if not PRODUCT_ID.match(str(pid or "")):
        raise GogError(f"zły identyfikator gry GOG: {pid!r}")
    return gog_dir() / "installs" / f"{pid}.json"


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}-{threading.get_ident()}")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def load_entry(pid: str) -> dict | None:
    try:
        return json.loads(_entry_path(pid).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        log.error("GOG: uszkodzony wpis rejestru %s", pid)
        return None


def save_entry(e: dict) -> None:
    e["updated"] = time.time()
    _atomic(_entry_path(e["product_id"]), json.dumps(e, ensure_ascii=False).encode("utf-8"))


def entries() -> list:
    d = gog_dir() / "installs"
    out = []
    for f in sorted(d.glob("*.json")) if d.is_dir() else []:
        if PRODUCT_ID.match(f.stem):
            e = load_entry(f.stem)
            if e:
                out.append(e)
    return out


def _bundle_path(pid: str, build: str) -> Path:
    if not BUILD_ID.match(str(build or "")):
        raise GogError(f"zły identyfikator buildu: {build!r}")
    return gog_dir() / "manifests" / str(pid) / f"{build}.json"


def save_bundle(b: dict) -> str:
    raw = json.dumps(b, ensure_ascii=False, sort_keys=True).encode("utf-8")
    _atomic(_bundle_path(b["product_id"], b["build_id"]), raw)
    return hashlib.sha256(raw).hexdigest()


def load_bundle(e: dict) -> dict:
    """Kopia manifestu zainstalowanego buildu — sprawdzona sumą z rejestru (praca bez serwera)."""
    try:
        raw = _bundle_path(e["product_id"], e["build_id"]).read_bytes()
    except FileNotFoundError:
        raise GogError("brak lokalnej kopii manifestu tej instalacji")
    if e.get("bundle_sha") and hashlib.sha256(raw).hexdigest() != e["bundle_sha"]:
        raise GogError("lokalna kopia manifestu jest zmieniona — weryfikacja niemożliwa")
    return json.loads(raw)


# ── plan ────────────────────────────────────────────────────────────────────────

def _lang_match(d: dict, wanted: set) -> bool:
    ls = depot_langs(d)
    return "*" in ls or bool(ls & wanted)


def _size(it: dict) -> int:
    ch = it.get("chunks") or []
    if ch:
        return sum(int(c.get("size") or 0) for c in ch)
    return int((it.get("sfcRef") or {}).get("size") or 0)


def _sig(it: dict):
    return it.get("md5") or tuple((c.get("md5"), c.get("size")) for c in it.get("chunks") or []) or None


def make_plan(bundle: dict, langs: list, primary: str = "", dlcs=(), base: bool = True, deps: bool = True) -> dict:
    """Plan instalacji z manifestu buildu i wyboru. Języki i DLC tylko z dostępnych na serwerze — bez
    cichej zamiany na angielski. `base=False` — tylko DLC (dogrywanie do istniejącej instalacji)."""
    rec = bundle.get("record") or {}
    pid = str(bundle["product_id"])
    avail = {norm_lang(x) for x in bundle.get("languages") or []}
    want = [norm_lang(x) for x in langs or [] if str(x).strip()]
    if not want:
        raise GogError("Wybierz co najmniej jeden język.")
    bad = [x for x in want if x not in avail]
    if bad:
        raise GogError(f"Na serwerze nie ma kompletnego języka: {', '.join(bad)} (dostępne: {', '.join(sorted(avail))}).")
    prim = norm_lang(primary) if primary else want[0]
    if prim not in want:
        raise GogError("Język główny musi być jednym z wybranych języków.")
    wanted = set(want)
    dlc_avail = {str(d["id"]): d for d in bundle.get("dlcs") or []}
    dlc_ids = [str(d) for d in dlcs or []]
    unknown = [d for d in dlc_ids if d not in dlc_avail]
    if unknown:
        raise GogError(f"DLC niedostępne na serwerze: {', '.join(unknown)}")
    depots = rec.get("depots") or []
    mans = (bundle.get("manifests") or {})
    sel: list = []                     # (depot, product)
    if base:
        b = [d for d in depots if str(d.get("productId")) == pid and _lang_match(d, wanted)]
        if not b:
            raise GogError("Archiwum nie ma plików gry dla wybranych języków.")
        sel += [(d, pid) for d in b]
    for did in dlc_ids:
        own = [d for d in depots if str(d.get("productId")) == did]
        s = [d for d in own if _lang_match(d, wanted)]
        if own and not s:
            raise GogError(f"DLC {dlc_avail[did]['name']} nie ma plików w wybranych językach.")
        sel += [(d, did) for d in s]
    groups = []
    for d, prod in sel:
        man = (mans.get("depot") or {}).get(d["manifest"])
        if man is None:
            raise GogError(f"brak manifestu depotu {d['manifest'][:12]}…")
        groups.append({"product": prod, "langs": depot_langs(d), "man": man})
    if deps and base:
        for d in rec.get("dependencies") or []:
            man = (mans.get("dep") or {}).get(d.get("manifest"))
            if man is None:
                raise GogError(f"brak manifestu zależności {d.get('id')}")
            groups.append({"product": f"dep:{d.get('id')}", "langs": {"*"}, "man": man})
    # pliki i katalogi; wszystkie ścieżki sprawdzone, zanim cokolwiek zostanie zapisane
    owners: dict = {}
    dirs: set = set()
    sfc: dict = {}
    for gi, g in enumerate(groups):
        dep = g["man"].get("depot") or {}
        if dep.get("smallFilesContainer"):
            sfc[gi] = (dep["smallFilesContainer"].get("chunks") or [])
        for it in dep.get("items") or []:
            try:
                rel = safe_rel(it.get("path"))
            except UnsafePath as ex:
                raise GogError(f"niebezpieczna ścieżka w manifeście: {ex}")
            t = it.get("type")
            if t == "DepotDirectory":
                dirs.add(rel)
            elif t == "DepotFile":
                ch = it.get("chunks") or []
                if ch and it.get("sfcRef") and _size(it) != int(it["sfcRef"].get("size") or 0):
                    raise GogError(f"niespójny manifest: {rel}")
                for c in ch:
                    if not HEX32.match(str(c.get("compressedMd5") or "").lower()) or int(c.get("size") or -1) < 0:
                        raise GogError(f"zły chunk w manifeście: {rel}")
                if not ch and it.get("sfcRef") and gi not in sfc:
                    raise GogError(f"plik {rel} wskazuje na kontener SFC, którego depot nie ma")
                owners.setdefault(rel.lower(), []).append((gi, rel, it))
    files, conflicts, dropped = [], 0, 0
    for key, cands in owners.items():
        win = cands[-1]
        if len(cands) > 1 and len({_sig(c[2]) for c in cands}) > 1:
            conflicts += 1
            pri = [c for c in cands if prim in groups[c[0]]["langs"]]
            neu = [c for c in cands if "*" in groups[c[0]]["langs"]]
            win = pri[-1] if pri else (neu[-1] if neu else None)
            if win is None:                    # wariant tylko innych języków — przełączyłby język gry
                dropped += 1
                continue
        gi, rel, it = win
        files.append({"path": rel, "size": _size(it), "chunks": list(it.get("chunks") or []),
                      "sfc": [gi, int(it["sfcRef"]["offset"]), int(it["sfcRef"]["size"])]
                      if (it.get("sfcRef") and not it.get("chunks")) else None,
                      "md5": it.get("md5") or "", "product": groups[gi]["product"]})
    files.sort(key=lambda f: f["path"].lower())
    sfc_need = {f["sfc"][0] for f in files if f["sfc"]}
    sfc_tmp = max((sum(int(c.get("size") or 0) for c in sfc[gi]) for gi in sfc_need), default=0)
    meta_dlcs = [d for d in dlc_ids if not any(g["product"] == d for g in groups)]
    return {"product_id": pid, "build_id": str(bundle["build_id"]), "hash": bundle["hash"],
            "languages": want, "primary": prim, "dlcs": dlc_ids, "meta_dlcs": meta_dlcs,
            "files": files, "dirs": sorted(dirs), "sfc": {str(k): v for k, v in sfc.items() if k in sfc_need},
            "total": sum(f["size"] for f in files), "sfc_tmp": sfc_tmp, "conflicts": conflicts,
            "dropped": dropped, "dependencies": [str(d.get("id")) for d in rec.get("dependencies") or []] if base else []}


def free_bytes(path: Path) -> int | None:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def space_needed(plan: dict, done: set) -> int:
    return sum(f["size"] for f in plan["files"] if f["path"] not in done) + plan["sfc_tmp"] + SPACE_MARGIN


# ── zapis plików ────────────────────────────────────────────────────────────────

def inflate(comp: bytes, c: dict) -> bytes:
    """Chunk z serwera → dane pliku, z kontrolą MD5 przed i po dekompresji oraz limitem rozmiaru z planu."""
    if hashlib.md5(comp).hexdigest() != str(c.get("compressedMd5") or "").lower():
        raise BadObject(f"chunk {str(c.get('compressedMd5'))[:12]}…: MD5 danych z serwera się nie zgadza")
    want = int(c.get("size") or 0)
    d = zlib.decompressobj(15)
    try:
        out = d.decompress(comp, want + 1)
    except zlib.error as ex:
        raise BadObject(f"chunk {c['compressedMd5'][:12]}…: dekompresja ({ex})")
    if len(out) != want or d.unconsumed_tail:
        raise BadObject(f"chunk {c['compressedMd5'][:12]}…: rozmiar po dekompresji ≠ {want}")
    if c.get("md5") and hashlib.md5(out).hexdigest() != str(c["md5"]).lower():
        raise BadObject(f"chunk {c['compressedMd5'][:12]}…: MD5 po dekompresji się nie zgadza")
    return out


def verify_file(root: Path, f: dict) -> str:
    """'' = plik zgodny z planem; inaczej opis problemu. Każdy fragment sprawdzany MD5 z manifestu."""
    try:
        p = safe_join(root, f["path"])
    except UnsafePath as ex:
        return str(ex)
    try:
        st = p.stat()
    except FileNotFoundError:
        return "brak pliku"
    if p.is_symlink() or not p.is_file():
        return "to nie jest zwykły plik"
    if st.st_size != f["size"]:
        return f"rozmiar {st.st_size} ≠ {f['size']}"
    with open(p, "rb") as fh:
        if f["chunks"]:
            for c in f["chunks"]:
                data = fh.read(int(c.get("size") or 0))
                if c.get("md5") and hashlib.md5(data).hexdigest() != str(c["md5"]).lower():
                    return "MD5 fragmentu się nie zgadza"
        elif f.get("md5"):
            h = hashlib.md5()
            for blk in iter(lambda: fh.read(1 << 20), b""):
                h.update(blk)
            if h.hexdigest() != f["md5"].lower():
                return "MD5 pliku się nie zgadza"
    return ""


class Progress:
    def __init__(self, total: int, cb=None):
        self.total, self.done, self.cb = total, 0, cb
        self.t0 = time.monotonic()
        self.lock = threading.Lock()

    def add(self, n: int) -> None:
        with self.lock:
            self.done += n
        if self.cb:
            self.cb(self.done, self.total)


def write_files(root: Path, plan: dict, source, done: set, cancel: threading.Event, prog: Progress,
                persist=None, workers: int = WORKERS) -> None:
    """Zapis plików planu, których nie ma w `done`. Chunki z serwera w ograniczonej liczbie naraz;
    plik oznaczany jako ukończony dopiero, gdy wszystkie jego fragmenty są zapisane."""
    pid, build, h = plan["product_id"], plan["build_id"], plan["hash"]
    todo = [f for f in plan["files"] if f["path"] not in done]
    dests = {f["path"]: safe_join(root, f["path"]) for f in plan["files"]}
    for d in plan["dirs"]:
        safe_join(root, d).mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    remaining: dict = {}
    last_persist = [0.0]
    abort = threading.Event()               # błąd jednego chunka zatrzymuje pozostałe

    def finished(rel: str) -> None:
        with lock:
            done.add(rel)
            if persist and time.monotonic() - last_persist[0] > 2:
                last_persist[0] = time.monotonic()
                persist()

    def job(rel: str, off: int, c: dict) -> None:
        if cancel.is_set() or abort.is_set():
            return
        try:
            data = inflate(source.chunk(pid, build, h, str(c["compressedMd5"]).lower()), c)
        except BaseException:
            abort.set()
            raise
        with open(dests[rel], "r+b") as fh:
            fh.seek(off)
            fh.write(data)
        prog.add(len(data))
        with lock:
            remaining[rel] -= 1
            last = remaining[rel] == 0
        if last:
            finished(rel)

    tasks = []
    for f in todo:
        if f["sfc"]:
            continue
        dest = dests[f["path"]]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink():
            raise GogError(f"{f['path']}: w katalogu gry jest dowiązanie zamiast pliku")
        with open(dest, "wb") as fh:
            fh.truncate(f["size"])
        if not f["chunks"]:
            finished(f["path"])
            continue
        remaining[f["path"]] = len(f["chunks"])
        off = 0
        for c in f["chunks"]:
            tasks.append((f["path"], off, c))
            off += int(c.get("size") or 0)
    if tasks:
        limit = max(1, workers) * INFLIGHT
        with ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="gog-chunk") as ex:
            pending = set()
            try:
                for t in tasks:
                    if cancel.is_set() or abort.is_set():
                        break
                    pending.add(ex.submit(job, *t))
                    if len(pending) >= limit:            # ograniczona liczba chunków w pamięci
                        fin, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for fu in fin:
                            fu.result()
                fin, _ = wait(pending)
                for fu in fin:
                    fu.result()
            except BaseException:
                abort.set()
                for fu in pending:
                    fu.cancel()
                raise
    if cancel.is_set():
        raise Cancelled("przerwano")
    # pliki tylko w kontenerze małych plików: kontener jednego depotu w pliku tymczasowym, potem cięcie
    by_group: dict = {}
    for f in todo:
        if f["sfc"]:
            by_group.setdefault(str(f["sfc"][0]), []).append(f)
    for gi, fs in by_group.items():
        tmp = root / SFC_TMP.format(gi)
        try:
            with open(tmp, "wb") as fh:
                for c in plan["sfc"][gi]:
                    if cancel.is_set():
                        raise Cancelled("przerwano")
                    fh.write(inflate(source.chunk(pid, build, h, str(c["compressedMd5"]).lower()), c))
            with open(tmp, "rb") as fh:
                for f in fs:
                    _g, off, size = f["sfc"]
                    fh.seek(off)
                    data = fh.read(size)
                    if len(data) != size:
                        raise BadObject(f"{f['path']}: kontener SFC krótszy niż manifest")
                    dests[f["path"]].parent.mkdir(parents=True, exist_ok=True)
                    dests[f["path"]].write_bytes(data)
                    prog.add(size)
                    finished(f["path"])
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    if persist:
        persist()


# ── zadania: instalacja, wznowienie, DLC, weryfikacja, usuwanie ─────────────────

def _empty_or_ours(d: Path, pid: str) -> bool:
    if not d.exists():
        return True
    if not d.is_dir():
        return False
    other = [p for p in d.iterdir()]
    if not other:
        return True
    e = load_entry(pid)
    return bool(e and Path(e["install_dir"]) == d)


def target_for(base_dir: str, bundle_or_game: dict) -> Path:
    """Katalog gry: <wspólny katalog GOG>/<installDirectory z archiwum> (nazwa sprawdzona jak ścieżka z manifestu)."""
    name = str(bundle_or_game.get("install_directory") or bundle_or_game.get("product_id"))
    try:
        rel = safe_rel(name)
    except UnsafePath:
        rel = str(bundle_or_game.get("product_id"))
    if "/" in rel:
        rel = rel.replace("/", " - ")
    return Path(base_dir) / rel


def install(source, pid: str, build: str, langs: list, primary: str, dlcs: list, install_dir: str,
            cancel: threading.Event | None = None, on_progress=None, on_phase=None) -> dict:
    """Nowa instalacja. Przerwana instalacja z tym samym wyborem (build, języki, język główny, DLC, katalog)
    jest wznawiana; z innym wyborem — jej pliki są usuwane i instalacja zaczyna się od nowa."""
    cancel = cancel or threading.Event()
    phase = on_phase or (lambda *_a: None)
    e = load_entry(pid)
    if e and e.get("state") in (ST_INSTALLED, ST_NEEDS_ADMIN):
        raise GogError("Ta gra jest już zainstalowana.")
    if e and e.get("state") == ST_DAMAGED:
        raise GogError("Ta gra jest zainstalowana, ale uszkodzona — napraw ją albo usuń przed ponowną instalacją.")
    phase("manifest")
    bundle = source.manifest(pid, build)
    if str(bundle.get("product_id")) != str(pid) or str(bundle.get("build_id")) != str(build):
        raise GogError("serwer zwrócił manifest innej gry")
    plan = make_plan(bundle, langs, primary, dlcs)
    root = Path(install_dir)
    if not root.is_absolute():
        raise GogError("Katalog instalacji musi być pełną ścieżką.")
    resume = False
    if e and e.get("state") in RESUMABLE:
        if _same_choice(e, build, plan, root):
            if bundle.get("hash") != e.get("hash"):
                raise SourceChanged("Build na serwerze zmienił się od rozpoczęcia instalacji — przerwanej instalacji "
                                    "nie da się dokończyć innymi danymi. Usuń ją i zainstaluj ponownie.")
            resume = True
        else:
            log.info("GOG %s: inny wybór niż w przerwanej instalacji — usuwam jej pliki i zaczynam od nowa", pid)
            discard(pid)
            e = None
    if not resume and not _empty_or_ours(root, pid):
        raise GogError(f"Katalog {root} nie jest pusty — wybierz inny.")
    done = _still_done(root, plan, e.get("done") if resume else None)
    need, free = space_needed(plan, done), free_bytes(root)
    if free is not None and free < need:
        raise NoSpace(f"Za mało miejsca: potrzeba {_human(need)} (z plikiem tymczasowym {_human(plan['sfc_tmp'])}), "
                      f"wolne {_human(free)}.")
    sha = save_bundle(bundle)
    e = {"product_id": pid, "title": bundle.get("title") or pid, "build_id": build, "version": bundle.get("version") or "",
         "hash": bundle["hash"], "bundle_sha": sha, "install_dir": str(root), "languages": plan["languages"],
         "primary": plan["primary"], "dlcs": plan["dlcs"], "dependencies": plan["dependencies"],
         "state": ST_INSTALLING, "done": sorted(done), "files": [[f["path"], f["size"]] for f in plan["files"]],
         "dirs": plan["dirs"], "created_files": (e or {}).get("created_files") or [],
         "started": (e or {}).get("started") or time.time(), "error": "", "finalize": {}}
    save_entry(e)                              # znacznik instalacji PRZED pierwszym zapisanym plikiem
    root.mkdir(parents=True, exist_ok=True)
    return _run(source, e, plan, bundle, done, cancel, on_progress, phase)


def resume(source, pid: str, cancel=None, on_progress=None, on_phase=None) -> dict:
    """Wznowienie przerwanej instalacji z wyborem zapisanym w rejestrze."""
    e = load_entry(pid)
    if not e or e.get("state") not in (ST_INSTALLING, ST_FAILED, ST_CANCELLED):
        raise GogError("Brak przerwanej instalacji tej gry.")
    return install(source, pid, e["build_id"], e["languages"], e["primary"], e["dlcs"], e["install_dir"],
                   cancel, on_progress, on_phase)


def _same_choice(e: dict, build: str, plan: dict, root: Path) -> bool:
    return (str(e.get("build_id")) == str(build)
            and os.path.normcase(os.path.abspath(e.get("install_dir") or "")) == os.path.normcase(os.path.abspath(root))
            and set(e.get("languages") or []) == set(plan["languages"])
            and (e.get("primary") or "") == plan["primary"]
            and set(e.get("dlcs") or []) == set(plan["dlcs"]))


def _still_done(root: Path, plan: dict, done_list) -> set:
    """Ukończone wcześniej pliki planu, które nadal mają rozmiar z planu (reszta pobiera się od nowa)."""
    sizes = {f["path"]: f["size"] for f in plan["files"]}
    done = set(done_list or []) & set(sizes)
    for rel in list(done):
        try:
            if safe_join(root, rel).stat().st_size != sizes[rel]:
                done.discard(rel)
        except (OSError, UnsafePath):
            done.discard(rel)
    return done


def _run(source, e: dict, plan: dict, bundle: dict, done: set, cancel, on_progress, phase, mode: str = "install") -> dict:
    """mode: install | dlc | repair. Błąd albo przerwanie DLC/naprawy nie zmienia stanu zainstalowanej gry."""
    root = Path(e["install_dir"])
    prog = Progress(plan["total"], on_progress)
    prog.add(sum(f["size"] for f in plan["files"] if f["path"] in done))
    dlc_only = mode == "dlc"

    def persist():
        if dlc_only:
            e["pending_dlc"]["done"] = sorted(done)
        else:
            e["done"] = sorted(done)
        save_entry(e)

    try:
        phase("download")
        write_files(root, plan, source, done, cancel, prog, persist)
        phase("verify")
        bad = [(f["path"], why) for f in plan["files"] if (why := verify_file(root, f))]
        if bad:
            for rel, _w in bad:
                done.discard(rel)
            raise GogError(f"Weryfikacja: {len(bad)} plików niezgodnych z manifestem (np. {bad[0][0]}: {bad[0][1]}) — "
                           "wznów instalację, aby je pobrać ponownie.")
    except Cancelled:
        persist()
        _set(e, ST_CANCELLED if mode == "install" else None,
             "Przerwano — postęp zapisany, " + ("naprawę" if mode == "repair" else "instalację") + " można wznowić.")
        raise
    except Exception as ex:
        persist()
        _set(e, ST_FAILED if mode == "install" else None, str(ex))
        raise
    if mode == "repair":
        # pliki znów zgodne z manifestem; finalizacja (rejestr, zapisy, krok administratora) zostaje bez zmian
        e["state"] = e.pop("state_before_damage", ST_INSTALLED)
        e["done"], e["error"] = [], ""
        save_entry(e)
        log.info("GOG %s: naprawiono (%s)", e["product_id"], root)
        return e
    phase("finalize")
    return finalize(e, plan, bundle, dlc_only)


def _set(e: dict, state: str | None, error: str) -> None:
    if state:
        e["state"] = state
    e["error"] = error
    save_entry(e)


def _dlc_names(bundle: dict) -> dict:
    return {str(d["id"]): d.get("name") or d["id"] for d in bundle.get("dlcs") or []}


def finalize(e: dict, plan: dict, bundle: dict, dlc_only: bool = False, hkcu=None) -> dict:
    """Kroki po zapisie plików: opis DLC bez plików, akcje skryptów, uruchamianie. Stan „installed” dopiero
    po wszystkich obowiązkowych krokach; HKLM → „needs_admin”."""
    root = Path(e["install_dir"])
    pid = e["product_id"]
    names = _dlc_names(bundle)
    try:
        for did in plan["meta_dlcs"]:
            info = root / f"goggame-{did}.info"
            if not info.exists():
                info.write_text(json.dumps({"gameId": did, "rootGameId": pid, "name": names.get(did, did),
                                            "buildId": e["build_id"], "playTasks": []}, indent=2), encoding="utf-8")
                e.setdefault("created_files", []).append(str(info))
        fin = {"done": [], "admin": [], "skipped": [], "saves": [], "created": [], "hkcu": []}
        for prod in ([pid] if not dlc_only else []) + list(plan["dlcs"]):
            r = gogfinal.apply_script(root, prod, **({"hkcu": hkcu} if hkcu else {}))
            for k in fin:
                fin[k] += r[k]
        e.setdefault("created_files", []).extend(fin.pop("created"))
        if not dlc_only:
            if not (root / f"goggame-{pid}.info").exists():
                raise gogfinal.FinalizeError(f"build nie ma goggame-{pid}.info — brak opisu uruchamiania")
            e["launch"] = gogfinal.launch_spec(root, pid)
            e["runtimes"] = [{"id": s["id"], "done": False} for s in gogfinal.runtime_steps(root, e.get("dependencies") or [])]
    except (gogfinal.FinalizeError, OSError, ValueError) as ex:
        if dlc_only:
            e["error"] = f"Finalizacja DLC nie powiodła się: {ex}"
        else:
            e["state"], e["error"] = ST_FAILED, f"Finalizacja nie powiodła się: {ex} — wznów, aby spróbować ponownie."
        save_entry(e)
        raise GogError(e["error"])
    if dlc_only:
        e["dlcs"] = sorted(set(e["dlcs"]) | set(plan["dlcs"]))
        have = {p for p, _s in e["files"]}
        e["files"] += [[f["path"], f["size"]] for f in plan["files"] if f["path"] not in have]
        e["dirs"] = sorted(set(e.get("dirs") or []) | set(plan["dirs"]))
        e.pop("pending_dlc", None)
        e["finalize"].setdefault("saves", [])
        e["finalize"]["saves"] = sorted(set(e["finalize"]["saves"]) | set(fin["saves"]))
        e["finalize"].setdefault("admin", [])
        e["finalize"]["admin"] += fin["admin"]
        e["finalize"]["hkcu"] = (e["finalize"].get("hkcu") or []) + fin["hkcu"]
        if fin["admin"]:
            e["state"] = ST_NEEDS_ADMIN
    else:
        e["finalize"] = fin
        e["state"] = ST_NEEDS_ADMIN if fin["admin"] else ST_INSTALLED
        e["done"] = []
        e["installed_at"] = time.time()
    e["error"] = ""
    save_entry(e)
    log.info("GOG %s: %s (%s)", pid, e["state"], root)
    return e


def add_dlc(source, pid: str, dlcs: list, cancel: threading.Event | None = None, on_progress=None, on_phase=None) -> dict:
    """DLC do istniejącej instalacji — tylko z tego samego buildu (ten sam hash), w językach instalacji."""
    cancel = cancel or threading.Event()
    phase = on_phase or (lambda *_a: None)
    e = load_entry(pid)
    if not e or e.get("state") not in (ST_INSTALLED, ST_NEEDS_ADMIN):
        raise GogError("Najpierw zainstaluj grę.")
    pend = e.get("pending_dlc") or {}
    want = sorted(set(pend.get("dlcs") or []) | {str(d) for d in dlcs or []}) if pend else sorted({str(d) for d in dlcs or []})
    want = [d for d in want if d not in set(e["dlcs"])]
    if not want:
        raise GogError("Wybrane DLC są już zainstalowane.")
    phase("manifest")
    bundle = source.manifest(pid, e["build_id"])
    if bundle.get("hash") != e["hash"]:
        raise SourceChanged("Serwer ma inny build tej gry niż zainstalowany — DLC z innego buildu nie zostanie "
                            "doinstalowane (aktualizacja gry nie jest jeszcze obsługiwana).")
    plan = make_plan(bundle, e["languages"], e["primary"], want, base=False)
    have = {p.lower() for p, _s in e["files"]}
    clash = [f["path"] for f in plan["files"] if f["path"].lower() in have and f["product"] != pid]
    root = Path(e["install_dir"])
    # plik DLC, który podmieniłby plik gry innym wariantem — zgoda tylko, gdy treść jest identyczna
    clash = [rel for rel in clash if verify_file(root, next(f for f in plan["files"] if f["path"] == rel))]
    if clash:
        raise GogError(f"DLC nadpisałoby {len(clash)} plików gry inną treścią (np. {clash[0]}) — nie doinstalowuję.")
    done = set(pend.get("done") or []) if pend else set()
    need, free = space_needed(plan, done), free_bytes(root)
    if free is not None and free < need:
        raise NoSpace(f"Za mało miejsca na DLC: potrzeba {_human(need)}, wolne {_human(free)}.")
    e["pending_dlc"] = {"dlcs": want, "done": sorted(done),
                        "files": [[f["path"], f["size"]] for f in plan["files"] if f["path"].lower() not in have],
                        "dirs": plan["dirs"]}
    save_entry(e)
    return _run(source, e, plan, bundle, done, cancel, on_progress, phase, mode="dlc")


def verify(pid: str, deep: bool = True) -> dict:
    """Pliki zainstalowanej gry zgodne z kopią manifestu (bez serwera). Niezgodne → stan „damaged”, naprawa
    = wznowienie (pobiera tylko złe pliki)."""
    e = load_entry(pid)
    if not e:
        raise GogError("Ta gra nie jest zainstalowana.")
    bundle = load_bundle(e)
    plan = make_plan(bundle, e["languages"], e["primary"], e["dlcs"])
    root = Path(e["install_dir"])
    bad = [(f["path"], why) for f in plan["files"] if (why := verify_file(root, f) if deep else
                                                      ("" if _size_ok(root, f) else "rozmiar"))]
    if bad and e["state"] in (ST_INSTALLED, ST_NEEDS_ADMIN):
        e["state_before_damage"] = e["state"]
        e["state"] = ST_DAMAGED
        e["done"] = sorted({f["path"] for f in plan["files"]} - {b[0] for b in bad})
        e["error"] = f"{len(bad)} plików niezgodnych z manifestem — napraw (pobierze tylko te pliki z serwera)."
        save_entry(e)
    elif not bad and e["state"] == ST_DAMAGED:
        e["state"] = e.pop("state_before_damage", ST_INSTALLED)
        e["error"] = ""
        e["done"] = []
        save_entry(e)
    return {"ok": not bad, "checked": len(plan["files"]), "bad": [{"path": p, "why": w} for p, w in bad[:50]],
            "bad_count": len(bad)}


def _size_ok(root: Path, f: dict) -> bool:
    try:
        return safe_join(root, f["path"]).stat().st_size == f["size"]
    except (OSError, UnsafePath):
        return False


def repair(source, pid: str, cancel=None, on_progress=None, on_phase=None) -> dict:
    """Naprawa uszkodzonej instalacji: pobiera z serwera tylko niezgodne pliki (ten sam przypięty build)."""
    cancel = cancel or threading.Event()
    phase = on_phase or (lambda *_a: None)
    e = load_entry(pid)
    if not e or e.get("state") != ST_DAMAGED:
        raise GogError("Najpierw sprawdź pliki gry (weryfikacja).")
    phase("manifest")
    bundle = source.manifest(pid, e["build_id"])
    if bundle.get("hash") != e.get("hash"):
        raise SourceChanged("Serwer ma inny build tej gry niż zainstalowany — naprawa pobrałaby pliki innej wersji. "
                            "Usuń grę i zainstaluj ponownie (zapisy w folderze zapisów zostają).")
    plan = make_plan(bundle, e["languages"], e["primary"], e["dlcs"])
    root = Path(e["install_dir"])
    done = _still_done(root, plan, e.get("done"))
    need, free = space_needed(plan, done), free_bytes(root)
    if free is not None and free < need:
        raise NoSpace(f"Za mało miejsca na naprawę: potrzeba {_human(need)}, wolne {_human(free)}.")
    return _run(source, e, plan, bundle, done, cancel, on_progress, phase, mode="repair")


def uninstall(pid: str, remove_hkcu=None) -> dict:
    """Usuwa tylko pliki zarządzane przez EmuStart (z manifestu i utworzone przy finalizacji), puste katalogi
    z manifestu i pusty katalog gry. Zapisy (savePath), pliki dodane przez grę lub gracza i wszystko poza
    katalogiem gry zostają. Archiwum na serwerze nie jest dotykane."""
    e = load_entry(pid)
    if not e:
        raise GogError("Ta gra nie jest zainstalowana.")
    root = Path(e["install_dir"])
    removed, left, kept_errors = _remove_managed(e)
    if kept_errors:
        e["error"] = f"Nie usunięto {len(kept_errors)} plików (np. {kept_errors[0]}) — zamknij grę i spróbuj ponownie."
        save_entry(e)
        raise GogError(e["error"])
    _forget(e, remove_hkcu)
    return {"removed": removed, "left": left, "dir": str(root), "dir_removed": not root.exists()}


def discard(pid: str, remove_hkcu=None) -> None:
    """Porzucenie przerwanej instalacji (przed instalacją z innym wyborem): jej pliki jak przy odinstalowaniu."""
    e = load_entry(pid)
    if not e:
        return
    _removed, _left, errors = _remove_managed(e)
    if errors:
        raise GogError(f"Nie usunięto plików przerwanej instalacji ({len(errors)}, np. {errors[0]}) — "
                       "zamknij programy, które ich używają, i spróbuj ponownie.")
    _forget(e, remove_hkcu)


def _forget(e: dict, remove_hkcu=None) -> None:
    # wartości rejestru HKCU ustawione przez finalizację tej gry
    (remove_hkcu or gogfinal.remove_hkcu)((e.get("finalize") or {}).get("hkcu") or [])
    try:
        _entry_path(e["product_id"]).unlink()
    except FileNotFoundError:
        pass
    shutil.rmtree(gog_dir() / "manifests" / str(e["product_id"]), ignore_errors=True)


def _remove_managed(e: dict) -> tuple:
    """(usunięte, pozostałe pliki, błędy). Pliki z rejestru (gra, niedokończone DLC, utworzone przy finalizacji,
    pliki tymczasowe SFC), potem puste katalogi z manifestu i pusty katalog gry."""
    root = Path(e["install_dir"])
    saves = [Path(s) for s in (e.get("finalize") or {}).get("saves") or []]
    pend = e.get("pending_dlc") or {}
    removed, kept_errors = 0, []

    def protected(p: Path) -> bool:
        ap = Path(os.path.abspath(p))
        for s in saves:
            try:
                ap.relative_to(Path(os.path.abspath(s)))
                return True
            except ValueError:
                pass
        return False

    if root.is_dir() and not root.is_symlink():
        rels = [p for p, _s in (e.get("files") or []) + (pend.get("files") or [])]
        rels += [os.path.relpath(c, root).replace("\\", "/") for c in e.get("created_files") or []
                 if _is_inside(Path(c), root)]
        rels += [p.name for p in root.glob(".emustart-gog-sfc-*.tmp") if _SFC_TMP_RE.fullmatch(p.name)]
        for rel in rels:
            try:
                p = safe_join(root, rel)
            except UnsafePath:
                continue
            if protected(p) or _has_link(root, p):
                continue
            try:
                if p.is_file():
                    p.unlink()
                    removed += 1
            except OSError as ex:
                kept_errors.append(f"{rel}: {ex}")
        dirs = set(e.get("dirs") or []) | set(pend.get("dirs") or [])
        for rel, _s in (e.get("files") or []) + (pend.get("files") or []):
            parts = rel.split("/")[:-1]
            for i in range(1, len(parts) + 1):
                dirs.add("/".join(parts[:i]))
        for rel in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
            try:
                p = safe_join(root, rel)
                # puste katalogi (także pusty folder zapisów) — w niepustym nic nie ruszamy
                if not _has_link(root, p) and p.is_dir() and not any(p.iterdir()):
                    p.rmdir()
            except (OSError, UnsafePath):
                pass
    left = sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0
    if root.is_dir() and not any(root.iterdir()) and not root.is_symlink():
        try:
            root.rmdir()
        except OSError:
            pass
    return removed, left, kept_errors


def _is_inside(p: Path, root: Path) -> bool:
    try:
        Path(os.path.abspath(p)).relative_to(Path(os.path.abspath(root)))
        return True
    except ValueError:
        return False


def _has_link(root: Path, p: Path) -> bool:
    """Dowiązanie albo punkt połączenia po drodze od katalogu gry do pliku — nie idziemy za nim."""
    cur = p
    root_abs = Path(os.path.abspath(root))
    while Path(os.path.abspath(cur)) != root_abs:
        try:
            if cur.is_symlink() or (os.name == "nt" and _reparse(cur)):
                return True
        except OSError:
            return True
        if cur.parent == cur:
            break
        cur = cur.parent
    return False


def _reparse(p: Path) -> bool:
    try:
        return bool(os.lstat(p).st_file_attributes & 0x400)      # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False


def _human(n: int) -> str:
    v = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if v < 1024:
            return f"{v:.0f} {u}" if u == "B" else f"{v:.1f} {u}"
        v /= 1024
    return f"{v:.1f} TB"
