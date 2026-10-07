"""Testy rdzenia: skan, cache (wznawianie, limit, przypięte), uruchamianie
z atrapą emulatora (python -c …) w obu trybach sieci."""

from __future__ import annotations

import json
import sys
import threading
import time
import zipfile
from pathlib import Path

import pytest

from emustart import cache, emulators, launcher, library, paths, scanner


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "data" / "lib.sqlite")
    monkeypatch.setattr(paths, "RUN_TMP", tmp_path / "run")
    library._local.con = None
    roms = tmp_path / "roms"
    cfg = {"rom_root": str(roms), "cache_dir": str(tmp_path / "cache"), "cache_recent": 2,
           "network_mode": "auto", "lan_threshold_mbps": 200, "small_game_mb": 1,
           "systems": {}}
    yield cfg, roms, tmp_path
    library._local.con = None


def _write(p: Path, data: bytes = b"x" * 1000) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _fake_emu(cfg, es, record: Path, native_zip=False):
    """Atrapa emulatora: zapisuje ścieżkę gry, którą dostała, do pliku."""
    code = f"import sys,pathlib;pathlib.Path(r'{record}').write_text(sys.argv[-1])"
    cfg["systems"][es] = {"exe": sys.executable, "args": f'-c "{code}"', "label": "fake"}


def test_scan_groups_discs(env):
    cfg, roms, _ = env
    psx = roms / "psx"
    _write(psx / "Game A (USA) (Disc 1).chd")
    _write(psx / "Game A (USA) (Disc 2).chd")
    _write(psx / "Single (Europe).chd")
    _write(psx / "Cue Game (USA).cue", b'FILE "Cue Game (USA) (Track 1).bin" BINARY\n')
    _write(psx / "Cue Game (USA) (Track 1).bin")
    _write(psx / "M (USA) (Disc 1).chd")
    _write(psx / "M (USA) (Disc 2).chd")
    _write(psx / "M (USA).m3u", b"M (USA) (Disc 1).chd\nM (USA) (Disc 2).chd\n")
    _write(psx / "readme.txt")
    assert scanner.scan_system("psx", psx) == 4
    rows = {r["title"]: dict(r) for r in library.db().execute("SELECT * FROM games")}
    assert rows["Game A"]["multidisc"] == 1 and len(json.loads(rows["Game A"]["files"])) == 2
    assert rows["Game A"]["name"] == "Game A (USA)"
    assert len(json.loads(rows["Cue Game"]["files"])) == 2
    assert rows["M"]["rel"] == "M (USA).m3u" and len(json.loads(rows["M"]["files"])) == 3
    # zniknięcie gry z NAS-a usuwa ją z bazy
    (psx / "Single (Europe).chd").unlink()
    assert scanner.scan_system("psx", psx) == 3


def test_copy_resumes_part(env):
    cfg, roms, tmp = env
    data = bytes(range(256)) * 4096
    _write(roms / "snes" / "G (USA).zip", data)
    scanner.scan_system("snes", roms / "snes")
    g = library.game(1)
    part = Path(cfg["cache_dir"]) / "snes" / "G (USA).zip.part"
    _write(part, data[:300_000])                        # przerwane wcześniej pobieranie
    assert cache.missing_bytes(cfg, g) == len(data) - 300_000
    prog = cache.Progress(cache.missing_bytes(cfg, g), 1)
    cache.copy_game(cfg, g, roms / "snes", prog, threading.Event())
    assert (Path(cfg["cache_dir"]) / "snes" / "G (USA).zip").read_bytes() == data
    assert prog.done == len(data) - 300_000
    assert cache.is_complete(cfg, g)


def test_limit_keeps_pinned(env):
    cfg, roms, _ = env
    for i in range(4):
        _write(roms / "gb" / f"G{i}.zip")
    scanner.scan_system("gb", roms / "gb")
    for gid in range(1, 5):
        g = library.game(gid)
        cache.copy_game(cfg, g, roms / "gb", cache.Progress(1000, 1), threading.Event())
        cache.touch(gid)
        time.sleep(0.01)
    cache.set_pinned(1, True)
    cache.enforce_limit(cfg)
    left = {gid for gid in range(1, 5) if cache.is_complete(cfg, library.game(gid))}
    assert left == {1, 3, 4}         # przypięta + 2 ostatnie


def _run_session(cfg, gid):
    s = launcher.Session(cfg, gid, library.default_profile())
    s.start()
    s.thread.join(30)
    return s


def test_launch_extracts_zip_for_non_native_emulator(env):
    cfg, roms, tmp = env
    z = roms / "gba" / "Hero (USA).zip"
    z.parent.mkdir(parents=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Hero (USA).gba", b"ROM" * 100)
        zf.writestr("readme.txt", b"hi")
    scanner.scan_system("gba", roms / "gba")
    rec = tmp / "rec.txt"
    _fake_emu(cfg, "gba", rec)
    s = _run_session(cfg, 1)
    assert s.phase == "finished", s.message
    got = Path(rec.read_text())
    assert got.name == "Hero (USA).gba" and str(paths.RUN_TMP) in str(got)
    assert cache.is_complete(cfg, library.game(1))
    assert not any(paths.RUN_TMP.iterdir())            # posprzątane


@pytest.mark.parametrize("mode,expect_nas", [("lan", True), ("remote", False)])
def test_launch_disc_network_modes(env, mode, expect_nas):
    cfg, roms, tmp = env
    cfg["network_mode"] = mode
    _write(roms / "psx" / "Big (USA) (Disc 1).chd", b"d" * 3_000_000)
    _write(roms / "psx" / "Big (USA) (Disc 2).chd", b"e" * 3_000_000)
    scanner.scan_system("psx", roms / "psx")
    rec = tmp / "rec.txt"
    _fake_emu(cfg, "psx", rec)
    cfg["systems"]["psx"]["exe"] = sys.executable   # python.exe nie czyta m3u → płyta 1
    s = _run_session(cfg, 1)
    assert s.phase == "finished", s.message
    got = Path(rec.read_text())
    assert got.name == "Big (USA) (Disc 1).chd"
    assert (str(roms) in str(got)) == expect_nas
    if s.copy_thread:
        s.copy_thread.join(10)
    assert cache.is_complete(cfg, library.game(1))


def test_offline_without_cache_is_clear_error(env):
    cfg, roms, tmp = env
    _write(roms / "nes" / "X.zip")
    scanner.scan_system("nes", roms / "nes")
    _fake_emu(cfg, "nes", tmp / "rec.txt")
    (roms / "nes" / "X.zip").unlink()                  # „NAS niedostępny”
    s = _run_session(cfg, 1)
    assert s.phase == "error" and "NAS" in s.message


def test_build_command_placeholders():
    cmd = emulators.build_command("mame.exe", emulators.EMU_ARGS["mame"], r"D:\c\fbneo\10yard.zip")
    assert cmd == ["mame.exe", "-rompath", r"D:\c\fbneo", "-skip_gameinfo", "10yard"]
    cmd = emulators.build_command("ra.exe", r'-L "C:\x y\c.dll" -f', r"Z:\a b\g.zip")
    assert cmd == ["ra.exe", "-L", r"C:\x y\c.dll", "-f", r"Z:\a b\g.zip"]
