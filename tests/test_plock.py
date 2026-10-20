"""Blokada profilu po stronie serwera (emustart/plock.py): jedna gra naraz na konto."""

from __future__ import annotations

import threading
import time

import pytest

from emustart import plock


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from emustart import paths
    monkeypatch.setattr(paths, "SERVER", tmp_path)
    plock.reset()
    yield
    plock.reset()


def test_simultaneous_take_only_one_wins():
    """Dwa urządzenia startują w tej samej chwili — blokadę dostaje dokładnie jedno."""
    results = {}
    go = threading.Barrier(8)

    def grab(i):
        go.wait()
        results[i] = plock.take("Jezus", f"dev{i}", f"Urządzenie {i}")["ok"]
    threads = [threading.Thread(target=grab, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(results.values()) == 1


def test_device_identity_not_name():
    """Ta sama nazwa komputera, inne urządzenie (token) — blokuje; to samo urządzenie — odnawia."""
    assert plock.take("Jezus", "a", "PC")["ok"]
    r = plock.take("Jezus", "b", "PC")
    assert not r["ok"] and r["held_by"] == "PC"
    assert plock.take("Jezus", "a", "PC")["ok"]                         # ponowne wzięcie przez właściciela
    assert plock.take("Ola", "b", "PC")["ok"]                           # inne konto — osobna blokada


def test_takeover_only_with_force_and_old_owner_loses():
    plock.take("Jezus", "tel", "Telefon")
    assert not plock.take("Jezus", "pc", "PC")["ok"]
    r = plock.take("Jezus", "pc", "PC", force=True)
    assert r["ok"] and r["took_over"]
    assert plock.renew("Jezus", "tel")["lost"]                          # telefon nie odbierze profilu
    assert plock.release("Jezus", "tel").get("not_mine")
    assert plock.status("Jezus")["device_name"] == "PC"


def test_lease_expiry_and_renew(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    plock.take("Jezus", "tel", "Telefon", lease=60)
    now[0] += 50
    assert plock.renew("Jezus", "tel", lease=60)["ok"]                  # odnowiona w trakcie gry
    now[0] += 50
    assert not plock.take("Jezus", "pc", "PC")["ok"]                    # dzierżawa trwa (50 < 60)
    now[0] += 20
    assert plock.take("Jezus", "pc", "PC")["ok"]                        # telefon padł — wygasła
    plock.release("Jezus", "pc")
    assert plock.status("Jezus") == {"ok": True, "held": False}


def test_state_survives_server_restart():
    plock.take("Jezus", "tel", "Telefon")
    plock.reset()                                                       # restart: stan z pliku
    assert not plock.take("Jezus", "pc", "PC")["ok"]
