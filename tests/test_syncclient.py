"""Synchronizacja zapisów — klient (emustart/syncclient.py, savebundles.py) z prawdziwym
serwerem testowym: dwa urządzenia jednego konta, nazwy plików różne na urządzeniach,
konflikt rozstrzygany przez gracza, gra bez połączenia."""

from __future__ import annotations

from pathlib import Path

import pytest

from emustart import netsrc, profiles, savebundles, savesync, syncclient

from test_core import _write, env  # noqa: F401  (fixture)
from test_savesync_server import srv  # noqa: F401  (fixture)

GAME = {"es": "snes", "name": "Chrono Trigger (USA)", "rel": "Chrono Trigger (USA).zip"}
PC, TEL = 1, 2                       # dwa urządzenia = dwa stany lokalne (jedna baza w teście)


@pytest.fixture()
def net(env, srv, monkeypatch):
    cfg, _roms, tmp = env
    host, port = srv
    monkeypatch.setattr(netsrc, "endpoint", lambda c: (host, port, c["dev"]) if c.get("dev") else None)
    monkeypatch.setattr(netsrc, "auth_headers", lambda key: {"Authorization": f"Bearer {key}"})
    monkeypatch.setattr(profiles, "get", lambda pid: {"id": pid, "name": "Jezus", "nas_name": "Jezus"})
    monkeypatch.setattr(profiles, "account_of_device", lambda c: "Jezus")
    monkeypatch.setattr(profiles, "LOCAL", tmp / "profiles")
    return {PC: {**cfg, "dev": "pc"}, TEL: {**cfg, "dev": "tel"}}, tmp


def ra_bundles(tmp: Path, who: int, stem: str) -> list:
    base = tmp / f"dev{who}"
    names = sorted({stem, GAME["name"], Path(GAME["rel"]).stem})
    return [savebundles.Bundle("retroarch", "snes9x", "saves", base / "saves", stem, names).collect(),
            savebundles.Bundle("retroarch", "snes9x", "states", base / "states", stem, names).collect()]


def test_two_devices_names_differ_and_changes_flow(net):
    cfgs, tmp = net
    pc_stem, tel_stem = "Chrono Trigger (USA)", "Chrono_Trigger_(USA)_(MSU1)"
    _write(tmp / "dev1" / "saves" / f"{pc_stem}.srm", b"SRAM-PC")
    _write(tmp / "dev1" / "states" / f"{pc_stem}.state3", b"STAN-3")
    _write(tmp / "dev1" / "states" / f"{pc_stem}.state3.png", b"PNG")
    _write(tmp / "dev1" / "states" / "Inna gra.state1", b"INNA")              # nie tej gry
    r = syncclient.before(cfgs[PC], PC, ra_bundles(tmp, PC, pc_stem), GAME)
    assert r.uploaded == 2 and not r.conflicts

    # telefon / drugi PC: pusto → pobranie, pliki z nazwą od pliku gry na tym urządzeniu
    r = syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, tel_stem), GAME)
    assert r.downloaded == 2 and not r.conflicts
    assert (tmp / "dev2" / "saves" / f"{tel_stem}.srm").read_bytes() == b"SRAM-PC"
    assert (tmp / "dev2" / "states" / f"{tel_stem}.state3.png").read_bytes() == b"PNG"
    assert not (tmp / "dev2" / "states" / "Inna gra.state1").exists()

    # gra na PC → po grze na serwer; drugie urządzenie (bez zmian u siebie) pobiera
    _write(tmp / "dev1" / "saves" / f"{pc_stem}.srm", b"SRAM-PC-2")
    assert syncclient.after(cfgs[PC], PC, ra_bundles(tmp, PC, pc_stem), GAME).uploaded == 1
    r = syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, tel_stem), GAME)
    assert r.downloaded == 1 and (tmp / "dev2" / "saves" / f"{tel_stem}.srm").read_bytes() == b"SRAM-PC-2"
    # nic się nie zmieniło — nic się nie dzieje
    r = syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, tel_stem), GAME)
    assert (r.downloaded, r.uploaded, r.conflicts) == (0, 0, [])


