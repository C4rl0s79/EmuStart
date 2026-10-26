"""ps2states — format stanów PS2 na PC (PCSX2) i telefonie (ArmSX2), z paczek zapisów (0.49).

Stany PS2 są wspólną paczką PCSX2 i ArmSX2 (emustart/savebundles.py, `pcsx2/states`), ale stan
wczytuje się tylko w wersji emulatora o tym samym formacie stanów. Plik stanu (.p2s) to ZIP
z wpisem `PCSX2 Savestate Version.id` (u32, little-endian) — numer formatu.

Serwer czyta format najnowszego stanu z każdej wersji paczki (aktualnej i z historii) i dzieli
je według urządzenia, które wysłało paczkę (PC / telefon — emustart/devices.py). Wynik:
ostatni znany format na PC i na telefonie. Odczyt jest zapamiętany po identyfikatorze paczki
(paczki są niezmienne) w `savesync/_ps2formats.json`.

Na tej podstawie emustore wstrzymuje aktualizację PCSX2 (emustart/emustore.py, `hold`).
"""

from __future__ import annotations

import io
import json
import logging
import os
import struct
import threading
import zipfile

from emustart import savesync

log = logging.getLogger("emustart.ps2states")

ENTRY = "PCSX2 Savestate Version.id"
MAX_STATE = 256 * 1024 * 1024
_lock = threading.Lock()


def state_version(data: bytes) -> int | None:
    """Numer formatu z pliku stanu (.p2s); None — nie da się odczytać."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            raw = z.read(ENTRY)
        return struct.unpack("<I", raw[:4])[0] if len(raw) >= 4 else None
    except (zipfile.BadZipFile, KeyError, OSError, struct.error):
        return None


def bundle_version(path) -> int | None:
    """Format najnowszego stanu w paczce (ZIP z plikami .p2s)."""
    try:
        with zipfile.ZipFile(path) as z:
            states = [i for i in z.infolist() if i.filename.lower().endswith(".p2s") and i.file_size <= MAX_STATE]
            for info in sorted(states, key=lambda i: i.date_time, reverse=True):
                v = state_version(z.read(info))
                if v is not None:
                    return v
    except (zipfile.BadZipFile, OSError):
        pass
    return None


def _cache_path():
    return savesync._root() / "_ps2formats.json"


def fmt(v: int | None) -> str:
    """Numer formatu do wyświetlenia, jak w PCSX2 (szesnastkowo: starsze 16 bitów — wersja główna,
    młodsze — poboczna), np. 9a51.0."""
    return "—" if v is None else f"{v >> 16:x}.{v & 0xFFFF:x}"


def formats() -> dict:
    """{"pc": {version, at, game} | None, "phone": … | None, "games": liczba gier ze stanami PS2}."""
    from emustart import devices
    root = savesync._root()
    with _lock:
        try:
            cache = json.loads(_cache_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cache = {}
        dirty = False
        best: dict = {"pc": None, "phone": None}
        games = 0
        for man in root.glob("*/*/*/manifest.json") if root.is_dir() else []:
            if man.parts[-4].startswith("_"):
                continue
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            b = (((m.get("emulators") or {}).get("pcsx2") or {}).get("bundles") or {}).get("states")
            if not b or not b.get("bundle_id"):
                continue
            games += 1
            for e in [b] + list(b.get("history") or []):
                bid = e.get("bundle_id")
                if not bid:
                    continue
                if bid not in cache:
                    cache[bid] = bundle_version(man.parent / "bundles" / f"{bid}.zip")
                    dirty = True
                v = cache[bid]
                cls = (devices.listing_one(e.get("updated_by_device_id") or "") or {}).get("class") or ""
                if v is None or cls not in best:
                    continue
                at = float(e.get("updated_at") or 0)
                if best[cls] is None or at > best[cls]["at"]:
                    best[cls] = {"version": v, "at": at, "game": man.parent.name, "label": fmt(v)}
        if dirty:
            tmp = _cache_path().with_name("_ps2formats.json.tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(cache), encoding="utf-8")
            os.replace(tmp, _cache_path())
    return {**best, "games": games}


def hold_reason(f: dict | None = None) -> dict | None:
    """Czy wstrzymać nową wersję PCSX2: None — nie (brak wspólnych stanów PC i telefonu);
    inaczej {"kind": "diverged" | "shared", "text"} — powód dla panelu.

    - "diverged" — formaty PC i telefonu już się różnią: kolejna wersja PCSX2 tylko pogłębi
      rozbieżność, dopóki ArmSX2 nie dogoni PCSX2;
    - "ahead" — telefon ma nowszy format: aktualizacja możliwa (zwykle przywraca zgodność);
    - "shared" — formaty zgodne: aktualizacja możliwa, ale nowa wersja PCSX2 zwykle zmienia
      format — stany z obecnej wersji mogą przestać się wczytywać, a nowe nie wczytają się na
      telefonie.
    We wszystkich przypadkach decyduje administrator (panel → Emulatory)."""
    f = f if f is not None else formats()
    pc, ph = f.get("pc"), f.get("phone")
    if not pc or not ph:
        return None
    if ph["version"] < pc["version"]:
        return {"kind": "diverged", "pc": pc["label"], "phone": ph["label"],
                "text": f"Stany PS2: PC ma format {pc['label']}, telefon (ArmSX2) {ph['label']} — stany z PC nie "
                        "wczytują się na telefonie. Nowa wersja PCSX2 wstrzymana, dopóki ArmSX2 nie dogoni PCSX2."}
    if ph["version"] > pc["version"]:
        return {"kind": "ahead", "pc": pc["label"], "phone": ph["label"],
                "text": f"Stany PS2: telefon (ArmSX2) ma już nowszy format ({ph['label']}) niż PC ({pc['label']}) — "
                        "aktualizacja PCSX2 jest możliwa i prawdopodobnie przywróci zgodność, ale stany zapisane na PC "
                        "w obecnej wersji mogą przestać się wczytywać. Zapisz grę na kartę pamięci."}
    return {"kind": "shared", "pc": pc["label"], "phone": ph["label"],
            "text": f"Stany PS2 wspólne dla PC i telefonu (format PC {pc['label']}, telefon {ph['label']}). "
                    "Aktualizacja możliwa, ale po niej stany zapisane w obecnej wersji mogą się nie wczytywać "
                    "— ani w nowym PCSX2, ani (stany z nowego PCSX2) na telefonie. Zapisz grę na kartę pamięci."}
