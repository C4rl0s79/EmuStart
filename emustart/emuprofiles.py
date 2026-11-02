"""emuprofiles — profile emulatorów graczy na serwerze (Xenia Edge: profil Xboksa 360, XUID).

Profil tworzy emulator na urządzeniu (bez serwera). Urządzenie zgłasza profile, z których grało jego konto
(PUT /v1/emuprofiles); w panelu serwera (konto → pliki i kopie) można przypiąć jeden z nich do konta — od tej
chwili to stały profil gracza na każdym urządzeniu (GET /v1/emuprofiles). Przypiąć można tylko profil zgłoszony
przez urządzenie tego konta (nie cudze zapisy). Dane: `emuprofiles.json` obok `accounts.json`.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

from emustart import paths

EMUS = {"xenia_edge"}
_XUID = re.compile(r"^[0-9A-F]{16}$")
_db: dict | None = None
_lock = threading.Lock()


def _path():
    return paths.SERVER / "emuprofiles.json"


def _load() -> dict:
    global _db
    if _db is None:
        try:
            _db = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _db = {}
    return _db


def _save() -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(_load(), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def _acct(account: str) -> dict:
    return _load().setdefault("accounts", {}).setdefault(account.strip().lower(), {})


def state(account: str) -> dict:
    """{emulator: {"pinned": XUID | "", "reported": [{"xuid", "device", "seen"}]}} konta."""
    with _lock:
        a = (_load().get("accounts") or {}).get(account.strip().lower(), {})
        return {emu: {"pinned": (a.get(emu) or {}).get("pinned", ""),
                      "reported": sorted(((a.get(emu) or {}).get("reported") or {}).values(),
                                         key=lambda r: -r.get("seen", 0))}
                for emu in sorted(EMUS)}


def report(account: str, device: str, emu: str, xuids: list) -> int:
    """Profile, z których grało konto na urządzeniu; liczba przyjętych (zły XUID — pominięty)."""
    if emu not in EMUS or not account.strip():
        raise ValueError("nieznany emulator albo konto")
    good = [x for x in (str(v).upper() for v in (xuids or [])[:16]) if _XUID.match(x)]
    with _lock:
        rep = _acct(account).setdefault(emu, {}).setdefault("reported", {})
        for x in good:
            rep[x] = {"xuid": x, "device": str(device)[:80], "seen": int(time.time())}
        _save()
    return len(good)


def pin(account: str, emu: str, xuid: str) -> str:
    """Przypięcie profilu do konta („” — odpięcie). Zwraca powód odmowy albo „”."""
    if emu not in EMUS:
        return "Nieznany emulator."
    x = str(xuid or "").upper()
    with _lock:
        a = (_load().get("accounts") or {}).get(account.strip().lower())
        if a is None:
            return "To konto nie zgłosiło jeszcze żadnego profilu."
        e = a.setdefault(emu, {})
        if x and x not in (e.get("reported") or {}):
            return "Tego profilu nie zgłosiło żadne urządzenie tego konta."
        e["pinned"] = x
        _save()
    return ""
