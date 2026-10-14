"""srvconfig — foldery EmuStart Server ustawiane w panelu (emustart/srvadmin.py).

Serwer zwykle działa jako zadanie Harmonogramu na koncie SYSTEM, a panel w sesji
użytkownika. Konto SYSTEM nie widzi dysków sieciowych podłączonych przez użytkownika
(np. `Z:`), dlatego każdy folder jest sprawdzany przez serwer — tak, jak on go widzi —
a panel zamienia wybrany dysk sieciowy na ścieżkę UNC (`\\\\NAS\\udział\\…`).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path

from emustart import config, paths

log = logging.getLogger("emustart.srvconfig")

# klucz w config.json → (opis, rodzaj: dirs | dir | file, pusty dozwolony, podpowiedź)
FIELDS = {
    "rom_roots": ("Foldery z grami", "dirs", False,
                  "Podfoldery systemów (np. psx, snes, „Sony - PlayStation”). Kolejność = pierwszeństwo przy tych samych grach."),
    "bios_dir": ("Folder BIOS-ów", "dir", True,
                 "BIOS-y i firmware dla emulatorów (np. scph5501.bin, neogeo.zip, kick40068.A1200) — "
                 "urządzenia dostają je przed uruchomieniem gry."),
    "emu_root": ("Folder emulatorów", "dir", True,
                 "RetroArch i inne emulatory na serwerze — listy BIOS-ów rdzeni (pliki .info) i BIOS-y z folderu system RetroArcha."),
    "profiles_nas": ("Folder profili graczy", "dir", True,
                     "Zapisy, stany gry i ustawienia emulatorów kont. Pusty — folder profiles w katalogu serwera."),
    "emu_store_dir": ("Magazyn emulatorów", "dir", True,
                      "Emulatory, które serwer pobiera i rozdaje komputerom (zakładka Emulatory). "
                      "Pusty — folder emulators w katalogu serwera."),
    "logo_pack_dir": ("Paczka logo platform (opcjonalnie)", "dir", True,
                      "Własne logo systemów do wyboru (np. platform_logos z PyLinks) — dodatkowo do motywów "
                      "z internetu. Serwer wczytuje paczkę do jednego pliku (media/_logopack.media); "
                      "folder może potem zniknąć."),
    "backup_dir": ("Folder kopii kont", "dir", True,
                   "Kopie zapasowe kont (ZIP). Pusty — folder backup w katalogu serwera."),
    "mame_exe": ("MAME (opcjonalnie)", "file", True,
                 "mame.exe — pełne dane gier arcade (gatunek, liczba graczy). Bez niego nazwy i wersje z pliku DAT FinalBurn Neo."),
}

_UNC_HINT = ("Serwer działa na koncie systemowym i nie widzi dysków sieciowych podłączonych literą "
             "(np. Z:). Podaj ścieżkę sieciową, np. \\\\NAS\\udział\\folder.")


def default_dir(key: str) -> Path | None:
    return {"profiles_nas": paths.SERVER / "profiles", "backup_dir": paths.SERVER / "backup",
            "emu_store_dir": paths.SERVER / "emulators"}.get(key)


def _value(cfg: dict, key: str):
    if key == "rom_roots":
        return config.rom_roots(cfg)
    return str(cfg.get(key) or "")


def _status(key: str, value: str) -> dict:
    """Co serwer widzi pod ścieżką: {ok, text}."""
    kind = FIELDS[key][1]
    if not value:
        if key == "logo_pack_dir":
            from emustart import mediastore
            n = len(mediastore.files("_logopack"))
            return {"ok": True, "text": f"logo w magazynie: {n}" if n else "nie ustawiono"}
        d = default_dir(key)
        return {"ok": True, "text": f"domyślnie: {d}" if d else "nie ustawiono"}
    p = Path(value)
    try:
        if kind == "file":
            return {"ok": p.is_file(), "text": "jest" if p.is_file() else "serwer nie widzi pliku"}
        if not p.is_dir():
            return {"ok": False, "text": "serwer nie widzi folderu" + (" — " + _UNC_HINT if _mapped(value) else "")}
        if key == "rom_roots":
            from emustart import systems
            subs = [d for d in p.iterdir() if d.is_dir()]
            known = sum(1 for d in subs if systems.match_folder(d.name))
            return {"ok": True, "text": f"systemów: {known} (folderów: {len(subs)})"}
        if key == "bios_dir":
            n = sum(1 for _ in p.rglob("*") if _.is_file())
            return {"ok": True, "text": f"plików: {n}"}
        if key == "logo_pack_dir":
            from emustart import mediastore
            return {"ok": True, "text": f"logo w magazynie: {len(mediastore.files('_logopack'))}"}
        if key == "emu_root":
            ra = any((p / d / "retroarch.exe").is_file() for d in ("", "RetroArch", "retroarch")) or \
                any(p.glob("*/retroarch.exe"))
            return {"ok": True, "text": "RetroArch znaleziony" if ra else "bez RetroArcha"}
        return {"ok": True, "text": "jest"}
    except OSError as ex:
        return {"ok": False, "text": f"błąd dostępu: {ex}"}


def _mapped(value: str) -> bool:
    """Ścieżka zaczyna się literą dysku, który nie jest lokalny (dla konta serwera)."""
    m = re.match(r"^([A-Za-z]):", value)
    if not m or os.name != "nt":
        return False
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(f"{m.group(1)}:\\") in (1, 4)   # brak / sieciowy
    except Exception:
        return False


def get(cfg: dict) -> dict:
    out = []
    for key, (label, kind, optional, hint) in FIELDS.items():
        val = _value(cfg, key)
        st = [_status(key, v) for v in val] if kind == "dirs" else _status(key, val)
        out.append({"key": key, "label": label, "kind": kind, "optional": optional, "hint": hint,
                    "value": val, "status": st})
    return {"ok": True, "fields": out}


def _check(key: str, value: str) -> str:
    """Powód odrzucenia ścieżki albo '' (z punktu widzenia serwera)."""
    kind = FIELDS[key][1]
    if not value:
        return "" if FIELDS[key][2] else "Podaj folder."
    if not os.path.isabs(value) and not value.startswith("\\\\"):
        return f"„{value}” — podaj pełną ścieżkę."
    p = Path(value)
    ok = p.is_file() if kind == "file" else p.is_dir()
    if ok:
        return ""
    what = "pliku" if kind == "file" else "folderu"
    return f"Serwer nie widzi {what} „{value}”." + (" " + _UNC_HINT if _mapped(value) else "")


def set_value(api, key: str, value) -> dict:
    """Zapis ustawienia po sprawdzeniu; zmiany działają od razu (skan, folder profili, MAME)."""
    if key not in FIELDS:
        return {"ok": False, "reason": "Nieznane ustawienie."}
    cfg = api._cfg
    if FIELDS[key][1] == "dirs":
        vals = [str(v).strip() for v in (value or []) if str(v).strip()]
        vals = list(dict.fromkeys(vals))
        if not vals:
            return {"ok": False, "reason": "Potrzebny co najmniej jeden folder z grami."}
        for v in vals:
            why = _check(key, v)
            if why:
                return {"ok": False, "reason": why}
        changed = vals != config.rom_roots(cfg)
        cfg["rom_roots"] = vals
        cfg["rom_root"] = ""
    else:
        v = str(value or "").strip()
        why = _check(key, v)
        if why:
            return {"ok": False, "reason": why}
        changed = v != str(cfg.get(key) or "")
        cfg[key] = v
    config.save(cfg)
    log.info("panel: %s = %s", key, cfg.get(key))
    if changed:
        _apply(api, key)
    return {"ok": True, **get(cfg)}


def _apply(api, key: str) -> None:
    if key == "rom_roots":
        api.rescan()                                  # lista gier z nowych folderów (w tle)
    elif key == "profiles_nas":
        from emustart import remote
        remote.init_nas(api._cfg)
    elif key == "logo_pack_dir" and api._cfg.get("logo_pack_dir"):
        def run(folder=api._cfg["logo_pack_dir"]):
            from emustart import logos
            try:
                logos.import_pack(Path(folder))
            except Exception:
                log.exception("paczka logo")
        threading.Thread(target=run, daemon=True, name="paczka-logo").start()
    elif key == "mame_exe":
        threading.Thread(target=api._arcade_meta, daemon=True, name="arcade").start()


def backup_dir(cfg: dict) -> Path:
    return Path(cfg.get("backup_dir") or default_dir("backup_dir"))


def unc(path: str) -> str:
    """Dysk sieciowy użytkownika (Z:\\…) → ścieżka UNC (\\\\NAS\\udział\\…) — panel, w sesji
    użytkownika (serwer na koncie SYSTEM liter dysków sieciowych nie zna)."""
    m = re.match(r"^([A-Za-z]):(.*)$", path or "")
    if not m or os.name != "nt":
        return path
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(1024)
        n = wintypes.DWORD(len(buf))
        if ctypes.windll.mpr.WNetGetConnectionW(f"{m.group(1)}:", buf, ctypes.byref(n)) == 0 and buf.value:
            return buf.value.rstrip("\\") + m.group(2)
    except Exception:
        pass
    return path
