"""rastates — stany RetroArcha wspólne dla wszystkich urządzeń, niezależnie od ich ustawień.

RetroArch zapisuje stan w miejscu zależnym od ustawień urządzenia:
    <savestate_directory>/[<folder gry>/][<rdzeń>/]<plik gry>.state[N|.auto]
(`sort_savestates_by_content_enable` — folder gry, `sort_savestates_enable` — rdzeń, nazwa
z `corename` w info/*.info, np. „bsnes”). Każdy komputer i telefon może mieć inaczej.

Na serwerze stany leżą w jednym, stałym układzie:
    <profil>/states/shared/retroarch/<rdzeń>/<plik gry>.state…
a każde urządzenie zgłasza swój układ w `.layouts.json` obok. Przed grą urządzenie bierze
z serwera nowsze stany tej gry i tego rdzenia i kładzie je tam, gdzie jego RetroArch ich
szuka; po grze zmienione wysyła z powrotem. Stany różnych rdzeni się nie wczytują —
dlatego wszystko jest w folderach rdzeni.

Autozapis (`.state.auto`) jest wspólny: gdy jest, RetroArch wczytuje go przy starcie gry
(`present` → `info["autoload"]`). Po grze stan trafia na serwer tylko wtedy, gdy sesja
wystartowała z wersji, która nadal jest na serwerze. Jeśli nie (wersja z serwera się nie
pobrała, sesja ruszyła bez serwera albo inne urządzenie zapisało w międzyczasie), stan
z tej sesji idzie do kopii jako konflikt i nie nadpisuje wspólnego.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import time
from pathlib import Path

from emustart import nasfs

log = logging.getLogger("emustart.rastates")

SHARED = "states/shared/retroarch"
LEGACY_PHONE = "states/android/retroarch"        # telefon przed 0.31.1
LAYOUTS = ".layouts.json"
CONFLICTS: list = []            # (profil, gra) z konfliktem w ostatniej sesji → komunikat przy następnej
_STATE = re.compile(r"^(?P<stem>.+?)\.state(\d*)(\.auto)?(\.png)?$", re.I)
_CORE_ARG = re.compile(r'-L\s+"?([^"]*?)([\w.+-]+?)_libretro\.(?:dll|so|dylib)"?', re.I)


def norm(s: str) -> str:
    return re.sub(r"[\s_]+", " ", s).strip().lower()


def norm_core(s: str) -> str:
    """„Beetle PSX HW” = „beetle_psx_hw” = „BeetlePSXHW” — porównanie folderów rdzeni."""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def core_id(args: str) -> str:
    """Rdzeń z parametrów RetroArcha (-L …\\cores\\bsnes_libretro.dll → bsnes)."""
    m = _CORE_ARG.search(args or "")
    return m.group(2) if m else ""


def core_name(ra_home: Path, core: str) -> str:
    """Nazwa folderu rdzenia, której RetroArch używa przy sortowaniu (corename z .info)."""
    f = ra_home / "info" / f"{core}_libretro.info"
    try:
        m = re.search(r'^corename\s*=\s*"([^"]*)"', f.read_text(encoding="utf-8", errors="replace"), re.M)
        if m and m.group(1):
            return m.group(1)
    except OSError:
        pass
    return core


def layout(adapter) -> dict:
    """Układ stanów tego RetroArcha (z retroarch.cfg)."""
    from emustart.ingame import _ini_value
    cfg = adapter.home / "retroarch.cfg"
    return {"base": str(adapter._cfg_dir("savestate_directory", "states")),
            "content": _ini_value(cfg, "sort_savestates_by_content_enable") == "true",
            "core": _ini_value(cfg, "sort_savestates_enable") == "true"}


def state_dir(adapter, core: str, content: Path) -> Path:
    """Folder, w którym ten RetroArch szuka stanów tej gry (wg jego ustawień)."""
    lay = layout(adapter)
    d = Path(lay["base"])
    if lay["content"]:
        d = d / content.parent.name
    if lay["core"] and core:
        d = d / core_name(adapter.home, core)
    return d


def _shared_folder(files: dict, cname: str) -> str:
    """Folder rdzenia na serwerze (istniejący o tej samej nazwie po normalizacji)."""
    want = norm_core(cname)
    for rel in files:
        if "/" in rel and norm_core(rel.split("/", 1)[0]) == want:
            return rel.split("/", 1)[0]
    return cname


def _register(fs, nas: str, adapter) -> None:
    """Układ tego urządzenia w .layouts.json (serwer i inne urządzenia go widzą)."""
    rel = f"{nas}/{SHARED}/{LAYOUTS}"
    try:
        data = json.loads(fs.read(rel) or b"{}")
    except (OSError, ValueError):
        data = {}
    me = socket.gethostname()
    lay = {**layout(adapter), "device": "komputer"}
    old = dict(data.get(me) or {})
    old.pop("time", None)
    if old != lay:
        data[me] = {**lay, "time": time.time()}
        try:
            fs.write(rel, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))
        except OSError as ex:
            log.info("układ stanów: %s", ex)


def _candidates(fs, nas: str, cname: str, stem: str, base_out: dict | None = None) -> list:
    """[(ścieżka na serwerze, nazwa pliku, czas)] stanów tej gry i tego rdzenia — wspólne
    i (przejściowo) ze starszych wersji aplikacji na telefon. `base_out` dostaje wersje
    ze wspólnego miejsca: {nazwa: (rozmiar, czas)}."""
    out = {}
    for base in (SHARED, LEGACY_PHONE):
        try:
            files = fs.scan(f"{nas}/{base}")
        except OSError:
            continue
        for rel, (size, mtime) in files.items():
            if "/" not in rel or norm_core(rel.split("/", 1)[0]) != norm_core(cname):
                continue
            name = rel.rsplit("/", 1)[-1]
            m = _STATE.match(name)
            if not m or norm(m.group("stem")) != stem:
                continue
            if base == SHARED and base_out is not None:
                base_out[name] = (size, mtime)
            if name not in out or mtime > out[name][2]:
                out[name] = (f"{base}/{rel}", name, mtime)
    return list(out.values())


def _is_auto(name: str) -> bool:
    m = _STATE.match(name)
    return bool(m and m.group(3))


def present(cfg: dict, prof: dict, adapter, core: str, content: Path, info: dict | None = None) -> str:
    """Przed grą: nowsze stany tej gry (ten rdzeń) z serwera → folder, w którym ten
    RetroArch ich szuka. Zwraca komunikat dla gracza ('' = nic do zgłoszenia).

    `info` (wypełniane dla `publish`): "base" — stany we wspólnym miejscu na starcie
    {nazwa: (rozmiar, czas)}, "stale" — nazwy, których nie udało się pobrać,
    "autoload" — jest aktualny autozapis do wczytania przy starcie."""
    info = {} if info is None else info
    if not prof or not core:
        return ""
    fs = nasfs.backend(cfg)
    nas = prof["nas_name"]
    _register(fs, nas, adapter)
    stem = norm(content.stem)
    cname = core_name(adapter.home, core)
    dst_dir = state_dir(adapter, core, content)
    base: dict = {}
    cands = _candidates(fs, nas, cname, stem, base)
    info["base"], info["stale"] = base, set()
    n = 0
    for rel, name, mtime in cands:
        dst = dst_dir / name
        try:
            if dst.is_file() and dst.stat().st_mtime >= mtime - 2:
                continue                           # tutaj ten sam albo nowszy
            tmp = dst.with_name(dst.name + ".z-serwera")
            if not fs.get_file(f"{nas}/{rel}", tmp):
                info["stale"].add(name)
                continue
            if dst.is_file() and dst.read_bytes() == tmp.read_bytes():
                tmp.unlink(missing_ok=True)
                continue
            if dst.is_file():
                from emustart import profiles
                nasfs.backup_copy(dst, profiles.LOCAL / str(prof["id"]) / "_backup"
                                  / time.strftime("%Y%m%d-%H%M%S") / "stany-wspolne" / name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp.replace(dst)
            if not name.lower().endswith(".png"):
                n += 1
        except OSError as ex:
            info["stale"].add(name)
            log.warning("stan %s: %s", name, ex)
    # autozapis tej gry na miejscu i aktualny → RetroArch wczyta go przy starcie
    autos = [f.name for f in dst_dir.glob("*.state.auto")
             if norm(_STATE.match(f.name).group("stem")) == stem] if dst_dir.is_dir() else []
    info["autoload"] = bool(autos) and not any(a in info["stale"] for a in autos)
    pending = _take_conflict(nas, stem)
    if info["stale"]:
        return ("Nie udało się pobrać stanu tej gry z serwera — wersja z serwera zostaje, "
                "stan z tej sesji trafi do kopii.")
    if n:
        log.info("stany wspólne (%s): %d → %s", cname, n, dst_dir)
        what = "Autozapis" if info["autoload"] and any(_is_auto(c[1]) for c in cands) else "Stan gry"
        return pending or f"{what} z innego urządzenia gotowy do wczytania ({cname})."
    if pending:
        return pending
    other = _other_cores(fs, nas, cname, stem)
    if other and not cands:
        return (f"Stan tej gry z innego urządzenia jest z rdzenia {', '.join(sorted(other))}, a tu gra "
                f"rdzeń {cname} — stany różnych rdzeni się nie wczytują.")
    return ""


def _take_conflict(nas: str, stem: str) -> str:
    """Komunikat o konflikcie z poprzedniej sesji tej gry (jednorazowo)."""
    hit = [c for c in CONFLICTS if c == (nas, stem)]
    for c in hit:
        CONFLICTS.remove(c)
    return ("Ostatnio stan z tego komputera nie nadpisał nowszego z innego urządzenia — "
            "leży w kopii zapasowej (konflikt).") if hit else ""


def _other_cores(fs, nas: str, cname: str, stem: str) -> set:
    out = set()
    try:
        files = fs.scan(f"{nas}/{SHARED}")
    except OSError:
        return out
    for rel in files:
        if "/" not in rel:
            continue
        folder, name = rel.split("/", 1)[0], rel.rsplit("/", 1)[-1]
        m = _STATE.match(name)
        if m and not m.group(4) and norm(m.group("stem")) == stem and norm_core(folder) != norm_core(cname):
            out.add(folder)
    return out


def publish(cfg: dict, prof: dict, adapter, core: str, content: Path, since: float,
            info: dict | None = None) -> int:
    """Po grze: stany tej gry zmienione w tej sesji → wspólne miejsce na serwerze.

    `info` z `present` (None = sesja bez stanów z serwera). Stan z tej sesji nie nadpisuje
    wersji z serwera innej niż ta ze startu gry — idzie do kopii jako konflikt."""
    if not prof or not core:
        return 0
    fs = nasfs.backend(cfg)
    nas = prof["nas_name"]
    stem = norm(content.stem)
    cname = core_name(adapter.home, core)
    src_dir = state_dir(adapter, core, content)
    try:
        files = fs.scan(f"{nas}/{SHARED}")
    except OSError:
        files = {}
    folder = _shared_folder(files, cname)
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{socket.gethostname()}"
    n = 0
    for f in sorted(src_dir.glob("*")) if src_dir.is_dir() else []:
        m = _STATE.match(f.name)
        if not f.is_file() or not m or norm(m.group("stem")) != stem:
            continue
        rel = f"{folder}/{f.name}"
        st = f.stat()
        have = files.get(rel)
        if have and have[0] == st.st_size and abs(have[1] - st.st_mtime) <= 2:
            continue                               # na serwerze już ten
        if st.st_mtime < since - 2 and have and have[1] >= st.st_mtime:
            continue                               # nie z tej sesji, a na serwerze nowszy
        if have and _conflict(info, f.name, have):
            try:
                fs.put_file(f, f"{nas}/_backup/{stamp}/konflikt/{SHARED}/{rel}")
                log.warning("stan %s: na serwerze inna wersja niż na starcie gry — z tej sesji "
                            "do kopii (konflikt), wspólny bez zmian", f.name)
                if (nas, stem) not in CONFLICTS:
                    CONFLICTS.append((nas, stem))
            except OSError as ex:
                log.warning("stan %s do kopii: %s", f.name, ex)
            continue
        try:
            fs.put_file(f, f"{nas}/{SHARED}/{rel}",
                        backup=f"{nas}/_backup/{stamp}/{SHARED}/{rel}" if have else "")
            if not f.name.lower().endswith(".png"):
                n += 1
        except OSError as ex:
            log.warning("stan %s na serwer: %s", f.name, ex)
    if n:
        log.info("stany na serwer (%s): %d", cname, n)
    return n


def _conflict(info: dict | None, name: str, have: tuple) -> bool:
    """Wersja na serwerze (`have`) nie jest tą, od której sesja wystartowała."""
    if info is None or "base" not in info:
        # start bez serwera: autozapisu z serwera nie było jak wczytać — nie nadpisujemy go
        return _is_auto(name[:-4] if name.lower().endswith(".png") else name)
    main = name[:-4] if name.lower().endswith(".png") else name
    if main in info.get("stale", ()):
        return True
    b = info["base"].get(name)
    if b is None:
        return True                                # pojawił się w trakcie gry (inne urządzenie)
    return not (b[0] == have[0] and abs(b[1] - have[1]) <= 2)
