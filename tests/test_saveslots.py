"""Sloty zapisów gry i edycja stanów (emustart/saveslots.py, syncclient.py) z prawdziwym
serwerem testowym: dwa urządzenia jednego konta, zmiana slotu, usunięte i przenumerowane
stany nie wracają przy sumowaniu, kosz, straż numerów, stary serwer bez slotów."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from emustart import library, savebundles, saveslots, savesync, syncclient

from test_core import _write, env  # noqa: F401  (fixture)
from test_savesync_server import srv  # noqa: F401  (fixture)
from test_syncclient import GAME, PC, TEL, net, ra_bundles  # noqa: F401  (fixture)

STEM = "Chrono Trigger (USA)"
PLAT, GID = syncclient.game_ids(GAME)


def states_dir(tmp, who):
    return tmp / f"dev{who}" / "states"


def put_state(tmp, who, n, data: bytes, png: bytes = b""):
    name = f"{STEM}.state{n}" if n != "auto" else f"{STEM}.state.auto"
    _write(states_dir(tmp, who) / name, data)
    if png:
        _write(states_dir(tmp, who) / (name + ".png"), png)


def local_states(tmp, who) -> dict:
    d = states_dir(tmp, who)
    return {p.name.replace(STEM, "game"): p.read_bytes() for p in d.iterdir()} if d.is_dir() else {}


def sync(cfgs, tmp, who):
    return syncclient.before(cfgs[who], who, ra_bundles(tmp, who, STEM), GAME)


def editor(cfgs, tmp, who):
    return saveslots.Editor(cfgs[who], who, GAME, ra_bundles(tmp, who, STEM))


def server_slot(slot, kind="states"):
    m = savesync.manifest("Jezus", "pc", PLAT, GID, urls=False)
    return m["slots"][str(slot)]["emulators"].get("retroarch", {}).get("cores", {}).get("snes9x", {}) \
        .get("bundles", {}).get(kind) or {}


def test_old_table_migrates_to_slot_1(env):
    """Baza sprzed slotów: wiersze synchronizacji zostają jako slot 1."""
    library._local.con = None
    c = library.db()
    c.execute("""CREATE TABLE save_sync(profile_id INTEGER NOT NULL, platform TEXT NOT NULL, game TEXT NOT NULL,
        emu TEXT NOT NULL, core TEXT NOT NULL, kind TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
        content TEXT NOT NULL DEFAULT '', dirty INTEGER NOT NULL DEFAULT 0, spec TEXT NOT NULL DEFAULT '',
        updated REAL NOT NULL DEFAULT 0, account TEXT NOT NULL DEFAULT '',
        PRIMARY KEY(profile_id, platform, game, emu, core, kind))""")
    c.execute("INSERT INTO save_sync(profile_id, platform, game, emu, core, kind, revision, content) "
              "VALUES(1,'snes','g1','retroarch','snes9x','states',7,'abc')")
    c.commit()
    syncclient._schema.clear()
    rows = [dict(r) for r in syncclient._db().execute("SELECT * FROM save_sync")]
    assert rows == [{**rows[0], "slot": 1, "revision": 7, "content": "abc", "removed": ""}]
    with pytest.raises(sqlite3.IntegrityError):         # slot w kluczu: ten sam klucz dwa razy — nie
        syncclient._db().execute("INSERT INTO save_sync(profile_id, platform, game, slot, emu, core, kind) "
                                 "VALUES(1,'snes','g1',1,'retroarch','snes9x','states')")
    syncclient._db().execute("INSERT INTO save_sync(profile_id, platform, game, slot, emu, core, kind) "
                             "VALUES(1,'snes','g1',2,'retroarch','snes9x','states')")


def test_all_slots_sync_and_active_slot_follows_server(net):
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"P1-STAN-1")
    _write(tmp / "dev1" / "saves" / f"{STEM}.srm", b"SRAM-1")
    assert sync(cfgs, tmp, PC).uploaded == 2
    # PC: slot 2 — puste foldery emulatora, nowe przejście
    bundles = ra_bundles(tmp, PC, STEM)
    r = saveslots.set_active(cfgs[PC], PC, GAME, bundles, 2)
    assert r == {"ok": True, "slot": 2, "sent": True}
    assert local_states(tmp, PC) == {} and not (tmp / "dev1" / "saves" / f"{STEM}.srm").exists()
    put_state(tmp, PC, 1, b"P2-STAN-1")
    assert syncclient.after(cfgs[PC], PC, ra_bundles(tmp, PC, STEM), GAME).uploaded == 1
    assert server_slot(1)["file_count"] == 1 and server_slot(2)["file_count"] == 1
    assert savesync.manifest("Jezus", "pc", PLAT, GID, urls=False)["active_slot"] == 2

    # telefon: pierwsza synchronizacja — w folderach emulatora slot 2 (aktywny na serwerze),
    # slot 1 w folderze profilu
    r = sync(cfgs, tmp, TEL)
    assert r.slot == 2 and not r.conflicts
    assert local_states(tmp, TEL) == {"game.state1": b"P2-STAN-1"}
    store1 = saveslots.store_bundle(TEL, PLAT, GID, 1, ra_bundles(tmp, TEL, STEM)[1])
    assert {a: Path(p).read_bytes() for p, a in store1.files} == {"game.state1": b"P1-STAN-1"}
    assert saveslots.here(TEL, PLAT, GID) == 2

    # telefon gra w slocie 2, PC wraca do slotu 1 — telefon przy następnej grze przełącza się
    put_state(tmp, TEL, 2, b"P2-STAN-2-TEL")
    syncclient.after(cfgs[TEL], TEL, ra_bundles(tmp, TEL, STEM), GAME)
    saveslots.set_active(cfgs[PC], PC, GAME, ra_bundles(tmp, PC, STEM), 1)
    assert local_states(tmp, PC) == {"game.state1": b"P1-STAN-1"}
    assert (tmp / "dev1" / "saves" / f"{STEM}.srm").read_bytes() == b"SRAM-1"
    r = sync(cfgs, tmp, TEL)
    assert r.slot == 1 and local_states(tmp, TEL) == {"game.state1": b"P1-STAN-1"}
    # zmiany slotu 2 z telefonu dotarły na PC (slot nieaktywny — folder profilu)
    sync(cfgs, tmp, PC)
    store2 = saveslots.store_bundle(PC, PLAT, GID, 2, ra_bundles(tmp, PC, STEM)[1])
    assert {a: Path(p).read_bytes() for p, a in store2.files} == {"game.state1": b"P2-STAN-1",
                                                                   "game.state2": b"P2-STAN-2-TEL"}


def test_interrupted_swap_finishes(net):
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"S1")
    sync(cfgs, tmp, PC)
    bundles = ra_bundles(tmp, PC, STEM)
    store2 = saveslots.store_bundle(PC, PLAT, GID, 2, bundles[1])
    _write(store2.folder / "game.state4", b"S2")
    # krok 1 zrobiony, program zamknięty przed podmianą plików
    sb1 = saveslots.store_bundle(PC, PLAT, GID, 1, bundles[1])
    _write(sb1.folder / "game.state1", b"S1")
    saveslots._put(PC, PLAT, GID, swap='{"from": 1, "to": 2}')
    assert saveslots.finish_swap(PC, PLAT, GID, bundles) == 2
    assert local_states(tmp, PC) == {"game.state4": b"S2"}
    assert {a for _p, a in saveslots.store_bundle(PC, PLAT, GID, 1, bundles[1]).files} == {"game.state1"}


def test_deleted_and_renumbered_states_do_not_come_back(net):
    """PC usuwa stan 1 i zmienia numer 2 → 5; telefon (bez połączenia) w tym czasie zapisał stan 3.
    Sumowanie: stan 3 dochodzi, usunięty i stary numer nie wracają."""
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"STARY", png=b"PNG1")
    put_state(tmp, PC, 2, b"DRUGI")
    sync(cfgs, tmp, PC)
    sync(cfgs, tmp, TEL)
    ed = editor(cfgs, tmp, PC)
    assert ed.delete("game.state1") == {"ok": True, "offline": ""}
    assert ed.renumber("game.state2", 5)["arc"] == "game.state5"
    assert local_states(tmp, PC) == {"game.state5": b"DRUGI"}
    assert [x["arc"] for x in ed.trash()] == ["game.state1"]
    srv = server_slot(1)
    assert srv["file_count"] == 1

    put_state(tmp, TEL, 3, b"TRZECI-TEL")            # gra offline: zmiana tu, serwer też się zmienił
    r = sync(cfgs, tmp, TEL)
    assert not r.conflicts
    assert local_states(tmp, TEL) == {"game.state5": b"DRUGI", "game.state3": b"TRZECI-TEL"}
    sync(cfgs, tmp, PC)
    assert local_states(tmp, PC) == {"game.state5": b"DRUGI", "game.state3": b"TRZECI-TEL"}
    # kosz dotarł na telefon — przywrócenie tam wraca na wszystkie urządzenia
    ed_tel = editor(cfgs, tmp, TEL)
    item = ed_tel.trash()[0]
    assert ed_tel.restore(item["name"])["arc"] == "game.state1"
    assert local_states(tmp, TEL)["game.state1.png"] == b"PNG1"
    sync(cfgs, tmp, PC)
    assert local_states(tmp, PC)["game.state1"] == b"STARY" and editor(cfgs, tmp, PC).trash() == []


def test_copy_move_and_continue(net):
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"JEDEN")
    put_state(tmp, PC, 2, b"DWA")
    put_state(tmp, PC, "auto", b"AUTO-STARY")
    sync(cfgs, tmp, PC)
    ed = editor(cfgs, tmp, PC)
    assert ed.copy_new("game.state1")["arc"] == "game.state3"
    assert ed.to_slot("game.state2", 3, move=True) == {"ok": True, "offline": "", "arc": "game.state1", "slot": 3}
    assert ed.to_slot("game.state1", 4)["slot"] == 4
    with pytest.raises(saveslots.EditError):
        ed.to_slot("game.state1", 1)                       # ten sam slot
    with pytest.raises(saveslots.EditError):
        ed.renumber("game.state1", 3)                      # numer zajęty
    assert ed.set_continue("game.state3")["arc"] == "game.state.auto"
    # poprzedni „Kontynuuj” nie zginął — dostał kolejny numer
    assert local_states(tmp, PC) == {"game.state1": b"JEDEN", "game.state3": b"JEDEN",
                                     "game.state4": b"AUTO-STARY", "game.state.auto": b"JEDEN"}
    assert server_slot(3)["file_count"] == 1 and server_slot(4)["file_count"] == 1
    # drugi PC: przeniesiony stan 2 nie wraca do slotu 1, jest w slocie 3
    sync(cfgs, tmp, TEL)
    assert "game.state2" not in local_states(tmp, TEL)
    s3 = saveslots.store_bundle(TEL, PLAT, GID, 3, ra_bundles(tmp, TEL, STEM)[1])
    assert {a: Path(p).read_bytes() for p, a in s3.files} == {"game.state1": b"DWA"}


def test_trash_keeps_last_ten(net, monkeypatch):
    cfgs, tmp = net
    for n in range(1, 13):
        put_state(tmp, PC, n, f"STAN-{n}".encode())
    sync(cfgs, tmp, PC)
    ed = editor(cfgs, tmp, PC)
    for n in range(1, 13):
        ed.delete(f"game.state{n}")
    assert len(ed.trash()) == saveslots.TRASH_KEEP == 10
    sync(cfgs, tmp, TEL)
    assert len(editor(cfgs, tmp, TEL).trash()) == 10


def test_edit_offline_is_sent_later(net, monkeypatch):
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"A")
    put_state(tmp, PC, 2, b"B")
    sync(cfgs, tmp, PC)
    real = syncclient._Client.json

    def down(self, *a, **k):
        raise syncclient.Offline("brak sieci")
    monkeypatch.setattr(syncclient._Client, "json", down)
    r = editor(cfgs, tmp, PC).delete("game.state1")
    assert r["offline"]
    monkeypatch.setattr(syncclient._Client, "json", real)
    assert syncclient.retry(cfgs[PC]) == 2                 # stany i kosz
    assert server_slot(1)["file_count"] == 1
    assert [x["name"] for x in server_slot(1)["removed"]] == ["game.state1"]


def test_old_server_without_slots(net, monkeypatch):
    """Serwer sprzed slotów: slot 1 jak dotąd; inny slot nie trafia na serwer (zapisałby się
    w slocie 1)."""
    cfgs, tmp = net
    orig = savesync.manifest

    def old(*a, **k):
        m = orig(*a, **k)
        m.pop("slots", None)
        m.pop("active_slot", None)
        return m
    monkeypatch.setattr(savesync, "manifest", old)
    put_state(tmp, PC, 1, b"S1")
    assert sync(cfgs, tmp, PC).uploaded == 1
    r = saveslots.set_active(cfgs[PC], PC, GAME, ra_bundles(tmp, PC, STEM), 2)
    assert r["sent"] is False
    put_state(tmp, PC, 1, b"SLOT2")
    r = sync(cfgs, tmp, PC)
    assert "bez slotów" in r.offline and r.uploaded == 0
    syncclient.after(cfgs[PC], PC, ra_bundles(tmp, PC, STEM), GAME)
    with savesync._lock_for("Jezus", PLAT, GID):
        m = savesync._load("Jezus", PLAT, GID)
    assert m["revision"] == 1 and "slots" not in m           # slot 1 nienaruszony


def test_guard_keeps_existing_numbers(tmp_path):
    """RetroArch po wczytaniu „Auto” zapisuje do slotu 0, potem 1 — istniejący stan 1 wraca,
    nowy zapis dostaje kolejny numer."""
    d = tmp_path / "states"
    _write(d / f"{STEM}.state1", b"STARY-1")
    _write(d / f"{STEM}.state1.png", b"PNG-STARY")
    _write(d / f"{STEM}.state3", b"TRZY")
    b = savebundles.Bundle("retroarch", "snes9x", "states", d, STEM, [STEM]).collect()
    g = saveslots.Guard([b], tmp_path / "run")
    _write(d / f"{STEM}.state", b"NOWY-0")                    # slot 0 — wolny, zostaje
    _write(d / f"{STEM}.state1", b"NOWY-1")                   # nadpisany
    _write(d / f"{STEM}.state1.png", b"PNG-NOWY")
    _write(d / f"{STEM}.state.auto", b"AUTO")                 # autozapis przy wyjściu — zawsze wolno
    assert g.finish() == [("game.state1", "game.state4")]
    got = {p.name.replace(STEM, "game"): p.read_bytes() for p in d.iterdir()}
    assert got == {"game.state": b"NOWY-0", "game.state1": b"STARY-1", "game.state1.png": b"PNG-STARY",
                   "game.state3": b"TRZY", "game.state4": b"NOWY-1", "game.state4.png": b"PNG-NOWY",
                   "game.state.auto": b"AUTO"}
    assert not (tmp_path / "run" / "_straz_stanow").exists()


def test_merge_states_removed_pairs():
    """Usunięcie dotyczy treści pod tą nazwą — kopia pod innym numerem zostaje."""
    mine = {"game.state5": ("X", 2.0)}
    other = {"game.state2": ("X", 1.0), "game.state2.png": ("P", 1.0), "game.state7": ("X", 1.0)}
    out, _moves = savebundles.merge_states(mine, other, {("game.state2", "X")})
    assert set(out) == {"game.state5", "game.state7"}


def test_retroarch_state_slot_not_kept(tmp_path):
    """Numer slotu z menu RetroArcha (np. „Auto” = −1) zostaje w sesji, nie w retroarch.cfg;
    log sesji RetroArcha do folderu sesji."""
    from emustart import ingame
    home = tmp_path / "RetroArch"
    _write(home / "retroarch.cfg", b'state_slot = "0"\nvideo_scale_integer = "false"\n')
    ra = ingame.RetroArch.__new__(ingame.RetroArch)
    ra.home, ra.keyboard_game, ra.cheevos = home, False, None
    run = tmp_path / "run"
    args = ra.launch_args(run, True, [])
    appended = Path(args[args.index("--appendconfig") + 1]).read_text(encoding="utf-8")
    assert 'log_to_file = "true"' in appended and str(run) in appended
    Path(args[1]).write_text('state_slot = "-1"\nvideo_scale_integer = "true"\n')
    (run / "retroarch.log").write_text("[INFO] [State] Loading state \"x.state.auto\", 100 bytes.\n[INFO] [Video] ok\n")
    assert ra.after_session() == 1
    cfg = (home / "retroarch.cfg").read_text()
    assert 'state_slot = "0"' in cfg and 'video_scale_integer = "true"' in cfg


def _slot_files(pid, tmp, who, slot):
    b = ra_bundles(tmp, who, STEM)[1]
    return {a: Path(p).read_bytes() for p, a in saveslots.store_bundle(pid, PLAT, GID, slot, b).files}


def test_swap_interrupted_in_step_3_loses_nothing(net):
    """Przerwanie w trakcie czyszczenia folderu nowego slotu (krok 3): emulator ma już komplet —
    dokończenie nie kasuje plików, których w folderze slotu już nie ma."""
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"S1-A")
    bundles = ra_bundles(tmp, PC, STEM)
    store2 = saveslots.store_bundle(PC, PLAT, GID, 2, bundles[1])
    _write(store2.folder / "game.state1", b"S2-A")
    _write(store2.folder / "game.state2", b"S2-B")
    real = saveslots._put

    def crash(pid, plat, gid, **kw):
        real(pid, plat, gid, **kw)
        if '"step": 3' in kw.get("swap", ""):
            (store2.folder / "game.state1").unlink()       # część kroku 3 zrobiona, potem awaria
            raise OSError("awaria w kroku 3")
    saveslots._put = crash
    try:
        with pytest.raises(OSError):
            saveslots.swap(PC, PLAT, GID, bundles, 2)
    finally:
        saveslots._put = real
    assert saveslots.finish_swap(PC, PLAT, GID, ra_bundles(tmp, PC, STEM)) == 2
    assert local_states(tmp, PC) == {"game.state1": b"S2-A", "game.state2": b"S2-B"}
    assert _slot_files(PC, tmp, PC, 1) == {"game.state1": b"S1-A"} and _slot_files(PC, tmp, PC, 2) == {}


def test_swap_interrupted_in_step_2_finishes(net):
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"S1-A")
    put_state(tmp, PC, 7, b"S1-B")
    bundles = ra_bundles(tmp, PC, STEM)
    store2 = saveslots.store_bundle(PC, PLAT, GID, 2, bundles[1])
    _write(store2.folder / "game.state1", b"S2-A")
    real = saveslots._copy
    n = {"i": 0}

    def flaky(src, dst):
        if "dev1" in str(dst) and n["i"] == 0:            # pierwsza kopia do emulatora (krok 2)
            n["i"] += 1
            real(src, dst)
            raise OSError("awaria w kroku 2")
        real(src, dst)
    saveslots._copy = flaky
    try:
        with pytest.raises(OSError):
            saveslots.swap(PC, PLAT, GID, bundles, 2)
    finally:
        saveslots._copy = real
    assert saveslots.here(PC, PLAT, GID) == 1
    assert _slot_files(PC, tmp, PC, 1) == {"game.state1": b"S1-A", "game.state7": b"S1-B"}   # krok 1 zrobiony
    assert saveslots.finish_swap(PC, PLAT, GID, ra_bundles(tmp, PC, STEM)) == 2
    assert local_states(tmp, PC) == {"game.state1": b"S2-A"}
    # i z powrotem — nic nie zginęło
    saveslots.swap(PC, PLAT, GID, ra_bundles(tmp, PC, STEM), 1)
    assert local_states(tmp, PC) == {"game.state1": b"S1-A", "game.state7": b"S1-B"}
    assert _slot_files(PC, tmp, PC, 2) == {"game.state1": b"S2-A"}


def test_swap_with_bad_copy_touches_nothing(net):
    """Kopia do folderu slotu niezgodna z oryginałem (np. pełny dysk) — zamiana wstrzymana,
    pliki emulatora i slot bez zmian."""
    cfgs, tmp = net
    put_state(tmp, PC, 1, b"S1-A")
    bundles = ra_bundles(tmp, PC, STEM)
    store2 = saveslots.store_bundle(PC, PLAT, GID, 2, bundles[1])
    _write(store2.folder / "game.state1", b"S2-A")
    real = saveslots._copy

    def bad(src, dst):
        real(src, dst)
        if "_sloty" in str(dst):
            Path(dst).write_bytes(b"uszkodzone")
    saveslots._copy = bad
    try:
        with pytest.raises(saveslots.EditError):
            saveslots.set_active(cfgs[PC], PC, GAME, bundles, 2)
    finally:
        saveslots._copy = real
    assert saveslots.here(PC, PLAT, GID) == 1 and not saveslots.info(PC, PLAT, GID)["swap"]
    assert local_states(tmp, PC) == {"game.state1": b"S1-A"}
    assert _slot_files(PC, tmp, PC, 2) == {"game.state1": b"S2-A"}
