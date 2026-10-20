"""emuchoice — wybór emulatora i rdzenia na koncie gracza (EmuStart na PC).

Komputery jednego konta mają te same wybory: emulator systemu (np. SNES → RetroArch z bsnes)
i emulator wybrany dla pojedynczej gry. Bez tego nowy komputer brał domyślne, a inny rdzeń
to inne paczki zapisów (Snes9x ≠ bsnes) — zapisy z drugiego komputera „znikały”.

Plik w profilu na serwerze: settings/windows/emulators.json

    {"systems": {"snes": {"key": "retroarch#bsnes", "label": "RetroArch: bsnes", "t": …}},
     "games":   {"<game_id>": {"key": "standalone:duckstation", "label": …, "t": …},
                 "<game_id>": {"clear": true, "t": …}}}

Klucz nie zależy od ścieżek komputera: „retroarch#<rdzeń>” albo „standalone:<emulator>”.
Telefony mają własny plik (settings/android/emulators.json) — inne emulatory.
Przy wejściu na profil wybory konta wygrywają z ustawieniami komputera; brakujący na tym
komputerze rdzeń / emulator EmuStart pobiera z serwera i ustawia po pobraniu.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from emustart import emulators, library, profiles, savebundles, systems

log = logging.getLogger("emustart.emuchoice")

NAME = "settings/windows/emulators.json"
_STANDALONE = sorted({p for lst in emulators.STANDALONE_PREF.values() for p in lst}, key=len, reverse=True)
_lock = threading.Lock()


def key_of(opt: dict) -> str:
    """Klucz wyboru niezależny od komputera ('' — nie da się go opisać)."""
    if not opt or not opt.get("exe"):
        return ""
    stem = Path(opt["exe"]).stem.lower()
    if stem == "retroarch":
        core = savebundles.core_id(opt.get("args", ""))
        return f"retroarch#{core}" if core else ""
    hit = next((p for p in _STANDALONE if p in stem), stem)
    return f"standalone:{hit}"


def find(cfg: dict, es: str, key: str) -> dict:
    """Opcja tego komputera dla klucza (pusta — brak tego emulatora / rdzenia)."""
    if not key:
        return {}
    opts = emulators.options_for(systems.info(es), cfg.get("emu_root", ""))
    return next((o for o in opts if key_of(o) == key), {})


def install_item(key: str) -> str:
    """Pozycja katalogu emulatorów serwera dla klucza („core:bsnes”, „pcsx2”)."""
    if key.startswith("retroarch#"):
        return "core:" + key.split("#", 1)[1]
    if key.startswith("standalone:"):
        return key.split(":", 1)[1]
    return ""


def _load(cfg: dict, pid: int) -> dict:
    d = profiles.json_get(cfg, pid, NAME) or {}
    d.setdefault("systems", {})
    d.setdefault("games", {})
    return d


def record_system(cfg: dict, pid: int, es: str, opt: dict) -> None:
    """Gracz wybrał emulator systemu na tym komputerze → konto."""
    k = key_of(opt)
    if not k:
        return
    with _lock:
        d = _load(cfg, pid)
        d["systems"][es] = {"key": k, "label": opt.get("label", ""), "t": time.time()}
        profiles.json_set(cfg, pid, NAME, d)
    log.info("wybór emulatora %s → konto: %s", es, k)


def record_game(cfg: dict, pid: int, g: dict, opt: dict | None) -> None:
    from emustart import savesync
    gid = savesync.game_id(g["es"], g["name"])
    with _lock:
        d = _load(cfg, pid)
        k = key_of(opt or {})
        d["games"][gid] = ({"key": k, "label": (opt or {}).get("label", ""), "t": time.time()} if k
                           else {"clear": True, "t": time.time()})
        profiles.json_set(cfg, pid, NAME, d)


def apply(cfg: dict, pid: int) -> tuple[int, list]:
    """Wybory konta → ustawienia tego komputera. (zmienione, [(es, pozycja do pobrania)])."""
    from emustart import savesync
    d = _load(cfg, pid)
    changed, missing = 0, []
    sysc = cfg.setdefault("systems", {})
    for es, v in d["systems"].items():
        opt = find(cfg, es, v.get("key", ""))
        if not opt:
            item = install_item(v.get("key", ""))
            if item:
                missing.append((es, item))
            continue
        cur = sysc.setdefault(es, {"enabled": True})
        if (cur.get("exe"), cur.get("args", "")) != (opt["exe"], opt.get("args", "")):
            cur.update(label=opt["label"], exe=opt["exe"], args=opt.get("args", ""), enabled=True)
            changed += 1
    if d["games"]:
        ids = {}
        for r in library.db().execute("SELECT id, es, name FROM games"):
            ids[savesync.game_id(r["es"], r["name"])] = (r["id"], r["es"])
        with library.db() as c:
            for gid, v in d["games"].items():
                hit = ids.get(gid)
                if not hit:
                    continue
                local_id, es = hit
                if v.get("clear"):
                    changed += c.execute("DELETE FROM game_emu WHERE game_id=?", (local_id,)).rowcount
                    continue
                opt = find(cfg, es, v.get("key", ""))
                if not opt:
                    item = install_item(v.get("key", ""))
                    if item:
                        missing.append((es, item))
                    continue
                row = c.execute("SELECT exe, args FROM game_emu WHERE game_id=?", (local_id,)).fetchone()
                if not row or (row["exe"], row["args"]) != (opt["exe"], opt.get("args", "")):
                    c.execute("INSERT OR REPLACE INTO game_emu VALUES(?,?,?,?)",
                              (local_id, opt["label"], opt["exe"], opt.get("args", "")))
                    changed += 1
    if changed:
        log.info("wybory emulatorów z konta: %d zmian na tym komputerze", changed)
    return changed, missing


def _default_key(cfg: dict, es: str) -> str:
    from emustart.api import _recommended_option
    return key_of(_recommended_option(es, cfg) or emulators.default_option(systems.info(es), cfg.get("emu_root", "")))


def seed(cfg: dict, pid: int) -> int:
    """Wybory z tego komputera, których konto jeszcze nie ma (wybrane przed 0.46.1, gdy
    trzymał je tylko config.json) → konto. Tylko inne niż domyślny emulator — domyślny
    nowego komputera nie może nadpisać wyboru zrobionego na starym."""
    from emustart import savesync
    with _lock:
        d = _load(cfg, pid)
        n = 0
        for es, sc in (cfg.get("systems") or {}).items():
            k = key_of(sc)
            if k and es not in d["systems"] and sc.get("exe") and Path(sc["exe"]).is_file() and k != _default_key(cfg, es):
                d["systems"][es] = {"key": k, "label": sc.get("label", ""), "t": time.time()}
                n += 1
        for r in library.db().execute("SELECT e.label, e.exe, e.args, g.es, g.name FROM game_emu e "
                                      "JOIN games g ON g.id = e.game_id"):
            gid = savesync.game_id(r["es"], r["name"])
            k = key_of(dict(r))
            if k and gid not in d["games"]:
                d["games"][gid] = {"key": k, "label": r["label"], "t": time.time()}
                n += 1
        if n:
            profiles.json_set(cfg, pid, NAME, d)
            log.info("wybory emulatorów z tego komputera → konto: %d", n)
        return n


def apply_and_fetch(cfg: dict, pid: int, on_change=None) -> None:
    """apply + pobranie brakujących rdzeni / emulatorów z serwera i drugie apply (w tle)."""
    from emustart import config, installer
    try:
        seed(cfg, pid)
        changed, missing = apply(cfg, pid)
        if changed:
            config.save(cfg)
            if on_change:
                on_change()
        items = sorted({item for _es, item in missing if item in installer.index().get("items", {})})
        if not items:
            return
        for item in items:
            try:
                log.info("wybór z konta: pobieram %s z serwera", item)
                installer.install(cfg, item)
            except Exception:
                log.exception("pobieranie %s", item)
        emulators.forget_scan()
        changed, _ = apply(cfg, pid)
        if changed:
            config.save(cfg)
            if on_change:
                on_change()
    except Exception:
        log.exception("wybory emulatorów z konta")
