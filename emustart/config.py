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
    # nowa instalacja niczego nie zakłada: bez folderów z grami, emulatorów i folderu profili
    # na NAS (wcześniej domyślne były ścieżki z pierwszej instalacji — Z:\…, D:\emu\…)
    "rom_root": "",                 # stary klucz (jeden folder) — patrz rom_roots()
    "rom_roots": [],                # foldery z grami, kolejność = pierwszeństwo przy duplikatach
    "emu_root": "",
    "profiles_nas": "",             # serwer: folder profili graczy ("" → <serwer>\profiles)
    "cache_dir": "",                # "" → <program>\cache
    "cache_recent": 10,             # ile ostatnio uruchomionych gier trzymać
    "small_game_mb": 64,            # mniejsze gry zawsze najpierw do cache
    "fullscreen": True,
    "hide_arcade_clones": True,
    "video_previews": False,
    "mame_exe": "",                 # "" → wykryj w emu_root
    "bios_dir": "",                 # BIOS-y (serwer: dla urządzeń; PC: kopiowane do RetroArcha)
    "backup_dir": "",               # serwer: kopie kont ("" → <serwer>\backup)
    "backup_auto": True,            # serwer: kopia konta raz dziennie, gdy się zmieniło
    "backup_keep": 14,              # serwer: tyle najnowszych kopii każdego konta
    "art_keys": {},                 # sgdb_key, igdb_client_id/secret, tgdb_key (z PyLinks)
    "systems": {},                  # es_name → {enabled, exe, args, label, extract}
    "system_merge": {},             # serwer: platforma → platforma, z którą jest scalona
}

_lock = threading.Lock()


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        data = json.loads(paths.CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update(_legacy_defaults(data))
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


LEGACY = {"rom_root": r"Z:\ROMS\ROMS", "emu_root": r"D:\emu\emulatory", "profiles_nas": r"Z:\emustart\Profiles"}


def _legacy_defaults(data: dict) -> dict:
    """Ustawienia sprzed 0.37.4 polegały na domyślnych ścieżkach pierwszej instalacji —
    istniejący plik ich nie miał, a zachowanie ma zostać takie samo. Nowa instalacja
    (bez pliku) dostaje puste wartości."""
    out = dict(data)
    for k, v in LEGACY.items():
        if k not in out:
            out[k] = v
    return out


def rom_roots(cfg: dict) -> list:
    """Foldery z grami. Starsze configi mają jeden `rom_root`."""
    roots = [r for r in (cfg.get("rom_roots") or []) if str(r).strip()]
    if not roots and cfg.get("rom_root"):
        roots = [cfg["rom_root"]]
    return [str(r) for r in roots]


def cache_dir(cfg: dict) -> Path:
    return Path(cfg.get("cache_dir") or paths.DEFAULT_CACHE)


def is_configured(cfg: dict) -> bool:
    if not paths.CONFIG_PATH.exists():
        return False
    # komputer połączony z serwerem bez folderów z grami bierze bibliotekę z serwera (libsync)
    return bool(cfg.get("systems")) or bool(cfg.get("server_url") and cfg.get("server_key") and not rom_roots(cfg))