def test_conflict_decided_by_player_nothing_lost(net):
    cfgs, tmp = net
    stem = "Chrono Trigger (USA)"
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"V1")
    syncclient.before(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, stem), GAME)
    # oba urządzenia grały (drugie bez połączenia) — tu i tam inna wersja
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"PC")
    syncclient.after(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    _write(tmp / "dev2" / "saves" / f"{stem}.srm", b"TEL")
    r = syncclient.after(cfgs[TEL], TEL, ra_bundles(tmp, TEL, stem), GAME)
    assert r.uploaded == 0 and len(r.conflicts) == 1                         # serwer: 409, nic nie nadpisane

    r = syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, stem), GAME)
    assert len(r.conflicts) == 1
    d = r.conflicts[0].describe()
    assert d["kind"] == "saves" and d["server_revision"] == 2 and d["server_device"]
    # gracz: „weź z serwera” — wersja z tego urządzenia w kopii zapasowej
    syncclient.resolve(cfgs[TEL], TEL, GAME, r.conflicts, "remote")
    assert (tmp / "dev2" / "saves" / f"{stem}.srm").read_bytes() == b"PC"
    assert [p.read_bytes() for p in (profiles.LOCAL / str(TEL)).rglob(f"{stem}.srm")] == [b"TEL"]

    # i odwrotnie: „zostaw z tego urządzenia” — wersja serwera do historii na serwerze
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"PC-3")
    syncclient.after(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    _write(tmp / "dev2" / "saves" / f"{stem}.srm", b"TEL-3")
    r = syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, stem), GAME)
    syncclient.resolve(cfgs[TEL], TEL, GAME, r.conflicts, "local")
    r = syncclient.before(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    assert r.downloaded == 1 and (tmp / "dev1" / "saves" / f"{stem}.srm").read_bytes() == b"TEL-3"
    _p, gid = syncclient.game_ids(GAME)
    hist = savesync.history("Jezus", "snes", gid)[0]["history"]
    assert len(hist) >= 3                                                    # wersja PC-3 też jest


def test_first_sync_with_same_files_is_not_a_conflict(net):
    """Oba urządzenia miały już te same zapisy (np. z dotychczasowej synchronizacji folderów)."""
    cfgs, tmp = net
    stem = "Chrono Trigger (USA)"
    for who in (PC, TEL):
        _write(tmp / f"dev{who}" / "saves" / f"{stem}.srm", b"TO-SAMO")
    syncclient.before(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    r = syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, stem), GAME)
    assert not r.conflicts and r.downloaded == 0


def test_offline_play_uploads_later(net, monkeypatch):
    cfgs, tmp = net
    stem = "Chrono Trigger (USA)"
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"V1")
    syncclient.before(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"OFFLINE")
    off = {**cfgs[PC], "dev": ""}                                            # brak serwera
    r = syncclient.after(off, PC, ra_bundles(tmp, PC, stem), GAME)
    assert r.offline and syncclient.status(off, PC, GAME)["state"] == "pending"
    assert syncclient.retry(cfgs[PC]) == 1
    assert syncclient.status(cfgs[PC], PC, GAME)["state"] == "synced"
    r = syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, stem), GAME)
    assert (tmp / "dev2" / "saves" / f"{stem}.srm").read_bytes() == b"OFFLINE"


def test_memcard_bundles_use_game_card(net):
    cfgs, tmp = net
    g = {"es": "ps2", "name": "Gran Turismo 4 (Europe)", "rel": "gt4.chd"}
    card = savebundles.card_stem(Path("gt4.chd"), g)
    _write(tmp / "dev1" / "mc" / f"{card}.ps2", b"KARTA")
    _write(tmp / "dev1" / "mc" / "Mcd001.ps2", b"WSPOLNA")                   # nie należy do paczki
    b = savebundles.Bundle("pcsx2", "", "memcards", tmp / "dev1" / "mc", card, [card]).collect()
    assert [a for _p, a in b.files] == ["game.ps2"]
    assert syncclient.before(cfgs[PC], PC, [b], g).uploaded == 1


def test_bundle_rejects_unsafe_names(tmp_path):
    b = savebundles.Bundle("retroarch", "snes9x", "saves", tmp_path, "Gra", ["Gra"])
    for bad in ("../x.srm", "game/../../x", "inna.srm"):
        with pytest.raises(ValueError):
            b.local_path(bad)
    assert savebundles.core_id('-L "D:\\RA\\cores\\bsnes_libretro.dll"') == "bsnes"


# ── po zalogowaniu / przy starcie (pull_all), wylogowanie z niewysłanymi zapisami ──

def _two_devices(cfgs, tmp, stem="Chrono Trigger (USA)"):
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"V1")
    syncclient.before(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    syncclient.before(cfgs[TEL], TEL, ra_bundles(tmp, TEL, stem), GAME)
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"V2")
    syncclient.after(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)       # PC wysłał nowszą wersję
    return tmp / "dev2" / "saves" / f"{stem}.srm"


