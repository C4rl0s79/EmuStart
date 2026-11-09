"""gogarchive — archiwum depotów GOG na serwerze: czytnik formatu i indeks (tylko odczyt).

Archiwum tworzy GOG Manager (D:\\GIT\\GogGui, `depot_archive.py`, format `goggui-depot-archive` v1) —
EmuStart go nie zapisuje, nie przepakowuje i nie uzupełnia (żadnych zapytań do GOG). Układ:

    archive.json                         {"format": "goggui-depot-archive", "version": 1}
    chunks/ab/cd/<compressedMd5>         skompresowany chunk (zlib), nazwa = MD5 skompresowanych danych
    manifests/depot/ab/cd/<id>.json      manifest depotu (Galaxy content-system v2, zdekodowany JSON)
    manifests/dep/ab/cd/<id>.json        manifest depotu zależności (redist: __redist/…)
    products/<pid>/<build>.meta.json     meta buildu Galaxy (zawiera clientId/clientSecret — nie publikujemy)
    products/<pid>/<build>.json          rekord: zakres (języki, DLC), depoty, zależności, state

Indeks (`build_index`) jest odtwarzalną warstwą nad tymi plikami: dla każdego rekordu sprawdza stan „complete”,
obecność manifestów i KAŻDEGO chunka (z rozmiarem) i liczy hash zawartości buildu (`content_hash`) — klient
instaluje build przypięty do tego hasha, serwer wydaje chunki tylko z przypiętego, nadal zgodnego buildu.
Niekompletny rekord nie jest publikowany (panel pokazuje powód).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path, PurePosixPath

log = logging.getLogger("emustart.gogarchive")

FORMAT = "goggui-depot-archive"
VERSION = 1

HEX32 = re.compile(r"^[0-9a-f]{32}$")
PRODUCT_ID = re.compile(r"^[0-9]{1,20}$")
BUILD_ID = re.compile(r"^[0-9]{1,24}$")
MANIFEST_ID = re.compile(r"^[0-9a-f]{32}$")
_WIN_RESERVED = {"con", "prn", "aux", "nul", "conin$", "conout$",
                 *(f"com{i}" for i in range(0, 10)), *(f"lpt{i}" for i in range(0, 10))}

# klucze, których nie wysyłamy klientom ani nie logujemy (meta buildu ma clientSecret gry)
SECRET_KEYS = {"clientsecret", "client_secret", "clientid", "client_id", "access_token", "refresh_token",
               "session_id", "token", "cookie", "cookies", "authorization", "secure_link", "securelink",
               "password"}


class ArchiveError(Exception):
    """Archiwum nie ma takiego obiektu albo jest w złym formacie."""


class UnsafePath(ValueError):
    """Ścieżka z manifestu, która wyszłaby poza katalog gry albo trafiła w urządzenie Windows."""


# ── bezpieczeństwo ścieżek (te same reguły co GOG Manager, plus spacje/kropki na końcu nazwy) ──

def safe_rel(path: str) -> str:
    """Ścieżka z manifestu → znormalizowana względna (z „/”) albo UnsafePath."""
    s = str(path or "").replace("\\", "/")
    if "\x00" in s or any(ord(ch) < 32 for ch in s):
        raise UnsafePath(f"znak sterujący w ścieżce: {path!r}")
    if s.startswith("//") or re.match(r"^[A-Za-z]:", s):
        raise UnsafePath(f"ścieżka bezwzględna: {path!r}")
    parts = []
    for p in s.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            raise UnsafePath(f"'..' w ścieżce: {path!r}")
        if ":" in p or any(ch in p for ch in '<>"|?*'):
            raise UnsafePath(f"niedozwolony znak w ścieżce: {path!r}")
        if p != p.rstrip(" ."):
            raise UnsafePath(f"nazwa kończy się spacją lub kropką: {path!r}")
        if p.split(".")[0].strip().lower() in _WIN_RESERVED:
            raise UnsafePath(f"zarezerwowana nazwa Windows: {path!r}")
        parts.append(p)
    if not parts:
        raise UnsafePath(f"pusta ścieżka: {path!r}")
    return "/".join(parts)


def safe_join(root: Path, rel: str) -> Path:
    """root / safe_rel(rel), sprawdzone także po rozwinięciu dowiązań."""
    r = safe_rel(rel)
    root_res = Path(root).resolve()
    dest = (root_res / PurePosixPath(r)).resolve()
    try:
        dest.relative_to(root_res)
    except ValueError:
        raise UnsafePath(f"poza katalogiem docelowym: {rel!r}")
    return Path(root) / PurePosixPath(r)


def strip_secrets(obj):
    if isinstance(obj, dict):
        return {k: strip_secrets(v) for k, v in obj.items() if str(k).lower() not in SECRET_KEYS}
    if isinstance(obj, list):
        return [strip_secrets(v) for v in obj]
    return obj


def _fan(h: str) -> str:
    return f"{h[0:2]}/{h[2:4]}/{h}"


def manifest_items(man: dict) -> list:
    return ((man or {}).get("depot") or {}).get("items") or []


def manifest_chunks(man: dict) -> list:
    """Chunki, do których odwołuje się manifest depotu (pliki + kontener małych plików)."""
    out = []
    dep = (man or {}).get("depot") or {}
    for it in dep.get("items") or []:
        if it.get("type") == "DepotFile":
            out.extend(it.get("chunks") or [])
    out.extend(((dep.get("smallFilesContainer") or {}).get("chunks")) or [])
    return out


def norm_lang(code: str) -> str:
    """'en-US' → 'en', 'zh-Hans' → 'zh' (jak GOG Manager: zakres archiwum trzyma krótkie kody)."""
    return str(code or "").lower().replace("_", "-").split("-")[0]


def depot_langs(d: dict) -> set:
    return {"*" if x == "*" else norm_lang(x) for x in (d.get("languages") or [])}


class Archive:
    """Jedno archiwum (folder). Tylko odczyt."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def check_format(self) -> None:
        try:
            marker = json.loads((self.root / "archive.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ArchiveError("brak archive.json — to nie jest archiwum depotów GOG Managera")
        except (OSError, ValueError) as ex:
            raise ArchiveError(f"archive.json nieczytelny: {ex}")
        if marker.get("format") != FORMAT:
            raise ArchiveError(f"obcy format archiwum: {marker.get('format')!r}")
        if int(marker.get("version") or 0) > VERSION:
            raise ArchiveError(f"archiwum w wersji {marker.get('version')} — EmuStart obsługuje {VERSION}")

    def chunk_path(self, md5: str) -> Path:
        m = str(md5 or "").lower()
        if not HEX32.match(m):
            raise ArchiveError(f"zły identyfikator chunka: {md5!r}")
        return self.root / "chunks" / _fan(m)

    def manifest_path(self, kind: str, mid: str) -> Path:
        if kind not in ("depot", "dep") or not MANIFEST_ID.match(str(mid or "")):
            raise ArchiveError(f"zły identyfikator manifestu: {kind} {mid!r}")
        return self.root / "manifests" / kind / f"{_fan(mid)}.json"

    def record_path(self, pid: str, build: str, suffix: str = ".json") -> Path:
        if not PRODUCT_ID.match(str(pid or "")) or not BUILD_ID.match(str(build or "")):
            raise ArchiveError(f"zły identyfikator gry/buildu: {pid!r}/{build!r}")
        return self.root / "products" / str(pid) / f"{build}{suffix}"

    def _read(self, p: Path) -> bytes:
        try:
            return p.read_bytes()
        except FileNotFoundError:
            raise ArchiveError(f"brak pliku {p.relative_to(self.root).as_posix()}")

    def read_json(self, p: Path):
        try:
            return json.loads(self._read(p))
        except ValueError as ex:
            raise ArchiveError(f"{p.name}: zły JSON ({ex})")

    def manifest(self, kind: str, mid: str) -> dict:
        return self.read_json(self.manifest_path(kind, mid))

    def records(self) -> list:
        """(pid, build) wszystkich rekordów (bez .meta.json)."""
        base = self.root / "products"
        out = []
        if not base.is_dir():
            return out
        for d in sorted(base.iterdir()):
            if not d.is_dir() or not PRODUCT_ID.match(d.name):
                continue
            for f in sorted(d.glob("*.json")):
                if f.name.endswith(".meta.json"):
                    continue
                if BUILD_ID.match(f.stem):
                    out.append((d.name, f.stem))
        return out


# ── wpis indeksu ───────────────────────────────────────────────────────────────

def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


_VOLATILE = {"created", "verified", "updated"}


def content_hash(rec: dict, meta: dict, manifests: dict) -> str:
    """Hash zawartości buildu: rekord (bez dat), meta (bez sekretów) i wszystkie manifesty. Zmiana któregokolwiek
    = inny build dla klienta (instalacja w toku kończy się błędem, nie mieszanką)."""
    h = hashlib.sha256()
    h.update(_canon({k: v for k, v in rec.items() if k not in _VOLATILE}))
    h.update(_canon(strip_secrets(meta)))
    for key in sorted(manifests):
        h.update(key.encode())
        h.update(_canon(manifests[key]))
    return h.hexdigest()


def public_meta(meta: dict) -> dict:
    """Z meta buildu tylko to, czego potrzebuje instalator (bez clientId/clientSecret)."""
    keep = ("version", "baseProductId", "platform", "buildId", "installDirectory", "products", "dependencies",
            "scriptInterpreter")
    return strip_secrets({k: meta[k] for k in keep if k in meta})


def available_languages(rec: dict) -> list:
    """Języki zakresu archiwum, które gra bazowa naprawdę ma w depotach rekordu (albo build tylko z depotami
    neutralnymi — wtedy każdy zarchiwizowany język jest kompletny)."""
    pid = str(rec.get("product_id"))
    base = [d for d in rec.get("depots") or [] if str(d.get("productId")) == pid]
    have = set()
    for d in base:
        have |= depot_langs(d)
    only_neutral = have <= {"*"}
    out = []
    for lang in (rec.get("scope") or {}).get("languages") or []:
        lg = norm_lang(lang)
        if only_neutral or lg in have:
            out.append(lg)
    return sorted(set(out))


def index_record(arc: Archive, pid: str, build: str, deep: bool = False) -> dict:
    """Wpis indeksu jednego rekordu: {"ok": True, …} albo {"ok": False, "reason"}. Sprawdza zawartość — nie
    ufa samemu polu state. `deep` — także MD5 każdego chunka (wolne; panel „Sprawdź dokładnie”)."""
    rp = arc.record_path(pid, build)
    try:
        st = rp.stat()
        rec = arc.read_json(rp)
        meta = arc.read_json(arc.record_path(pid, build, ".meta.json"))
    except (ArchiveError, OSError) as ex:
        return {"ok": False, "product_id": pid, "build_id": build, "reason": str(ex)}
    base = {"product_id": pid, "build_id": build, "title": str(rec.get("title") or pid),
            "mtime": st.st_mtime, "size_rec": st.st_size}
    if rec.get("format") != FORMAT or int(rec.get("version") or 0) > VERSION:
        return {**base, "ok": False, "reason": "rekord w nieznanym formacie"}
    if str(rec.get("product_id")) != pid or str(rec.get("build_id")) != build:
        return {**base, "ok": False, "reason": "rekord nie pasuje do swojej ścieżki"}
    if rec.get("state") != "complete":
        return {**base, "ok": False, "reason": f"archiwum niekompletne (state: {rec.get('state') or 'brak'})"}
    if int(rec.get("generation") or 2) != 2:
        return {**base, "ok": False, "reason": "obsługiwany jest tylko Galaxy content-system v2"}
    if str(rec.get("os") or "windows") != "windows":
        return {**base, "ok": False, "reason": f"build dla {rec.get('os')} — klient GOG jest tylko dla Windows"}
    try:
        manifests = {}
        for d in rec.get("depots") or []:
            manifests[f"depot:{d['manifest']}"] = arc.manifest("depot", d["manifest"])
        for d in rec.get("dependencies") or []:
            manifests[f"dep:{d['manifest']}"] = arc.manifest("dep", d["manifest"])
    except (ArchiveError, KeyError, TypeError) as ex:
        return {**base, "ok": False, "reason": f"brak manifestu: {ex}"}
    # ścieżki z manifestów sprawdzane już tutaj: build z niebezpieczną ścieżką nie jest publikowany
    try:
        for man in manifests.values():
            for it in manifest_items(man):
                safe_rel(it.get("path"))
    except UnsafePath as ex:
        return {**base, "ok": False, "reason": f"niebezpieczna ścieżka w manifeście: {ex}"}
    chunks: dict = {}
    for man in manifests.values():
        for c in manifest_chunks(man):
            m = str(c.get("compressedMd5") or "").lower()
            if not HEX32.match(m):
                return {**base, "ok": False, "reason": f"zły identyfikator chunka w manifeście: {m!r}"}
            chunks[m] = int(c.get("compressedSize") or 0)
    missing = bad = 0
    for m, size in chunks.items():
        try:
            if arc.chunk_path(m).stat().st_size != size:
                bad += 1
            elif deep:
                if hashlib.md5(arc.chunk_path(m).read_bytes()).hexdigest() != m:
                    bad += 1
        except FileNotFoundError:
            missing += 1
    if missing or bad:
        return {**base, "ok": False, "reason": f"brakujące chunki: {missing}, uszkodzone: {bad} — uzupełnij archiwum w GOG Managerze"}
    langs = available_languages(rec)
    if not langs:
        return {**base, "ok": False, "reason": "żaden zarchiwizowany język nie jest kompletny"}
    scope_dlcs = {str(x) for x in (rec.get("scope") or {}).get("dlcs") or []}
    dlcs = [{"id": str(p.get("id")), "name": str(p.get("name") or p.get("id")), "has_files": bool(p.get("has_files"))}
            for p in rec.get("products") or [] if p.get("role") == "dlc" and str(p.get("id")) in scope_dlcs]
    return {**base, "ok": True,
            "slug": str(rec.get("slug") or ""), "version": str(rec.get("version_name") or ""),
            "install_directory": str(rec.get("install_directory") or rec.get("slug") or pid),
            "languages": langs, "dlcs": dlcs,
            "dependencies": [str(d.get("id")) for d in rec.get("dependencies") or []],
            "size": int(rec.get("size") or 0), "compressed_size": int(rec.get("compressed_size") or 0),
            "hash": content_hash(rec, meta, manifests), "chunks": chunks,
            "created": str(rec.get("created") or ""), "verified": str(rec.get("verified") or "")}


# ── indeks serwera (w pamięci, odbudowywany z archiwum) ─────────────────────────

_lock = threading.Lock()
INDEX: dict = {"root": "", "builds": {}, "rejected": [], "error": "", "built": 0.0, "running": False,
               "done": 0, "total": 0}


def build_index(root: str, deep: bool = False) -> dict:
    """Pełny indeks archiwum (wywoływany w tle: przy starcie serwera i z panelu)."""
    res = {"root": root, "builds": {}, "rejected": [], "error": "", "built": time.time()}
    if not root:
        res["error"] = "nie ustawiono folderu archiwum"
        return res
    arc = Archive(Path(root))
    try:
        arc.check_format()
        recs = arc.records()
    except (ArchiveError, OSError) as ex:
        res["error"] = str(ex)
        return res
    INDEX.update(total=len(recs), done=0)
    for pid, build in recs:
        e = index_record(arc, pid, build, deep)
        INDEX["done"] += 1
        if e["ok"]:
            res["builds"][(pid, build)] = e
        else:
            res["rejected"].append({k: e.get(k) for k in ("product_id", "build_id", "title", "reason")})
            log.info("GOG: pomijam %s/%s (%s): %s", pid, build, e.get("title"), e.get("reason"))
    log.info("GOG: indeks %s — buildów: %d, pominiętych: %d", root, len(res["builds"]), len(res["rejected"]))
    return res


def refresh(root: str, deep: bool = False, wait: bool = False) -> None:
    """Odbudowa indeksu w tle (jedna naraz)."""
    with _lock:
        if INDEX["running"]:
            return
        INDEX["running"] = True

    def run():
        try:
            res = build_index(root, deep)
            with _lock:
                INDEX.update(res)
        except Exception as ex:
            log.exception("GOG: indeks")
            with _lock:
                INDEX.update(error=str(ex))
        finally:
            INDEX["running"] = False

    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True, name="gog-indeks").start()


def status() -> dict:
    """Do panelu serwera."""
    games = {pid for pid, _b in INDEX["builds"]}
    return {"root": INDEX["root"], "games": len(games), "builds": len(INDEX["builds"]),
            "rejected": list(INDEX["rejected"]), "error": INDEX["error"], "running": INDEX["running"],
            "done": INDEX["done"], "total": INDEX["total"], "built": INDEX["built"]}


def _public_build(e: dict) -> dict:
    return {k: e[k] for k in ("build_id", "version", "languages", "dlcs", "dependencies", "size",
                              "compressed_size", "hash", "install_directory", "verified")}


def catalog() -> list:
    """Gry z kompletnym archiwum: najnowszy build pierwszy (po dacie archiwizacji, potem numerze)."""
    by_pid: dict = {}
    for (pid, _b), e in INDEX["builds"].items():
        by_pid.setdefault(pid, []).append(e)
    out = []
    for pid, builds in by_pid.items():
        builds.sort(key=lambda e: (e["created"], int(e["build_id"])), reverse=True)
        out.append({"product_id": pid, "title": builds[0]["title"], "slug": builds[0]["slug"],
                    "builds": [_public_build(e) for e in builds]})
    out.sort(key=lambda g: g["title"].lower())
    return out


def game(pid: str) -> dict | None:
    return next((g for g in catalog() if g["product_id"] == str(pid)), None)


class Changed(Exception):
    """Build zmienił się / zniknął od przypięcia (instalacja nie może kontynuować)."""


def pinned(pid: str, build: str, want_hash: str = "") -> dict:
    """Wpis indeksu przypiętego buildu, sprawdzony z plikiem na dysku (rekord zmieniony od indeksu → Changed)."""
    e = INDEX["builds"].get((str(pid), str(build)))
    if not e:
        raise Changed("tego buildu nie ma w archiwum serwera (usunięty albo niekompletny)")
    if want_hash and want_hash != e["hash"]:
        raise Changed("build na serwerze zmienił się od rozpoczęcia instalacji")
    arc = Archive(Path(INDEX["root"]))
    try:
        st = arc.record_path(pid, build).stat()
    except (OSError, ArchiveError):
        raise Changed("rekord buildu zniknął z archiwum serwera")
    if st.st_mtime != e["mtime"] or st.st_size != e["size_rec"]:
        raise Changed("rekord buildu zmienił się na serwerze — odśwież indeks GOG w panelu serwera")
    return e


def build_manifest(pid: str, build: str) -> dict:
    """Wszystko, czego instalator potrzebuje do planu: rekord, meta (bez sekretów) i manifesty depotów."""
    e = pinned(pid, build)
    arc = Archive(Path(INDEX["root"]))
    rec = arc.read_json(arc.record_path(pid, build))
    meta = arc.read_json(arc.record_path(pid, build, ".meta.json"))
    mans = {"depot": {}, "dep": {}}
    flat = {}
    for d in rec.get("depots") or []:
        mans["depot"][d["manifest"]] = flat[f"depot:{d['manifest']}"] = arc.manifest("depot", d["manifest"])
    for d in rec.get("dependencies") or []:
        mans["dep"][d["manifest"]] = flat[f"dep:{d['manifest']}"] = arc.manifest("dep", d["manifest"])
    if content_hash(rec, meta, flat) != e["hash"]:
        raise Changed("zawartość buildu zmieniła się na serwerze — odśwież indeks GOG w panelu serwera")
    pub_rec = strip_secrets({k: v for k, v in rec.items() if k not in ("migration",)})
    return {"format": 1, "product_id": str(pid), "build_id": str(build), "hash": e["hash"],
            "title": e["title"], "version": e["version"], "install_directory": e["install_directory"],
            "languages": e["languages"], "dlcs": e["dlcs"], "record": pub_rec, "meta": public_meta(meta),
            "manifests": mans}


def chunk_bytes(pid: str, build: str, want_hash: str, md5: str) -> bytes:
    """Skompresowany chunk przypiętego buildu — tylko taki, do którego build się odwołuje, sprawdzony MD5."""
    e = pinned(pid, build, want_hash)
    m = str(md5 or "").lower()
    if m not in e["chunks"]:
        raise ArchiveError("ten chunk nie należy do buildu")
    data = Archive(Path(INDEX["root"])).chunk_path(m).read_bytes()
    if hashlib.md5(data).hexdigest() != m:
        raise Changed("chunk na serwerze jest uszkodzony — sprawdź archiwum w GOG Managerze")
    return data
