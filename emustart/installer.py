"""installer — emulatory i rdzenie RetroArcha na tym komputerze, wyłącznie z EmuStart Server.

Serwer jest źródłem prawdy o wersjach (emustart/emustore.py): trzyma emulatory potrzebne
systemom biblioteki, a ten moduł porównuje z nimi folder emulatorów komputera
(<emu_root>\\<Folder>\\, jak dotąd) i pobiera z serwera tylko brakujące albo zmienione pliki:

- brakujący emulator — instalacja; starszy — aktualizacja (także emulator zastany w folderze,
  np. z RetroBata: wymieniane są tylko pliki programu z manifestu serwera),
- pliki użytkownika (konfiguracja, zapisy — emucatalog.keep_file) — tylko gdy ich nie ma,
- plików spoza manifestu nic nie usuwa,
- działający emulator (jego .exe/.dll nie da się otworzyć do zapisu) — podmiana później.

Bez serwera nic się nie pobiera (EmuStart działa na tym, co jest zainstalowane).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

from emustart import emucatalog, emulators, library, netsrc, paths, systems

log = logging.getLogger("emustart.installer")

INDEX_META = "server_emu_index"
KICKSTARTS = ("kick34005.A500", "kick40068.A1200", "kick40063.A600", "kick37175.A500")


# ── RetroArch na tym komputerze ──

def _retroarch_exe(emu_root: Path) -> Path | None:
    for p in (emu_root / "RetroArch" / "retroarch.exe", emu_root / "retroarch" / "retroarch.exe"):
        if p.is_file():
            return p
    return None


def ra_system_dir(ra_root: Path) -> Path:
    """Folder „system” RetroArcha (BIOS-y, capsimg, Kickstarty) wg retroarch.cfg."""
    d = ""
    try:
        m = re.search(r'^\s*system_directory\s*=\s*"?([^"\r\n]*)"?', (ra_root / "retroarch.cfg").read_text(
            encoding="utf-8", errors="replace"), re.M)
        d = m.group(1).strip() if m else ""
    except OSError:
        pass
    if d.startswith(":"):
        d = str(ra_root) + d[1:]
    return Path(d) if d and d != "default" else ra_root / "system"


def _ra_root(root: Path) -> Path:
    ra = _retroarch_exe(root)
    return ra.parent if ra else root / "RetroArch"


def amiga_extras(emu_root: str) -> list:
    """Brakujące dodatki Amigi w RetroArchu: capsimg.dll (pliki .ipf)."""
    if (ra_system_dir(_ra_root(Path(emu_root))) / "capsimg.dll").is_file():
        return []
    return [{"kind": "capsimg", "key": "capsimg", "label": emucatalog.item_info("capsimg")["label"]}]


def amiga_kickstart_missing(emu_root: str) -> bool:
    sysdir = ra_system_dir(_ra_root(Path(emu_root)))
    return not any((sysdir / k).is_file() for k in KICKSTARTS)


# ── indeks emulatorów serwera ──

def index() -> dict:
    """Ostatnio pobrany indeks /v1/emulators ({items, systems, policy})."""
    try:
        d = json.loads(library.meta_get(INDEX_META, "{}") or "{}")
    except ValueError:
        d = {}
    return {"items": d.get("items") or {}, "systems": d.get("systems") or {}, "policy": d.get("policy")}


def policy(family: str) -> dict:
    """Wymagane ustawienia emulatora z serwera (emustart/emupolicy.py); przed pierwszym
    połączeniem z serwerem — domyślne."""
    from emustart import emupolicy
    pol = index()["policy"]
    if isinstance(pol, dict):
        return dict(pol.get(family) or {})
    return dict(emupolicy.DEFAULTS.get(family, {}))


def refresh_index(cfg: dict) -> dict:
    ep = netsrc.endpoint(cfg)
    if ep:
        try:
            st, body, _rtt = netsrc._request(ep, "/v1/emulators", 30)
            if st == 200:
                d = json.loads(body)
                library.meta_set(INDEX_META, json.dumps({"items": d.get("items") or {},
                                                         "systems": d.get("systems") or {},
                                                         "policy": d.get("policy") or {}}))
        except (OSError, ValueError) as ex:
            log.info("emulatory z serwera: %s", ex)
    return index()


def _meta(item: str) -> dict:
    return index()["items"].get(item) or {}


def target(root: Path, item: str) -> Path:
    info = emucatalog.item_info(item)
    if info["kind"] == "capsimg":
        return ra_system_dir(_ra_root(root))
    if info["kind"] == "core":
        return _ra_root(root) / "cores"
    if info["kind"] == "retroarch":
        return _ra_root(root)
    return root / info["dir"]


def present(root: Path, item: str) -> bool:
    """Pozycja jest na tym komputerze (w jakiejkolwiek wersji)."""
    kind = emucatalog.item_info(item)["kind"]
    if kind == "retroarch":
        return _retroarch_exe(root) is not None
    if kind == "core":
        return (target(root, item) / f"{item[5:]}_libretro.dll").is_file()
    if kind == "capsimg":
        return (target(root, item) / "capsimg.dll").is_file()
    d = target(root, item)
    return d.is_dir() and any(d.glob("*.exe"))


# ── stan: wersje zainstalowane z serwera ──

def _state_file() -> Path:
    return paths.DATA / "emu_state.json"


def _state() -> dict:
    try:
        d = json.loads(_state_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(item: str, version: str) -> None:
    d = _state()
    d[item] = {"version": version, "at": time.time()}
    f = _state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def current(root: Path, item: str) -> bool:
    """Ta pozycja ma tu wersję z serwera."""
    ver = _meta(item).get("version")
    return bool(ver) and _state().get(item, {}).get("version") == ver and present(root, item)


# ── plan dla systemu ──

def plan_for(es: str, emu_root: str) -> list:
    """Czego brakuje systemowi `es` (wg serwera): [{kind, key, label}]; pusta lista — nic albo
    serwer nie zna emulatora dla tego systemu."""
    root = Path(emu_root)
    out = []
    for item in index()["systems"].get(es, []):
        if emucatalog.valid_item(item) and not present(root, item):
            out.append({"kind": emucatalog.item_info(item)["kind"], "key": item,
                        "label": emucatalog.item_info(item)["label"]})
    return out


def describe(steps: list) -> list:
    """Kroki z rozmiarem i wersją z serwera."""
    out = []
    for s in steps:
        m = _meta(s["key"])
        d = dict(s, size=m.get("archive_size") or m.get("size", 0), version=m.get("version", ""))
        if not m:
            d["error"] = "serwer nie ma tego emulatora (jeszcze go nie pobrał?)"
        out.append(d)
    return out


# ── instalacja z serwera ──

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for buf in iter(lambda: f.read(1 << 20), b""):
            h.update(buf)
    return h.hexdigest()


def _locked(p: Path) -> bool:
    """Plik programu w użyciu (działający emulator) — nie da się go otworzyć do zapisu."""
    try:
        with open(p, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


class Busy(Exception):
    """Emulator działa — podmiana później."""


def _get_json(ep, path: str) -> dict:
    st, body, _rtt = netsrc._request(ep, path, 60)
    if st != 200:
        raise OSError(f"serwer: {path.split('?')[0]} {st}")
    return json.loads(body)


def _fetch_archive(ep, item: str, version: str, dest: Path, progress, cancel) -> None:
    """Archiwum wersji z serwera jednym strumieniem; przerwane pobieranie wznawia się (Range)
    od miejsca, w którym stanęło (plik .part)."""
    host, port, key = ep
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    path = "/v1/emulators/archive?" + urllib.parse.urlencode({"item": item, "version": version})
    c = netsrc.conn(host, port, 120)
    try:
        hdrs = netsrc.auth_headers(key)
        if have:
            hdrs["Range"] = f"bytes={have}-"
        c.request("GET", path, headers=hdrs)
        r = c.getresponse()
        if r.status == 416:                  # .part już pełny
            r.read()
            os.replace(part, dest)
            return
        if r.status not in (200, 206):
            raise OSError(f"serwer: archiwum emulatora {r.status}")
        if r.status == 200:
            have = 0                         # serwer wysyła od początku
        total = have + int(r.headers.get("Content-Length") or 0)
        done = have
        with open(part, "ab" if have else "wb") as f:
            while True:
                if cancel and cancel.is_set():
                    raise InterruptedError("anulowano")
                buf = r.read(1 << 20)
                if not buf:
                    break
                f.write(buf)
                done += len(buf)
                if progress:
                    progress(done, total)
        if done != total:
            raise OSError("serwer przerwał pobieranie — wznowi się przy następnej próbie")
        os.replace(part, dest)
    except http.client.HTTPException as ex:
        raise OSError(f"serwer: {ex}") from ex
    finally:
        c.close()


# jedna instalacja naraz: okno „Pobierz emulatory” i wyrównanie w tle pisały do tych samych
# plików (WinError 32, „emulator działa”, znikające pliki .emustart-new)
_install_lock = threading.Lock()


def install(cfg: dict, item: str, progress=None, cancel: threading.Event | None = None) -> bool:
    """Wyrównuje pozycję do wersji z serwera. True — coś się zmieniło. Busy — emulator działa."""
    with _install_lock:
        return _install(cfg, item, progress, cancel)


def _install(cfg: dict, item: str, progress, cancel) -> bool:
    root = Path(cfg.get("emu_root") or "")
    if not str(root) or str(root) == ".":
        raise OSError("najpierw wybierz folder emulatorów (Ustawienia)")
    ver = _meta(item).get("version")
    if not ver:
        raise OSError("serwer nie ma tego emulatora")
    if current(root, item):
        return False                         # w międzyczasie zrobiło to inne zadanie
    ep = netsrc.endpoint(cfg)
    if not ep:
        raise OSError("brak połączenia z serwerem EmuStart")
    if item == "capsimg" and not _retroarch_exe(root):
        raise OSError("najpierw potrzebny RetroArch")
    man = _get_json(ep, "/v1/emulators/manifest?" + urllib.parse.urlencode({"item": item, "version": ver}))
    tgt = target(root, item)
    todo, replace = [], []
    base = tgt.resolve()
    for rel, (size, sha) in man["files"].items():
        p = tgt / rel
        if base not in (tgt / rel).resolve().parents:   # tylko wewnątrz folderu emulatora
            raise OSError(f"podejrzana ścieżka w emulatorze z serwera: {rel}")
        if p.is_file():
            if emucatalog.keep_file(item, rel):
                continue                     # konfiguracja / zapisy użytkownika
            if p.stat().st_size == size and _sha256(p) == sha:
                continue
            replace.append(p)
        todo.append(rel)
    if any(_locked(p) for p in replace if p.suffix.lower() in (".exe", ".dll")):
        raise Busy(item)
    if todo:
        tmpdir = root / ".emustart-tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        arch = tmpdir / f"{item}-{re.sub(r'[^0-9A-Za-z._@+-]', '_', ver)}.zip"
        _fetch_archive(ep, item, ver, arch, progress, cancel)
        try:
            with zipfile.ZipFile(arch) as z:
                for rel in todo:              # tylko brakujące i zmienione pliki
                    size, sha = man["files"][rel]
                    p = tgt / rel
                    p.parent.mkdir(parents=True, exist_ok=True)
                    new = p.with_name(p.name + ".emustart-new")
                    h = hashlib.sha256()
                    with z.open(rel) as src, open(new, "wb") as out:
                        for buf in iter(lambda: src.read(1 << 20), b""):
                            h.update(buf)
                            out.write(buf)
                    if h.hexdigest() != sha:
                        new.unlink(missing_ok=True)
                        raise OSError(f"uszkodzony plik z serwera: {rel}")
                    try:
                        os.replace(new, p)
                    except PermissionError as ex:
                        new.unlink(missing_ok=True)
                        raise Busy(item) from ex
        except (zipfile.BadZipFile, KeyError) as ex:
            arch.unlink(missing_ok=True)      # uszkodzone archiwum — następnym razem od nowa
            raise OSError(f"uszkodzone archiwum emulatora: {ex}") from ex
        arch.unlink(missing_ok=True)
    portable = emucatalog.item_info(item)["portable"]
    if portable and not (tgt / portable).exists():
        # tryb przenośny: ustawienia i save'y w folderze emulatora (profile podpinają je stamtąd)
        (tgt / portable).touch()
    _save_state(item, ver)
    if todo:
        log.info("emulatory: %s %s (%d plików z archiwum serwera)", item, ver, len(todo))
    return bool(todo)


def assign(cfg: dict, es: str) -> None:
    """Po instalacji: emulator systemu = domyślna opcja (jak po „Wykryj emulatory”)."""
    emulators.forget_scan()
    cur0 = (cfg.get("systems") or {}).get(es) or {}
    if cur0.get("exe") and Path(cur0["exe"]).is_file():
        return
    opt = emulators.default_option(systems.info(es), cfg.get("emu_root", ""))
    if opt:
        cur = cfg.setdefault("systems", {}).setdefault(es, {"enabled": True})
        cur.update(label=opt["label"], exe=opt["exe"], args=opt["args"], enabled=True)


class Job:
    """Instalacja z serwera: kroki dla systemów (okno „Pobierz emulatory”) albo wyrównanie
    wszystkich (sync). UI odpytuje status()."""

    def __init__(self, cfg: dict, items: list, on_done=None, quiet_busy: bool = False):
        """items = [(es, [kroki])]; es="" — bez przypisywania emulatora."""
        self.cfg, self.items, self.on_done, self.quiet_busy = cfg, items, on_done, quiet_busy
        self.cancel = threading.Event()
        self.total_steps = sum(len(s) for _es, s in items)
        self.step = 0
        self.current = ""
        self.done_bytes = self.total_bytes = 0
        self.errors: list = []
        self.installed: list = []
        self.postponed: list = []
        self.finished = False
        self.thread = threading.Thread(target=self._run, daemon=True, name="emulatory")

    def start(self) -> None:
        self.thread.start()

    def status(self) -> dict:
        return {"running": not self.finished, "step": self.step, "steps": self.total_steps,
                "current": self.current, "done": self.done_bytes, "total": self.total_bytes,
                "errors": list(self.errors), "installed": list(self.installed),
                "postponed": list(self.postponed), "cancelled": self.cancel.is_set()}

    def _progress(self, done: int, total: int) -> None:
        self.done_bytes, self.total_bytes = done, total

    def _run(self) -> None:
        try:
            seen: set = set()
            for es, steps in self.items:
                for s in steps:
                    if self.cancel.is_set():
                        return
                    self.step += 1
                    self.current = s["label"]
                    self.done_bytes = self.total_bytes = 0
                    if s["key"] in seen:      # RetroArch, rdzeń wspólny dla kilku systemów
                        continue
                    seen.add(s["key"])
                    try:
                        if install(self.cfg, s["key"], self._progress, self.cancel):
                            self.installed.append(s["label"])
                    except Busy:
                        self.postponed.append(s["label"])
                        if not self.quiet_busy:
                            self.errors.append(f"{s['label']}: emulator działa — zamknij go i spróbuj ponownie")
                        break
                    except Exception as ex:
                        log.exception("instalacja %s", s["label"])
                        self.errors.append(f"{s['label']}: {ex}")
                        break             # bez emulatora dalsze kroki systemu nie mają sensu
                if es:
                    assign(self.cfg, es)
        finally:
            self.finished = True
            if self.on_done:
                try:
                    self.on_done(self)
                except Exception:
                    log.exception("on_done")


def sync_items(cfg: dict, es_list: list) -> list:
    """[(es, kroki)] — wszystko, co dla tych systemów jest na serwerze w innej wersji niż tu
    (brakujące i do aktualizacji)."""
    root = Path(cfg.get("emu_root") or "")
    idx = index()
    out = []
    for es in es_list:
        steps = [{"kind": emucatalog.item_info(i)["kind"], "key": i, "label": emucatalog.item_info(i)["label"]}
                 for i in idx["systems"].get(es, []) if emucatalog.valid_item(i) and i in idx["items"]
                 and not current(root, i)]
        if steps:
            out.append((es, steps))
    return out