def test_pull_after_login_unchanged_conflict_offline(net):
    cfgs, tmp = net
    tel = _two_devices(cfgs, tmp)
    off = {**cfgs[TEL], "dev": ""}
    r = syncclient.pull_all(off, TEL)
    assert r["offline"] and tel.read_bytes() == b"V1"                       # bez serwera nic się nie zmienia
    _write(tel, b"TEL-LOCAL")                                                # zmiana tu i tam — konflikt
    r = syncclient.pull_all(cfgs[TEL], TEL)
    assert r == {"downloaded": 0, "conflicts": 1, "offline": ""} and tel.read_bytes() == b"TEL-LOCAL"
    _write(tel, b"V1")                                                       # bez zmian lokalnie — pobranie
    r = syncclient.pull_all(cfgs[TEL], TEL)
    assert r["downloaded"] == 1 and tel.read_bytes() == b"V2"


def test_pending_saves_of_previous_account_never_land_on_new_one(net, monkeypatch):
    from emustart import devices
    cfgs, tmp = net
    stem = "Chrono Trigger (USA)"
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"V1")
    syncclient.before(cfgs[PC], PC, ra_bundles(tmp, PC, stem), GAME)
    _write(tmp / "dev1" / "saves" / f"{stem}.srm", b"OFFLINE")
    syncclient.after({**cfgs[PC], "dev": ""}, PC, ra_bundles(tmp, PC, stem), GAME)
    assert [p["account"] for p in syncclient.pending(cfgs[PC])] == ["Jezus"]
    # komputer zalogowany teraz kontem Ola (ten sam profil lokalny)
    monkeypatch.setattr(devices, "account_raw", lambda did: "Ola")
    monkeypatch.setattr(profiles, "account_of_device", lambda c: "Ola")
    monkeypatch.setattr(profiles, "get", lambda pid: {"id": pid, "name": "Ola", "nas_name": "Ola"})
    assert syncclient.retry(cfgs[PC]) == 0                                   # bez tokenu konta Jezus — czeka
    assert syncclient.pending(cfgs[PC], "Ola") == [] and len(syncclient.pending(cfgs[PC], "Jezus")) == 1
    _p, gid = syncclient.game_ids(GAME)
    assert savesync.manifest("Ola", "pc", "snes", gid, urls=False)["revision"] == 0   # nic na koncie Oli
    assert syncclient.pull_all(cfgs[PC], PC)["downloaded"] == 0             # paczki Jezusa pominięte


def test_logout_waits_for_unsent_saves(monkeypatch):
    from emustart import api as api_mod, config, keystore, launcher, nasfs
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg = {"server_key": "dev:abc", "server_url": "https://x"}
    calls = []
    monkeypatch.setattr(launcher, "wait_post", lambda t=None: calls.append("wait") or True)
    monkeypatch.setattr(syncclient, "retry", lambda cfg: calls.append("retry") or 0)
    left = [{"name": "Chrono Trigger", "platform": "snes", "game": "g1", "account": "Jezus"}]
    monkeypatch.setattr(syncclient, "pending", lambda cfg, account="": left)
    monkeypatch.setattr(api_mod.netsrc, "authed_post", lambda *a_, **k: {"ok": True})
    monkeypatch.setattr(api_mod.netsrc, "forget_server", lambda: None)
    monkeypatch.setattr(keystore, "delete", lambda *a_: None)
    monkeypatch.setattr(config, "save", lambda c: None)
    monkeypatch.setattr(nasfs, "reset", lambda: None)
    r = a.server_logout()
    assert r["ask"] and r["pending"] == 1 and r["games"] == ["Chrono Trigger"] and a._cfg.get("server_key")
    assert calls == ["wait", "retry"]                                        # najpierw zapisy po ostatniej grze
    left.clear()
    assert a.server_logout()["ok"] and not a._cfg.get("server_key")          # wszystko wysłane — wylogowanie
    a._cfg["server_key"] = "dev:abc"
    left.append({"name": "X", "platform": "snes", "game": "g2", "account": "Jezus"})
    assert a.server_logout(force=True)["ok"]                                 # „wyloguj mimo to”

    class S:
        phase = "running"
        game = {}
    a._cfg["server_key"] = "dev:abc"
    a._session = S()
    assert not a.server_logout(force=True)["ok"]                             # w trakcie gry — zablokowane
