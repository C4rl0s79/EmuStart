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
    "gog_depots_dir": ("Archiwum depotów GOG (opcjonalnie)", "dir", True,
                       "Folder _depots z GOG Managera (archive.json, chunks, manifests, products). Serwer tylko "
                       "czyta archiwum i udostępnia kompletne buildy zalogowanym urządzeniom — nie łączy się z GOG "
                       "i niczego nie pobiera. Zapisz ponownie, aby odświeżyć indeks."),
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
            return _pack_status(empty="nie ustawiono")
        d = default_dir(key)
        return {"ok": True, "text": f"domyślnie: {d}" if d else "nie ustawiono"}
    p = Path(value)
    try:
        if kind == "file":
            return {"ok": p.is_file(), "text": "jest" if p.is_file() else "serwer nie widzi pliku"}
        if not p.is_dir() and key == "logo_pack_dir" and not logos_import()["running"]:
            st = _pack_status()
            if st["ok"] and st["text"].startswith("logo w magazynie"):
                return {"ok": True, "text": st["text"] + " (folder już usunięty)"}
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
            return _pack_status()
        if key == "gog_depots_dir":
            return gog_status()
        if key == "emu_root":
            ra = any((p / d / "retroarch.exe").is_file() for d in ("", "RetroArch", "retroarch")) or \
                any(p.glob("*/retroarch.exe"))
            return {"ok": True, "text": "RetroArch znaleziony" if ra else "bez RetroArcha"}
        return {"ok": True, "text": "jest"}
    except OSError as ex:
        return {"ok": False, "text": f"błąd dostępu: {ex}"}


def logos_import() -> dict:
    from emustart import logos
    return logos.IMPORT


def _pack_status(empty: str = "") -> dict:
    """Paczka logo: import w toku, błąd ostatniego importu albo liczba logo w magazynie."""
    from emustart import mediastore
    imp = logos_import()
    if imp["running"]:
        return {"ok": True, "text": f"wczytuję paczkę: {imp['done']} / {imp['total'] or '…'}", "busy": True}
    n = len(mediastore.files("_logopack"))
    if imp["error"]:
        return {"ok": False, "text": f"{imp['error']} (logo w magazynie: {n})"}
    return {"ok": True, "text": f"logo w magazynie: {n}" if n or not empty else empty}


def gog_status() -> dict:
    """Archiwum GOG: indeksowanie w toku, błąd albo liczba udostępnianych gier i pominiętych buildów (z powodem)."""
    from emustart import gogarchive
    st = gogarchive.status()
    if st["running"]:
        return {"ok": True, "text": f"indeksuję archiwum GOG: {st['done']} / {st['total'] or '…'}", "busy": True}
    if st["error"]:
        return {"ok": False, "text": st["error"]}
    text = f"gry GOG: {st['games']} (buildów: {st['builds']})"
    if st["rejected"]:
        why = "; ".join(f"{r.get('title') or r.get('product_id')}: {r.get('reason')}" for r in st["rejected"][:3])
        text += f" · pominięte: {len(st['rejected'])} — {why}" + (" …" if len(st["rejected"]) > 3 else "")
    return {"ok": True, "text": text}


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
    if changed or key in ("logo_pack_dir", "gog_depots_dir"):   # ta sama ścieżka — wczytaj / zindeksuj ponownie
        _apply(api, key)
    return {"ok": True, **get(cfg)}


def _apply(api, key: str) -> None:
    if key == "rom_roots":
        api.rescan()                                  # lista gier z nowych folderów (w tle)
    elif key == "profiles_nas":
        from emustart import remote
        remote.init_nas(api._cfg)
    elif key == "gog_depots_dir":
        from emustart import gogarchive
        # pusta ścieżka = nic nie udostępniamy (także gdy w tle trwa indeksowanie poprzedniej)
        gogarchive.refresh(api._cfg.get("gog_depots_dir") or "")
    elif key == "logo_pack_dir" and api._cfg.get("logo_pack_dir"):
        if logos_import()["running"]:
            return
        logos_import().update(running=True, done=0, total=0, error="")   # status „wczytuję” od razu
        def run(folder=api._cfg["logo_pack_dir"]):
            from emustart import logos
            try:
                logos.import_pack(Path(folder))
            except Exception:
                log.exception("paczka logo")
            finally:
                logos.IMPORT["running"] = False
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
