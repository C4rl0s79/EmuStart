"""plock — jedna gra naraz na profil (konto), jak w Steam: blokada po stronie serwera.

Wcześniej blokadą był plik <profil>/lock w folderze profilu: urządzenie rozpoznawane po nazwie
komputera, zapis „przeczytaj, sprawdź, zapisz” — dwa urządzenia startujące w tej samej chwili
mogły wziąć ją obie. Teraz serwer trzyma blokady sam, zmienia je atomowo (jedna blokada
procesu) i rozpoznaje urządzenie po tokenie sesji (emustart/remote.py), nie po nazwie.

    POST /v1/lock/take     {lease, force}   weź (force — przejęcie po potwierdzeniu gracza)
    POST /v1/lock/renew    {lease}          odnów (tylko właściciel; inaczej „lost”)
    POST /v1/lock/release  {}               oddaj (tylko właściciel)
    POST /v1/lock/status   {}               kto gra

Dzierżawa jak dotąd: urządzenie w grze odnawia ją co minutę (telefon rzadziej, dłuższa
dzierżawa); urządzenie, które padło, przestaje blokować po jej upływie.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from emustart import paths

log = logging.getLogger("emustart.plock")

MIN_LEASE, MAX_LEASE, DEFAULT_LEASE = 30, 3600, 300
_lock = threading.Lock()
_locks: dict | None = None


def _file() -> Path:
    return paths.SERVER / "locks.json"


def _load() -> dict:
    global _locks
    if _locks is None:
        try:
            d = json.loads(_file().read_text(encoding="utf-8"))
            _locks = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            _locks = {}
    return _locks


def _save() -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(_locks or {}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, f)


def reset() -> None:
    """Testy: zapomnij stan w pamięci (plik zostaje)."""
    global _locks
    with _lock:
        _locks = None


def _active(cur: dict | None, now: float) -> bool:
    return bool(cur) and now - float(cur.get("time", 0)) < float(cur.get("lease", DEFAULT_LEASE))


def _lease(v) -> float:
    try:
        return float(min(MAX_LEASE, max(MIN_LEASE, float(v or DEFAULT_LEASE))))
    except (TypeError, ValueError):
        return float(DEFAULT_LEASE)


def _public(cur: dict | None, now: float) -> dict:
    if not _active(cur, now):
        return {"held": False}
    return {"held": True, "device_name": cur.get("name", ""), "since": cur.get("since", 0),
            "expires_in": round(float(cur["lease"]) - (now - float(cur["time"])))}


def take(account: str, device: str, name: str, lease=None, force: bool = False) -> dict:
    """Atomowe wzięcie blokady konta. Zajęta przez inne urządzenie (aktywna dzierżawa) —
    {"ok": False, "held_by": nazwa}; force — przejęcie (gracz potwierdził)."""
    key, now = account.lower(), time.time()
    with _lock:
        locks = _load()
        cur = locks.get(key)
        if _active(cur, now) and cur.get("device") != device:
            if not force:
                return {"ok": False, "held_by": cur.get("name") or "inne urządzenie", **_public(cur, now)}
            log.warning("profil %s przejęty: %s → %s", account, cur.get("name"), name)
        mine = _active(cur, now) and cur.get("device") == device
        locks[key] = {"device": device, "name": name, "time": now, "lease": _lease(lease),
                      "since": cur.get("since", now) if mine else now}
        _save()
    return {"ok": True, "took_over": bool(force and cur and _active(cur, now) and cur.get("device") != device)}


def renew(account: str, device: str, lease=None) -> dict:
    key, now = account.lower(), time.time()
    with _lock:
        locks = _load()
        cur = locks.get(key)
        if not cur or cur.get("device") != device:
            return {"ok": False, "lost": True, **_public(cur, now)}   # przejęta przez inne urządzenie
        cur.update(time=now, lease=_lease(lease if lease is not None else cur.get("lease")))
        _save()
    return {"ok": True}


def release(account: str, device: str) -> dict:
    key = account.lower()
    with _lock:
        locks = _load()
        cur = locks.get(key)
        if cur and cur.get("device") == device:
            locks.pop(key)
            _save()
            return {"ok": True}
    return {"ok": True, "not_mine": True}


def status(account: str) -> dict:
    with _lock:
        return {"ok": True, **_public(_load().get(account.lower()), time.time())}
