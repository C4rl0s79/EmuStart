"""0.25.12: stara mapa bloków po usunięciu gry, dokończenie pliku mimo błędu
strumienia, ROM w archiwum MSU-1 bez listy rozszerzeń, usuwanie lokalnej kopii,
panel serwera."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import pytest

from emustart import cache, launcher, library, partial, paths, scanner, server, srvadmin, systems


def _write(p: Path, data: bytes = b"x" * 1000) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "data" / "lib.sqlite")
    monkeypatch.setattr(paths, "RUN_TMP", tmp_path / "run")
    library._local.con = None
    roms = tmp_path / "roms"
    cfg = {"rom_root": str(roms), "cache_dir": str(tmp_path / "cache"), "cache_recent": 2,
           "systems": {}}
    yield cfg, roms, tmp_path
    library._local.con = None


def test_stale_map_without_part_counts_whole_file(env, monkeypatch):
    """Mapa bloków została po usunięciu gry — postęp nie może liczyć z niej
    „prawie wszystko pobrane” (było 40272 %)."""
    cfg, roms, _ = env
    monkeypatch.setattr(partial, "BLOCK", 1024)
    monkeypatch.setattr(partial, "MIN_SIZE", 1024)
    data = bytes(range(256)) * 64
    _write(roms / "SNESMSU1" / "G (USA) (MSU1).zip", data)
    scanner.scan_system("SNESMSU1", roms / "SNESMSU1")
    g = library.game(1)
    dst = Path(cfg["cache_dir"]) / "SNESMSU1" / g["rel"]
    _write(Path(str(dst) + ".part.map"), b"\1" * (len(data) // 1024 - 1) + b"\0")
    assert cache.missing_bytes(cfg, g) == len(data)
    prog = cache.Progress(cache.missing_bytes(cfg, g), 1)
    cache.copy_game(cfg, g, roms / "SNESMSU1", prog, threading.Event())
    assert dst.read_bytes() == data and cache.is_complete(cfg, g)
    assert prog.snapshot()["done"] <= prog.snapshot()["total"]


def test_remove_deletes_part_and_map(env, monkeypatch):
    cfg, roms, _ = env
    _write(roms / "snes" / "G.zip")
    scanner.scan_system("snes", roms / "snes")
    g = library.game(1)
    base = Path(cfg["cache_dir"]) / "snes"
    for n in ("G.zip.part", "G.zip.part.map"):
        _write(base / n)
    cache.remove(cfg, g)
    assert not (base / "G.zip.part").exists() and not (base / "G.zip.part.map").exists()


def test_progress_never_over_100():
    p = cache.Progress(100, 1)
    p.add(5000)
    s = p.snapshot()
    assert s["total"] == 5000 and s["left"] == 0


def test_partial_finishes_despite_stream_error(tmp_path, monkeypatch):
    """Jeden strumień padł, ale reszta pobrała wszystko — plik ma być gotowy, nie .part."""
    monkeypatch.setattr(partial, "BLOCK", 1024)
    monkeypatch.setattr(partial, "RETRY_WAIT", 0.01)
    data = bytes(range(256)) * 40
    src = _write(tmp_path / "s.bin", data)
    dst = tmp_path / "c" / "d.bin"
    p = partial.Partial(src, dst, len(data), 1.0)
    orig = p._fetch
    calls = {"n": 0}

    def flaky(i, f, count=1):
        calls["n"] += 1
        if calls["n"] in (2, 3):
            raise OSError("chwilowy błąd sieci")
        return orig(i, f, count)
    p._fetch = flaky
    p.run(cache.Progress(len(data), 1), threading.Event(), streams=2)
    assert dst.read_bytes() == data and not p.part.exists() and not p.mapf.exists()


def test_partial_error_after_all_blocks_still_finalizes(tmp_path, monkeypatch):
    monkeypatch.setattr(partial, "BLOCK", 1024)
    data = bytes(range(256)) * 8
    src = _write(tmp_path / "s.bin", data)
    dst = tmp_path / "c" / "d.bin"
    p = partial.Partial(src, dst, len(data), 1.0)
    p.run()
    assert dst.exists()
    # gotowy .part z pełną mapą i błędem strumienia z poprzedniego uruchomienia
    dst.rename(p.part)
    _write(p.mapf, b"\1" * p.blocks)
    p2 = partial.Partial(src, dst, len(data), 1.0)
    p2.error = OSError("stary błąd")
    p2.run()
    assert dst.read_bytes() == data


def test_msu1_rom_found_without_system_exts(env):
    """Stary serwer nie podaje rozszerzeń systemu — ROM .sfc i tak jest wybrany,
    nie .msu/.pcm/.bps."""
    names = ["G-1.pcm", "G-2.pcm", "G.msu", "G.bps", "G.sfc", "manifest.bml"]
    assert launcher.pick_rom(names, set()) == ["G.sfc"]
    assert launcher.pick_rom(names, {"sfc", "smc"}) == ["G.sfc"]


def test_msu1_zip_extract_picks_sfc(env):
    cfg, roms, tmp = env
    z = roms / "SNESMSU1" / "Chrono Trigger (USA) (MSU1).zip"
    z.parent.mkdir(parents=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Chrono_Trigger_(USA)_(MSU1)-1.pcm", b"p" * 5000)
        zf.writestr("Chrono_Trigger_(USA)_(MSU1).msu", b"m" * 9000)
        zf.writestr("Chrono_Trigger_(USA)_(MSU1).bps", b"b")
        zf.writestr("Chrono_Trigger_(USA)_(MSU1).sfc", b"s" * 100)
        zf.writestr("manifest.bml", b"x")
    s = launcher.Session.__new__(launcher.Session)
    s.run_dir = tmp / "run" / "1"
    rom = s._extract(z, {"exts": ""})          # nieznane rozszerzenia systemu
    assert rom.name == "Chrono_Trigger_(USA)_(MSU1).sfc"
    rom = s._extract(z, systems.info("snesmsu1"))
    assert rom.suffix == ".sfc"


def test_system_lookup_ignores_case():
    assert systems.info("snesmsu1")["exts"] == systems.info("SNESMSU1")["exts"] != ""
    assert systems.info("PSX")["display"] == "PlayStation"


def test_android_prepare_has_companion_fallback():
    kt = (Path(__file__).parents[1] / "android/app/src/main/java/io/github/c4rl0s79/emustart/Prepare.kt").read_text(encoding="utf-8")
    assert "COMPANION_EXTS" in kt
    for ext in sorted(launcher.COMPANION_EXTS):
        assert f'"{ext}"' in kt, ext


def test_api_remove_local(env, monkeypatch):
    from emustart import api as api_mod
    cfg, roms, _ = env
    _write(roms / "snes" / "G.zip", b"z" * 3000)
    scanner.scan_system("snes", roms / "snes")
    g = library.game(1)
    cache.copy_game(cfg, g, roms / "snes", cache.Progress(3000, 1), threading.Event())
    cache.set_pinned(1, True)
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._session, a._pin_jobs, a._background = cfg, None, {}, []
    r = a.remove_local(1)
    assert r["ok"] and r["freed"] == 3000
    assert not cache.is_complete(cfg, g)
    row = library.db().execute("SELECT pinned, complete FROM cache WHERE game_id=1").fetchone()
    assert row["pinned"] == 0 and row["complete"] == 0


def test_web_uses_remove_local_on_both_platforms():
    root = Path(__file__).parents[1]
    assert "remove_local" in (root / "web/options.js").read_text(encoding="utf-8")
    assert '"remove_local"' in (root / "android/app/src/main/java/io/github/c4rl0s79/emustart/Bridge.kt").read_text(encoding="utf-8")


def test_admin_panel_endpoints_local_only(env, monkeypatch):
    cfg, roms, _ = env

    class Api:
        _cfg = dict(cfg, server_token="k")
        _scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}

        def rescan(self):
            self._scan["running"] = True
            return {"ok": True}
    monkeypatch.setattr(srvadmin, "ENABLED", True)
    base = server.start(0, dev_api=Api())
    port = base.rsplit(":", 1)[1]

    def call(path, host, hdr=None):
        req = urllib.request.Request(base + path, data=b"", headers={"Host": host, **(hdr or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as ex:
            return ex.code, None
    local = f"127.0.0.1:{port}"
    assert call("/admin/status", local)[0] == 403                          # bez nagłówka
    assert call("/admin/status", f"100.1.2.3:{port}", {"X-EmuStart": "1"})[0] == 403
    code, st = call("/admin/status", local, {"X-EmuStart": "1"})
    assert code == 200 and st["port"] and "clients" in st and "version" in st
    srvadmin.seen("100.64.0.7")
    srvadmin.sent("100.64.0.7", 1, "Chrono Trigger", 1 << 20)
    st = call("/admin/status", local, {"X-EmuStart": "1"})[1]
    assert st["transfers"][0]["title"] == "Chrono Trigger" and st["clients"][0]["ip"] == "100.64.0.7"
    assert call("/admin/rescan", local, {"X-EmuStart": "1"})[1]["ok"]
    assert call("/admin/key", local, {"X-EmuStart": "1"})[0] == 404        # 0.39: wspólnego klucza nie wydaje
    assert call("/admin/nope", local, {"X-EmuStart": "1"})[0] == 404
    with urllib.request.urlopen(urllib.request.Request(base + "/server.html", headers={"Host": local}), timeout=5) as r:
        assert b"/admin/" not in r.read() and r.status == 200          # strona panelu; skrypt osobno (CSP)
    with urllib.request.urlopen(urllib.request.Request(base + "/server.js", headers={"Host": local}), timeout=5) as r:
        assert b"/admin/" in r.read()
    server.DEV_API = None


def test_retroarch_menu_changes_survive_session(tmp_path):
    """Zmiana w menu RetroArcha w trakcie gry (np. proporcje obrazu) zostaje
    w retroarch.cfg; ustawienia sesji EmuStart (komendy, foldery, konto RA) — nie."""
    from emustart import ingame
    home = tmp_path / "RetroArch"
    _write(home / "retroarch.cfg", b'video_aspect_ratio_index = "22"\nvideo_scale_integer = "false"\n'
                                   b'savefile_directory = ":\\\\saves"\nnetwork_cmd_enable = "false"\n')
    ra = ingame.RetroArch.__new__(ingame.RetroArch)
    ra.home, ra.keyboard_game = home, False
    ra.cheevos = {"user": "u", "token": "SEKRET"}
    run = tmp_path / "run"
    args = ra.launch_args(run, False, [1, 0])
    assert args[0] == "--config" and "--appendconfig" in args
    sess = Path(args[1])
    assert 'config_save_on_exit = "true"' in sess.read_text()
    # RetroArch przy wyjściu zapisuje całość (z wartościami sesji) do kopii
    sess.write_text('video_aspect_ratio_index = "1"\nvideo_scale_integer = "true"\n'
                    'savefile_directory = "C:\\\\profil\\\\saves"\nnetwork_cmd_enable = "true"\n'
                    'cheevos_token = "SEKRET"\ninput_player1_joypad_index = "1"\n'
                    'config_save_on_exit = "true"\nvideo_shader_enable = "true"\n')
    assert ra.after_session() == 3
    cfg = (home / "retroarch.cfg").read_text()
    assert 'video_aspect_ratio_index = "1"' in cfg and 'video_scale_integer = "true"' in cfg
    assert 'video_shader_enable = "true"' in cfg
    assert 'network_cmd_enable = "false"' in cfg and "SEKRET" not in cfg and "profil" not in cfg
    assert "joypad_index" not in cfg and "config_save_on_exit" not in cfg
    assert ra.after_session() == 0                     # jednorazowo


def test_retroarch_config_mode_opens_menu(tmp_path):
    from emustart import ingame
    home = tmp_path / "RetroArch"
    _write(home / "retroarch.cfg", b'video_fullscreen = "true"\n')
    ra = ingame.RetroArch.__new__(ingame.RetroArch)
    ra.home, ra.keyboard_game, ra.cheevos = home, False, None
    args = ra.config_args(tmp_path / "run")
    assert "--menu" in args and args[0] == "--config"
    Path(args[1]).write_text('video_fullscreen = "false"\nconfig_save_on_exit = "true"\n')
    assert ra.after_session() == 1
    assert 'video_fullscreen = "false"' in (home / "retroarch.cfg").read_text()


def test_settings_on_nas_separate_for_windows(tmp_path, monkeypatch):
    """Ustawienia z Windows w settings/windows/<emulator>; stare settings/<emulator> przeniesione."""
    from emustart import nasfs, profiles
    nas = tmp_path / "nas"
    _write(nas / "Jezus" / "settings" / "retroarch" / "retroarch.cfg", b'video_smooth = "true"\n')
    fs = nasfs.DirFs(nas)
    assert profiles._migrate_settings(fs, {"nas_name": "Jezus"}, "retroarch") >= 1
    assert (nas / "Jezus" / "settings" / "windows" / "retroarch" / "retroarch.cfg").read_bytes() == b'video_smooth = "true"\n'
    assert profiles._migrate_settings(fs, {"nas_name": "Jezus"}, "retroarch") == 0   # już jest
