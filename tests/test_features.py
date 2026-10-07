"""Profile (junction + NAS), kolejność padów w ini emulatorów, baza .rdb, stany gry."""

from __future__ import annotations

import os
import socket
import struct
from pathlib import Path

import pytest

from emustart import ingame, library, metadata, pads, profiles

from test_core import _write, env  # noqa: F401  (fixture)


# ── profile ──

@pytest.fixture()
def prof_env(env, monkeypatch):
    cfg, roms, tmp = env
    monkeypatch.setattr(profiles, "LOCAL", tmp / "profiles")
    cfg["profiles_nas"] = str(tmp / "nas")
    return cfg, tmp


def test_attach_migrates_to_first_profile_and_switches(prof_env):
    cfg, tmp = prof_env
    emu = tmp / "emu" / "memcards"
    _write(emu / "shared.mcd", b"OLD")
    first = profiles.first_id()
    second = profiles.create("Ola")["id"]

    profiles.attach(second, "duckstation", emu)          # gra Ola — save'y zostają u pierwszego
    assert os.path.isjunction(emu)
    assert not (emu / "shared.mcd").exists()
    assert (profiles.local_dir(first, "duckstation", "memcards") / "shared.mcd").read_bytes() == b"OLD"
    _write(emu / "ola.mcd", b"OLA")                       # zapis Oli trafia do jej folderu

    profiles.attach(first, "duckstation", emu)           # wraca pierwszy profil
    assert (emu / "shared.mcd").read_bytes() == b"OLD" and not (emu / "ola.mcd").exists()
    assert (profiles.local_dir(second, "duckstation", "memcards") / "ola.mcd").exists()


def test_sync_up_down_newer_wins(prof_env):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    local = profiles.local_dir(pid, "pcsx2", "memcards")
    _write(local / "a.ps2", b"v1")
    assert profiles.sync_up(cfg, pid, "pcsx2", ["memcards"]) == 1
    nas = Path(cfg["profiles_nas"]) / "Gracz" / "save" / "pcsx2" / "memcards" / "a.ps2"
    assert nas.read_bytes() == b"v1"
    # inny komputer zapisał nowszą wersję na NAS
    nas.write_bytes(b"v2-newer")
    os.utime(nas, (nas.stat().st_mtime + 100,) * 2)
    assert profiles.sync_down(cfg, pid, "pcsx2", ["memcards"]) == 1
    assert (local / "a.ps2").read_bytes() == b"v2-newer"
    assert list((profiles.LOCAL / str(pid) / "_backup").rglob("a.ps2"))   # stara wersja w kopii


def test_lock_blocks_other_host(prof_env):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    assert profiles.lock(cfg, pid) == ""
    lock = Path(cfg["profiles_nas"]) / "Gracz" / "lock"
    lock.write_text('{"host": "INNY-PC", "time": %d}' % int(__import__("time").time()))
    assert profiles.lock(cfg, pid) == "INNY-PC"
    lock.write_text('{"host": "%s", "time": 0}' % socket.gethostname())
    assert profiles.lock(cfg, pid) == ""
    profiles.unlock(cfg, pid)
    assert not lock.exists()


# ── pady ──

def test_pad_remap_and_restore_keeps_other_changes(env, tmp_path):
    ini = tmp_path / "settings.ini"
    ini.write_text("[Main]\nX = 1\n[Pad1]\nCross = SDL-0/A\nUp = SDL-0/DPadUp\n"
                   "[Pad2]\nCross = SDL-1/A\n", encoding="utf-8")
    restore = ingame._remap_sdl_ini(ini, "Pad{}", [1, 0])
    text = ini.read_text()
    assert "Cross = SDL-1/A\nUp = SDL-1/DPadUp" in text and "[Pad2]\nCross = SDL-0/A" in text
    # emulator w trakcie gry zmienia inne ustawienie — przywrócenie go nie cofa
    ini.write_text(ini.read_text().replace("X = 1", "X = 2"), encoding="utf-8")
    restore()
    text = ini.read_text()
    assert "X = 2" in text and "Cross = SDL-0/A\nUp = SDL-0/DPadUp" in text
    assert "[Pad2]\nCross = SDL-1/A" in text


def test_pad_restore_after_crash(env, tmp_path):
    ini = tmp_path / "PCSX2.ini"
    ini.write_text("[Pad1]\nCross = SDL-0/A\n", encoding="utf-8")
    ingame._remap_sdl_ini(ini, "Pad{}", [2])
    assert "SDL-2/A" in ini.read_text()
    ingame.restore_pending()                              # start po awarii
    assert "SDL-0/A" in ini.read_text()


