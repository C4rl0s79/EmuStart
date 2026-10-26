r"""Izolacja testów od prawdziwych danych EmuStart.

Testy uruchamiane są z folderu programu (np. D:\emustart) — tego samego, w którym leżą
config.json, data\ i profiles\ działającego EmuStart. Bez izolacji test, który zapisuje
konfigurację, nadpisywał prawdziwy config.json (2026-10-12: klucz testowy zamiast ustawień,
EmuStart pokazywał „pierwsze uruchomienie”). Każdy test dostaje więc własny, tymczasowy
folder programu, a po wszystkich testach sprawdzamy, że prawdziwe pliki się nie zmieniły.
"""

from __future__ import annotations

import hashlib

import pytest

from emustart import paths

_REAL = {"config": paths.CONFIG_PATH, "db": paths.DB_PATH}
_BEFORE: dict = {}


def _digest(p) -> str:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()
    except OSError:
        return "brak"


def pytest_sessionstart(session):
    for k, p in _REAL.items():
        _BEFORE[k] = _digest(p)
    # poza testem (wątki w tle, które otwierają bazę już po teście) — też folder tymczasowy,
    # nigdy prawdziwe dane programu
    import tempfile
    from pathlib import Path
    base = Path(tempfile.mkdtemp(prefix="emustart-sesja-"))
    for name, rel in {"APP": "", "DATA": "data", "MEDIA": "data/media", "DB_PATH": "data/library.sqlite",
                      "CONFIG_PATH": "config.json", "LOGS": "logs", "DEFAULT_CACHE": "cache",
                      "RUN_TMP": "run", "SERVER": "server"}.items():
        setattr(paths, name, base / rel if rel else base)


def pytest_sessionfinish(session, exitstatus):
    changed = [str(p) for k, p in _REAL.items() if _digest(p) != _BEFORE.get(k)]
    if changed:
        session.exitstatus = 1
        # bez polskich znaków w konsoli CI (cp1252) — inaczej sam komunikat kończył się błędem
        msg = "\n\nBŁĄD: testy zmieniły prawdziwe pliki EmuStart: " + ", ".join(changed)
        print(msg.encode("ascii", "backslashreplace").decode("ascii"))


@pytest.fixture(autouse=True)
def _isolated_app(tmp_path_factory, monkeypatch):
    from emustart import library, profiles, server
    base = tmp_path_factory.mktemp("emustart-app")
    monkeypatch.setattr(paths, "APP", base)
    monkeypatch.setattr(paths, "DATA", base / "data")
    monkeypatch.setattr(paths, "MEDIA", base / "data" / "media")
    monkeypatch.setattr(paths, "DB_PATH", base / "data" / "library.sqlite")
    monkeypatch.setattr(paths, "CONFIG_PATH", base / "config.json")
    monkeypatch.setattr(paths, "LOGS", base / "logs")
    monkeypatch.setattr(paths, "DEFAULT_CACHE", base / "cache")
    monkeypatch.setattr(paths, "RUN_TMP", base / "run")
    monkeypatch.setattr(paths, "SERVER", base / "server")
    from emustart import devices, netsrc, remote
    devices.reset_cache()
    from emustart import enroll as _enroll
    _enroll.reset()
    from emustart import accounts as _accounts
    _accounts.reset()
    from emustart import invites, public, webportal
    invites.reset()
    webportal.reset()
    monkeypatch.setitem(public.TUNNEL, "url", "")
    monkeypatch.setattr(_accounts, "N", 2 ** 10)          # szybki scrypt w testach
    netsrc._learned.clear()
    netsrc.forget_best()
    netsrc._PINS.clear()
    netsrc._CA.clear()
    netsrc._EPINFO.clear()
    netsrc._session.clear()
    netsrc._signer.clear()
    netsrc._hint.clear()
    netsrc._cfg_ref.clear()
    from emustart import keystore
    monkeypatch.setattr(keystore, "USE_TPM", False)
    monkeypatch.setattr(remote, "TLS", {})
    from emustart import srvadmin            # adresy tego komputera — testy podają je same
    monkeypatch.setattr(srvadmin, "_addresses", lambda: [])
    monkeypatch.setattr(profiles, "LOCAL", base / "profiles")
    if "/media/" in getattr(server, "ROUTES", {}):
        monkeypatch.setitem(server.ROUTES, "/media/", base / "data" / "media")
    library._local.con = None
    yield
    library._local.con = None


@pytest.fixture()
def server_files(monkeypatch):
    """EmuStart na PC pobiera gry tylko z serwera — w testach uruchamiania pliki „z serwera”
    to pliki z folderu gry w teście (bez prawdziwego serwera)."""
    from emustart import library, netsrc

    def fake(cfg, g):
        d = library.game_dir(g)
        return {rel: d / rel for rel, _size, _mt in g["files"] if (d / rel).is_file()}
    monkeypatch.setattr(netsrc, "sources", fake)
