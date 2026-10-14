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

Stany jak w Steam (koncepcja 2.0, §5.5) — tylko dochodzą, nigdy się nie nadpisują:
- stany numerowane (`.state`, `.stateN`; RetroArch z `savestate_auto_index` nadaje kolejne
  numery) są niezmienne — gdy numer na serwerze zajmuje inna zawartość (dwa urządzenia zrobiły
  `.state13`), stan z tego urządzenia dostaje pierwszy wolny numer;
- autozapis przy wyjściu z gry trafia też do historii jako kolejny numerowany stan;
- `.state.auto` na serwerze to „Kontynuuj”: najnowszy stan, który RetroArch wczyta sam przy
  starcie. Przed grą urządzenie podkłada w jego miejsce najnowszy stan z dowolnego urządzenia;
- `.states.json` w folderze rdzenia: urządzenie, czas i rodzaj (autozapis / ręczny) każdego
  stanu — do opisów w EmuStart; na serwerze zostaje KEEP najnowszych stanów gry.

Wskaźnik „Kontynuuj” po grze trafia na serwer tylko wtedy, gdy sesja wystartowała z wersji,
która nadal jest na serwerze. Jeśli nie (inne urządzenie zapisało w międzyczasie, sesja bez
serwera), stan z tej sesji i tak jest w historii — nic nie ginie.
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

from emustart import nasfs

log = logging.getLogger("emustart.rastates")

SHARED = "states/shared/retroarch"
LEGACY_PHONE = "states/android/retroarch"        # telefon przed 0.31.1
LAYOUTS = ".layouts.json"
INDEX = ".states.json"          # {plik: {device, platform, time, kind}} w folderze rdzenia
KEEP = 50                       # stanów jednej gry (jeden rdzeń) na serwerze
CONFLICTS: list = []            # (profil, gra) z konfliktem w ostatniej sesji → komunikat przy następnej
_STATE = re.compile(r"^(?P<stem>.+?)\.state(\d*)(\.auto)?(\.png)?$", re.I)
_NUMBERED = re.compile(r"^(?P<stem>.+?)\.state(?P<n>\d*)$", re.I)
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


def _number(name: str) -> int | None:
    """Numer stanu („.state” = 0, „.state13” = 13); None — autozapis, obrazek albo inny plik."""
    m = _NUMBERED.match(name)
    return int(m.group("n") or 0) if m else None


def _index(fs, nas: str, folder: str) -> dict:
    try:
        d = json.loads(fs.read(f"{nas}/{SHARED}/{folder}/{INDEX}") or b"{}")
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(fs, nas: str, folder: str, idx: dict) -> None:
    try:
        fs.write(f"{nas}/{SHARED}/{folder}/{INDEX}", json.dumps(idx, ensure_ascii=False, indent=1).encode("utf-8"))
    except OSError as ex:
        log.info("opis stanów: %s", ex)


def _entry(kind: str) -> dict:
    return {"device": socket.gethostname(), "platform": "komputer", "time": time.time(), "kind": kind}