def test_pad_order_modes():
    lst = [{"slot": 0, "wireless": False}, {"slot": 1, "wireless": True}]
    assert pads.order({}, lst) == [0, 1]
    assert pads.order({"pad_order": {"mode": "wireless_first"}}, lst) == [1, 0]
    assert pads.order({"pad_order": {"mode": "manual", "manual": [1, 3]}}, lst) == [1, 0]


def test_retroarch_pad_indexes(tmp_path):
    a = ingame.adapter_for(str(tmp_path / "retroarch.exe"))
    args = a.launch_args(tmp_path / "run", False, [1, 0])
    text = Path(args[1]).read_text()
    assert 'input_player1_joypad_index = "1"' in text and 'input_player2_joypad_index = "0"' in text


# ── metadane ──

def _mp_str(s: str) -> bytes:
    b = s.encode()
    return bytes([0xA0 | len(b)]) + b


def test_rdb_parser(tmp_path):
    rec = {"name": "Game (USA)", "developer": "Dev", "publisher": "Pub", "releaseyear": 1999}
    body = bytes([0x80 | len(rec)])
    for k, v in rec.items():
        body += _mp_str(k) + (_mp_str(v) if isinstance(v, str) else b"\xcd" + struct.pack(">H", v))
    f = tmp_path / "x.rdb"
    f.write_bytes(b"RARCHDB\0" + struct.pack(">Q", 0) + body + b"\xc0")
    rows = metadata.parse_rdb(f)
    assert rows == [{"name": "Game (USA)", "developer": "Dev", "publisher": "Pub", "releaseyear": 1999}]
    assert metadata.from_rdb(rows[0])["year"] == "1999"


def test_user_edits_win(env):
    cfg, roms, tmp = env
    _write(roms / "snes" / "G (USA).zip")
    from emustart import scanner
    scanner.scan_system("snes", roms / "snes")
    metadata._store(1, {"developer": "Auto", "year": "1990"})
    metadata.set_edits(1, {"developer": "Mój", "year": ""})
    m = metadata.get(1)
    assert m["developer"] == "Mój" and m["year"] == "1990"
    metadata._store(1, {"developer": "Auto2"})            # ponowne pobranie nie nadpisuje
    assert metadata.get(1)["developer"] == "Mój"


def test_duckstation_states_by_serial(tmp_path):
    (tmp_path / "settings.ini").write_text("")
    _write(tmp_path / "savestates" / "SLUS-01013_1.sav")
    _write(tmp_path / "savestates" / "SLUS-01013_resume.sav")
    _write(tmp_path / "savestates" / "SLUS-99999_1.sav")
    a = ingame.adapter_for(str(tmp_path / "duckstation-qt.exe"))
    st = ingame.list_states(a, {"rel": "x.chd", "name": "x"}, {"serial": "SLUS-01013CE"}, [])
    assert sorted(s["name"] for s in st) == ["SLUS-01013_1.sav", "SLUS-01013_resume.sav"]


def test_moved_app_adopts_old_profile_store(prof_env, monkeypatch):
    cfg, tmp = prof_env
    emu = tmp / "emu" / "memcards"
    _write(emu / "a.mcd", b"A")
    pid = profiles.first_id()
    profiles.attach(pid, "duckstation", emu)
    monkeypatch.setattr(profiles, "LOCAL", tmp / "nowy" / "profiles")   # EmuStart w innym folderze
    profiles.attach(pid, "duckstation", emu)
    assert (emu / "a.mcd").read_bytes() == b"A"
    assert (tmp / "nowy" / "profiles" / str(pid) / "duckstation" / "memcards" / "a.mcd").exists()


def test_uipad_events_repeat_and_release(monkeypatch):
    from emustart import uipad
    seq = [{"down"}, {"down"}, {"down"}, set(), {"a"}, set()]
    monkeypatch.setattr(uipad, "_actions", lambda: seq.pop(0))
    monkeypatch.setattr(uipad.xinput, "available", lambda: False)   # bez wątku
    p = uipad.UiPad(lambda: True)
    for t in (0.0, 0.1, 0.5, 0.6, 0.7, 0.8):
        p._tick(t)
    assert p.poll() == [{"a": "down", "up": False}, {"a": "down", "up": False},
                        {"a": "down", "up": True}, {"a": "a", "up": False}, {"a": "a", "up": True}]
