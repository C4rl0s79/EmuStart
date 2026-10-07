"""config — `config.json` obok programu.

Ustawienia per system (emulator, argumenty, rozpakowywanie) trzymamy pod kluczem
nazwy folderu ES, bo to on jest tożsamością systemu w kolekcji użytkownika.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from emustart import paths

DEFAULTS: dict = {
    "rom_root": r"Z:\ROMS\ROMS",
    "emu_root": r"D:\emu\emulatory",
    "cache_dir": "",                # "" → <program>\cache
    "cache_recent": 10,             # ile ostatnio uruchomionych gier trzymać
    "network_mode": "auto",         # auto | lan | remote
    "lan_threshold_mbps": 200,      # powyżej: LAN (gra płytowa wprost z NAS)
    "small_game_mb": 64,            # mniejsze gry zawsze najpierw do cache
    "fullscreen": True,
    "hide_arcade_clones": True,
    "video_previews": False,
    "mame_exe": "",                 # "" → wykryj w emu_root
    "art_keys": {},                 # sgdb_key, igdb_client_id/secret, tgdb_key (z PyLinks)
    "systems": {},                  # es_name → {enabled, exe, args, label, extract}
}

_lock = threading.Lock()


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        data = json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update(data)
    except FileNotFoundError:
        pass
    except Exception:
        # uszkodzony plik: nie nadpisujemy go po cichu, odkładamy kopię
        try:
            bak = paths.CONFIG_PATH.with_suffix(".json.broken")
            paths.CONFIG_PATH.replace(bak)
        except OSError:
            pass
    return cfg


def save(cfg: dict) -> None:
    with _lock:
        tmp = paths.CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(paths.CONFIG_PATH)


def cache_dir(cfg: dict) -> Path:
    return Path(cfg.get("cache_dir") or paths.DEFAULT_CACHE)


def is_configured(cfg: dict) -> bool:
    return paths.CONFIG_PATH.exists() and bool(cfg.get("systems"))
