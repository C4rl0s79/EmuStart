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


def test_launch_extracts_zip_for_non_native_emulator(env, server_files):
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


def test_launch_disc_downloaded_from_server(env, server_files):
    """Gra płytowa: pobranie z serwera do pamięci podręcznej i start z kopii lokalnej."""
    cfg, roms, tmp = env
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
    assert str(roms) not in str(got)                                 # z kopii lokalnej
    if s.copy_thread:
        s.copy_thread.join(10)
    assert cache.is_complete(cfg, library.game(1))


def test_offline_without_cache_is_clear_error(env, server_files):
    cfg, roms, tmp = env
    _write(roms / "nes" / "X.zip")
    scanner.scan_system("nes", roms / "nes")
    _fake_emu(cfg, "nes", tmp / "rec.txt")
    (roms / "nes" / "X.zip").unlink()                  # serwer nie ma gry / nie odpowiada
    s = _run_session(cfg, 1)
    assert s.phase == "error" and "Serwer EmuStart" in s.message


def test_build_command_placeholders():
    cmd = emulators.build_command("mame.exe", emulators.EMU_ARGS["mame"], r"D:\c\fbneo\10yard.zip")
    assert cmd == ["mame.exe", "-rompath", r"D:\c\fbneo", "-skip_gameinfo", "10yard"]
    cmd = emulators.build_command("ra.exe", r'-L "C:\x y\c.dll" -f', r"Z:\a b\g.zip")
    assert cmd == ["ra.exe", "-L", r"C:\x y\c.dll", "-f", r"Z:\a b\g.zip"]


def test_multiple_roots_dedupe_and_names(env):
    cfg, roms, tmp = env
    redump, nointro = tmp / "REDUMP", tmp / "No-Intro"
    _write(redump / "Sony - PlayStation" / "A (USA).chd")
    _write(nointro / "Sony - PlayStation" / "A (USA).chd")          # ta sama gra drugi raz
    _write(nointro / "Sony - PlayStation" / "B (USA).chd")
    _write(nointro / "Atari - Atari 7800 (BIN)" / "C (USA).zip")
    _write(nointro / "Apple - II (WOZ)" / "D.woz")                  # archiwalne — pomijane
    cfg["rom_roots"] = [str(redump), str(nointro)]
    res = scanner.scan_all(cfg)
    assert res["ok"] and res["games"] == 3
    rows = {r["name"]: dict(r) for r in library.db().execute("SELECT * FROM games")}
    assert rows["A (USA)"]["src"] == str(redump / "Sony - PlayStation")   # pierwszy folder wygrywa
    assert rows["B (USA)"]["src"] == str(nointro / "Sony - PlayStation")
    assert rows["C (USA)"]["es"] == "atari7800"
    assert library.game_dir(library.game(rows["B (USA)"]["id"])) == nointro / "Sony - PlayStation"


def test_fresh_database_opened_by_many_threads_at_once():
    """Pierwsze otwarcie bazy naraz w kilku wątkach (serwer, pobieranie, interfejs): migracja
    kolumn raz, jeden domyślny profil — wcześniej „duplicate column name” (CI wydania 0.43.0)."""
    import threading
    from emustart import arcade, profiles
    errs, start = [], threading.Barrier(8)

    def open_db():
        try:
            start.wait()
            library.db()
            profiles.all_profiles()
            arcade.needs_refresh()
        except Exception as ex:                      # noqa: BLE001 — każdy błąd to porażka testu
            errs.append(repr(ex))
        finally:
            library._local.con = None
    th = [threading.Thread(target=open_db) for _ in range(8)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert not errs, errs
    assert library.db().execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1


def test_fbneo_names_and_versions_without_mame(env, tmp_path):
    """Serwer bez MAME (samo FinalBurn Neo): nazwy i rodzice wersji z DAT FinalBurn Neo,
    lista pokazuje grę raz, inne wersje — w „Wersja gry” (wcześniej „10yardj” i wszystkie
    wersje osobno)."""
    from emustart import api as api_mod, arcade
    cfg, roms, tmp = env
    dat = tmp_path / "fbneo.dat"
    dat.write_text('<?xml version="1.0"?><datafile>'
                   '<game name="1941"><description>1941 - Counter Attack (World)</description><year>1990</year>'
                   '<manufacturer>Capcom</manufacturer></game>'
                   '<game name="1941j" cloneof="1941" romof="1941"><description>1941 - Counter Attack (Japan)</description></game>'
                   '<game name="neogeo" isbios="yes"><description>Neo-Geo BIOS</description></game>'
                   '</datafile>', encoding="utf-8")
    from emustart import art, paths
    for n in ("1941", "1941j", "neogeo"):
        _write(roms / "fbneo" / f"{n}.zip", b"zip")
    scanner.scan_system("fbneo", roms / "fbneo")                    # bez indeksu: nazwy setów
    from emustart import mediastore
    mediastore.put("fbneo", art.media_file("1941", "box"), b"png")   # grafika pod nazwą setu
    assert arcade.refresh_fbneo(force=True, url=dat.as_uri())["sets"] == 3
    assert arcade.refresh_fbneo()["cached"]                          # raz na 30 dni
    scanner.scan_system("fbneo", roms / "fbneo")
    # nowa nazwa gry — grafika przeniesiona za nią (0.44.4: grafiki znikały)
    assert art.has("fbneo", "1941 - Counter Attack (World)", "box")
    assert not art.has("fbneo", "1941", "box")
    mediastore.put("fbneo", art.media_file("1941j", "snap"), b"png")   # naprawa po fakcie
    assert art.adopt_set_names("fbneo") == 1
    assert art.has("fbneo", "1941 - Counter Attack (Japan)", "snap")
    games = library.list_games("fbneo", library.default_profile(), True)
    assert [g["title"] for g in games] == ["1941 - Counter Attack"]  # bez wersji i BIOS-u
    assert arcade.dependencies("1941j") == ["1941"]                  # wersja potrzebuje rodzica
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg = cfg
    v = a._variants(library.game(games[0]["id"]))
    assert len(v) == 1 and v[0]["rel"].endswith("1941j.zip") and "Japan" in v[0]["label"]


def test_window_mode_fetches_missing_media_from_server(env, tmp_path, monkeypatch):
    """Zwykłe okno (bez --browser): brakująca grafika (np. logo systemu wybrane na serwerze)
    pobierana z serwera EmuStart — wcześniej tylko w trybie przeglądarki."""
    import urllib.request
    from emustart import libsync, server
    monkeypatch.setattr(server, "ROUTES", {"/media/": tmp_path / "media", "/assets/": paths.ASSETS})
    monkeypatch.setattr(server, "DEV_API", None)
    asked = []

    def fake(cfg, rel, target):
        asked.append((cfg.get("x"), rel))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"svg")
        return True
    monkeypatch.setattr(libsync, "fetch_media", fake)
    monkeypatch.setattr(server, "MEDIA_CFG", lambda: {"x": 1})
    base = server.start(0)
    body = urllib.request.urlopen(base + "/media/_systems/amiga.custom.svg?v=5", timeout=10).read()
    assert body == b"svg" and asked == [(1, "_systems/amiga.custom.svg")]