def describe(cfg: dict, prof: dict, adapter, core: str) -> dict:
    """{plik stanu: {device, platform, time, kind}} — opisy stanów tego rdzenia z serwera."""
    if not prof or not core:
        return {}
    fs = nasfs.backend(cfg)
    try:
        files = fs.scan(f"{prof['nas_name']}/{SHARED}")
    except OSError:
        return {}
    return _index(fs, prof["nas_name"], _shared_folder(files, core_name(adapter.home, core)))


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
    # „Kontynuuj”: najnowszy stan (z dowolnego urządzenia) w miejscu autozapisu — RetroArch
    # wczyta go sam przy starcie; numerowany zostaje na swoim miejscu w historii
    nums = [(mtime, name) for _rel, name, mtime in cands
            if _number(name) is not None and name not in info["stale"]]
    if nums:
        t, newest = max(nums)
        src = dst_dir / newest
        auto = dst_dir / (_NUMBERED.match(newest).group("stem") + ".state.auto")
        try:
            if src.is_file() and (not auto.is_file() or auto.stat().st_mtime < t - 2):
                import shutil
                shutil.copyfile(src, auto)
                os.utime(auto, (t, t))
                if src.with_name(src.name + ".png").is_file():
                    shutil.copyfile(src.with_name(src.name + ".png"), auto.with_name(auto.name + ".png"))
        except OSError as ex:
            log.warning("najnowszy stan jako autozapis: %s", ex)
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
    return ("Ostatnio stan z tego komputera nie zastąpił nowszego z innego urządzenia jako "
            "„Kontynuuj” — jest w historii stanów (Wczytaj zapis).") if hit else ""


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
    """Po grze: nowe stany tej gry → serwer jako nowe, niezmienne wpisy historii. Zwraca ich
    liczbę. `info` z `present` (None = sesja bez stanów z serwera)."""
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
    idx = _index(fs, nas, folder)
    mine = [f for f in sorted(src_dir.glob("*")) if src_dir.is_dir()]
    mine = [f for f in mine if f.is_file() and _STATE.match(f.name) and not f.name.lower().endswith(".png")
            and norm(_STATE.match(f.name).group("stem")) == stem]
    taken = {_number(rel.rsplit("/", 1)[-1]) for rel in files if rel.startswith(folder + "/")
             and _STATE.match(rel.rsplit("/", 1)[-1])
             and norm(_STATE.match(rel.rsplit("/", 1)[-1]).group("stem")) == stem} | {_number(f.name) for f in mine}
    taken.discard(None)

    def next_name(orig_stem: str) -> str:
        n = max(taken | {0}) + 1
        taken.add(n)
        return f"{orig_stem}.state{n}"

    def upload(f: Path, name: str, kind: str, overwrite: str = "") -> bool:
        try:
            fs.put_file(f, f"{nas}/{SHARED}/{folder}/{name}", backup=overwrite)
            png = f.with_name(f.name + ".png")
            if png.is_file():
                fs.put_file(png, f"{nas}/{SHARED}/{folder}/{name}.png")
            if kind:
                idx[name] = _entry(kind)
            return True
        except OSError as ex:
            log.warning("stan %s na serwer: %s", name, ex)
            return False

    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{socket.gethostname()}"
    n = 0
    for f in mine:
        st = f.stat()
        orig_stem = _STATE.match(f.name).group("stem")
        have = files.get(f"{folder}/{f.name}")
        if have and have[0] == st.st_size and abs(have[1] - st.st_mtime) <= 2:
            continue                               # na serwerze już ten
        if st.st_mtime < since - 2 and have and have[1] >= st.st_mtime:
            continue                               # nie z tej sesji, a na serwerze nowszy
        if _is_auto(f.name):
            if st.st_mtime >= since - 2:
                # autozapis z tej sesji → także nowy stan w historii (tutaj i na serwerze)
                hist = f.with_name(next_name(orig_stem))
                shutil.copyfile(f, hist)
                os.utime(hist, (st.st_atime, st.st_mtime))
                if f.with_name(f.name + ".png").is_file():
                    shutil.copyfile(f.with_name(f.name + ".png"), hist.with_name(hist.name + ".png"))
                n += upload(hist, hist.name, "auto")
            if have and _conflict(info, f.name, have):
                log.warning("stan %s: na serwerze inny „Kontynuuj” niż na starcie gry — ten z tej sesji "
                            "tylko w historii", f.name)
                if (nas, stem) not in CONFLICTS:
                    CONFLICTS.append((nas, stem))
                continue
            upload(f, f.name, "", f"{nas}/_backup/{stamp}/{SHARED}/{folder}/{f.name}" if have else "")
            continue
        name = f.name
        if have:
            # numer zajęty na serwerze inną zawartością — stany się nie nadpisują: następny wolny
            name = next_name(orig_stem)
            new = f.with_name(name)
            f.rename(new)
            if f.with_name(f.name + ".png").is_file():
                f.with_name(f.name + ".png").rename(new.with_name(name + ".png"))
            f = new
            log.info("stan %s: numer zajęty na serwerze — zapisany jako %s", have and f.name, name)
        n += upload(f, name, "manual")
    if n:
        _prune(fs, nas, folder, stem, idx)
        _save_index(fs, nas, folder, idx)
        log.info("stany na serwer (%s): %d nowych w historii", cname, n)
    return n


def _prune(fs, nas: str, folder: str, stem: str, idx: dict) -> None:
    """Na serwerze zostaje KEEP najnowszych stanów gry (starsze z obrazkami i opisami znikają)."""
    try:
        files = fs.scan(f"{nas}/{SHARED}/{folder}")
    except OSError:
        return
    nums = [(idx.get(name, {}).get("time") or mtime, name) for name, (_size, mtime) in files.items()
            if "/" not in name and _number(name) is not None and norm(_NUMBERED.match(name).group("stem")) == stem]
    for _t, name in sorted(nums, reverse=True)[KEEP:]:
        for rel in (name, name + ".png"):
            try:
                fs.unlink(f"{nas}/{SHARED}/{folder}/{rel}")
            except OSError:
                pass
        idx.pop(name, None)


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
