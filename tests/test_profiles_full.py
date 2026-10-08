"""Profile per gracz: save'y po czystej instalacji, ustawienia emulatorów i EmuStart,
RetroAchievements (RetroArch, DuckStation, PCSX2), ochrona dysku przy grafikach."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from emustart import art, dstoken, ingame, launcher, paths, profiles

from test_core import _write, env  # noqa: F401  (fixture)


@pytest.fixture()
def prof_env(env, monkeypatch):
    cfg, roms, tmp = env
    monkeypatch.setattr(profiles, "LOCAL", tmp / "profiles")
    cfg["profiles_nas"] = str(tmp / "nas")
    return cfg, tmp


def _session(cfg, pid):
    s = launcher.Session.__new__(launcher.Session)
    s.cfg, s.profile_id, s.profiles_on = cfg, pid, True
    return s


def _duck(tmp: Path) -> ingame.DuckStation:
    home = tmp / "emu" / "DuckStation"
    _write(home / "portable.txt", b"")
    _write(home / "settings.ini", (
        "[Main]\nSettingsVersion = 3\n\n[BIOS]\nSearchDirectory = bios\n\n[GPU]\nRenderer = Vulkan\nAdapter = GPU-A\n\n"
        "[Cheevos]\nEnabled = false\n\n[MemoryCards]\nDirectory = memcards\n\n[Folders]\nSaveStates = savestates\n").encode())
    return ingame.DuckStation(str(home / "duckstation-qt-x64-ReleaseLTCG.exe"))


def _pcsx2(tmp: Path) -> ingame.PCSX2:
    home = tmp / "emu" / "PCSX2"
    _write(home / "portable.ini", b"")
    _write(home / "inis" / "PCSX2.ini", (
        "[Folders]\nBios = bios\nSavestates = sstates\nMemoryCards = memcards\n\n"
        "[EmuCore/GS]\nAdapter = GPU-A\nupscale_multiplier = 1\n\n[MemoryCards]\nSlot1_Filename = Mcd001.ps2\n").encode())
    return ingame.PCSX2(str(home / "pcsx2-qt.exe"))


def test_ini_set_edits_adds_and_creates(tmp_path):
    f = tmp_path / "a.ini"
    _write(f, b"[A]\nx = 1\n\n[B]\ny = 2\n")
    ingame.ini_set(f, {("A", "x"): "9", ("A", "new"): "n", ("C", "z"): "3"})
    assert ingame.ini_section(f, "A") == {"x": "9", "new": "n"}
    assert ingame.ini_section(f, "B") == {"y": "2"}
    assert ingame.ini_section(f, "C") == {"z": "3"}
    g = tmp_path / "nowy" / "secrets.ini"
    ingame.ini_set(g, {("Achievements", "Token"): "T"})
    assert ingame.ini_section(g, "Achievements") == {"Token": "T"}


def test_clean_reinstall_restores_saves_from_nas(prof_env):
    """Emulator zainstalowany od nowa (pusta karta pamięci założona przez emulator),
    lokalnego magazynu profili brak — save wraca z NAS przy pierwszej grze."""
    cfg, tmp = prof_env
    pid = profiles.first_id()
    nas_card = Path(cfg["profiles_nas"]) / "Gracz" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2"
    _write(nas_card, b"MOJE-ZAPISY")
    ad = _pcsx2(tmp)
    blank = ad.settings_base() / "memcards" / "Mcd001.ps2"
    _write(blank, b"PUSTA")
    os.utime(blank, (1, 1))                                  # świeża karta starsza niż save z NAS

    s = _session(cfg, pid)
    names = s._profile_prepare(ad)
    assert "memcards" in names
    mc = ad.settings_base() / "memcards"
    assert os.path.isjunction(mc)
    assert (mc / "Mcd001.ps2").read_bytes() == b"MOJE-ZAPISY"
    # pusta karta nie przepadła, ale nie zasłania save'a — leży w kopii zapasowej
    assert [p.read_bytes() for p in (profiles.LOCAL / str(pid) / "_backup").rglob("Mcd001.ps2")] == [b"PUSTA"]

    _write(mc / "Mcd001.ps2", b"NOWY-ZAPIS")                # gra zapisała
    s._profile_finish(ad, names)
    assert nas_card.read_bytes() == b"NOWY-ZAPIS"


def test_settings_per_profile_keep_machine_values(prof_env):
    cfg, tmp = prof_env
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    ad = _duck(tmp)
    ini = ad.settings_base() / "settings.ini"

    # Ola gra pierwszy raz: dostaje bieżące ustawienia, zmienia renderer
    s = _session(cfg, ola)
    names = s._profile_prepare(ad)
    ingame.ini_set(ini, {("GPU", "Renderer"): "D3D12"})
    s._profile_finish(ad, names)
    assert (Path(cfg["profiles_nas"]) / "Ola" / "settings" / "duckstation" / "settings.ini").is_file()

    # pierwszy profil zapisuje swoje (Vulkan) …
    ingame.ini_set(ini, {("GPU", "Renderer"): "Vulkan"})
    s1 = _session(cfg, first)
    s1._profile_finish(ad, s1._profile_prepare(ad))

    # … inny komputer: inna karta grafiki; Ola wraca — jej renderer, karta tego komputera
    ingame.ini_set(ini, {("GPU", "Adapter"): "GPU-B"})
    s = _session(cfg, ola)
    names = s._profile_prepare(ad)
    gpu = ingame.ini_section(ini, "GPU")
    assert gpu["Renderer"] == "D3D12" and gpu["Adapter"] == "GPU-B"
    s._profile_finish(ad, names)


def test_retroachievements_per_profile(prof_env):
    cfg, tmp = prof_env
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    duck, ps2 = _duck(tmp), _pcsx2(tmp)
    ingame.ini_set(duck._ini(), {("Cheevos", "Username"): "caros",
                                 ("Cheevos", "Token"): dstoken.encrypt("TOK1", "caros", True)})
    assert profiles.ra_for_launch(cfg, first, duck)["user"] == ""     # komputer jeszcze bez właściciela
    profiles.finish_setup(first, ask=False)
    # konto zalogowane wcześniej w DuckStation → przejmuje je profil tego komputera
    assert profiles.ra_for_launch(cfg, first, duck) == {"user": "caros", "token": "TOK1", "hardcore": False}
    profiles.ra_set(cfg, ola, {"user": "ola", "token": "TOK2", "hardcore": True})

    # gra Ola w PCSX2: jej konto, token w secrets.ini, hardcore
    s = _session(cfg, ola)
    s._profile_finish(ps2, s._profile_prepare(ps2))
    assert ingame.ini_section(ps2._ini(), "Achievements")["Username"] == "ola"
    assert ingame.ini_section(ps2._ini(), "Achievements")["ChallengeMode"] == "true"
    assert ingame.ini_section(ps2._secrets(), "Achievements")["Token"] == "TOK2"
    assert ps2.read_cheevos() == {"user": "ola", "token": "TOK2"}

    # potem pierwszy profil w DuckStation: jego konto wraca, token zaszyfrowany jak w DuckStation
    s = _session(cfg, first)
    s._profile_finish(duck, s._profile_prepare(duck))
    assert duck.read_cheevos() == {"user": "caros", "token": "TOK1"}
    stored = ingame.ini_section(duck._ini(), "Cheevos")["Token"]
    assert stored != "TOK1" and dstoken.decrypt(stored, "caros", True) == "TOK1"

    # profil bez konta wyłącza osiągnięcia
    bez = profiles.create("Gość")["id"]
    s = _session(cfg, bez)
    s._profile_finish(duck, s._profile_prepare(duck))
    assert ingame.ini_section(duck._ini(), "Cheevos")["Enabled"] == "false"
    assert duck.read_cheevos() == {}


def test_retroarch_session_cfg_has_dirs_and_account(tmp_path):
    home = tmp_path / "RetroArch"
    _write(home / "retroarch.cfg", b'savefile_directory = "default"\nsavestate_directory = ":\\\\states"\n')
    ad = ingame.RetroArch(str(home / "retroarch.exe"))
    ad.cheevos = {"user": "ola", "token": "TOK", "hardcore": False}
    args = ad.launch_args(tmp_path / "run", False)
    text = Path(args[1]).read_text(encoding="utf-8")
    assert f'savefile_directory = "{home / "saves"}"' in text       # nigdy obok gry
    assert 'cheevos_enable = "true"' in text and 'cheevos_token = "TOK"' in text
    assert 'cheevos_hardcore_mode_enable = "false"' in text


def test_profiles_recreated_from_nas(prof_env):
    cfg, tmp = prof_env
    (Path(cfg["profiles_nas"]) / "Ola" / "save").mkdir(parents=True)
    (Path(cfg["profiles_nas"]) / "pusty").mkdir(parents=True)        # bez danych — pomijany
    assert profiles.import_from_nas(cfg) == ["Ola"]
    assert profiles.import_from_nas(cfg) == []
    assert [p["name"] for p in profiles.all_profiles()] == ["Gracz", "Ola"]


def test_user_settings_newer_wins(prof_env):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    profiles.json_set(cfg, pid, "emustart.json", {"look": {"fs": 120}})
    nas = Path(cfg["profiles_nas"]) / "Gracz" / "emustart.json"
    nas.write_text(json.dumps({"look": {"fs": 140}}), encoding="utf-8")
    t = nas.stat().st_mtime + 60
    os.utime(nas, (t, t))                                    # zmiana z innego komputera
    assert profiles.json_get(cfg, pid, "emustart.json") == {"look": {"fs": 140}}


def test_art_shrinks_and_stops_when_disk_full(tmp_path, monkeypatch):
    from PIL import Image
    monkeypatch.setattr(paths, "MEDIA", tmp_path / "media")
    buf = io.BytesIO()
    Image.new("RGB", (3000, 3000), (200, 30, 30)).save(buf, "PNG")
    small = art.shrink("box", buf.getvalue())
    assert small[:4] == b"RIFF" and Image.open(io.BytesIO(small)).size == (900, 900)

    class Usage:
        free = 100 * 1024 ** 2
    monkeypatch.setattr(art.shutil, "disk_usage", lambda p: Usage)
    with pytest.raises(art.DiskFull):
        art.save("snes", "Gra", "box", buf.getvalue())
    assert not list((tmp_path / "media").rglob("*.tmp"))


# ── dwa komputery, jeden NAS ──

def test_second_pc_saves_go_to_chosen_profile(prof_env):
    """Na drugim komputerze save'y zastane w emulatorze trafiają do profilu
    wybranego przy pierwszym uruchomieniu, a nie do „Gracz” z NAS."""
    cfg, tmp = prof_env
    (Path(cfg["profiles_nas"]) / "Gracz" / "save").mkdir(parents=True)     # profil z PC1
    assert profiles.setup_needed()
    ola = profiles.create("Ola")["id"]
    profiles.finish_setup(ola, ask=False)
    emu = tmp / "emu" / "memcards"
    _write(emu / "ola.mcd", b"OLA")
    profiles.attach(profiles.first_id(), "duckstation", emu)              # gra „Gracz”
    assert (profiles.local_dir(ola, "duckstation", "memcards") / "ola.mcd").read_bytes() == b"OLA"
    assert not (profiles.local_dir(profiles.first_id(), "duckstation", "memcards") / "ola.mcd").exists()
    assert not profiles.ask_at_start()


def test_rename_moves_nas_folder_only_when_unused(prof_env):
    cfg, tmp = prof_env
    pid = profiles.create("Nowy")["id"]
    profiles.rename(pid, "Ola", cfg)
    assert profiles.get(pid)["nas_name"] == "Ola"
    (Path(cfg["profiles_nas"]) / "Ola").mkdir(parents=True)
    profiles.rename(pid, "Ola2", cfg)
    assert profiles.get(pid)["nas_name"] == "Ola"                         # folder już używany


def test_nas_backup_and_conflict_when_both_sides_changed(prof_env):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    local = profiles.local_dir(pid, "pcsx2", "memcards") / "Mcd001.ps2"
    nas = Path(cfg["profiles_nas"]) / "Gracz" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2"
    _write(local, b"V1")
    profiles.sync_up(cfg, pid, "pcsx2", ["memcards"])
    # drugi komputer zmienia save na NAS, ten komputer gra offline i też zmienia
    _write(nas, b"PC2")
    os.utime(nas, (2000000000, 2000000000))
    _write(local, b"PC1-OFFLINE")
    os.utime(local, (2000000100, 2000000100))
    profiles.sync_up(cfg, pid, "pcsx2", ["memcards"])
    assert nas.read_bytes() == b"PC1-OFFLINE"                              # nowszy wygrywa
    bak = Path(cfg["profiles_nas"]) / "Gracz" / "_backup"
    assert b"PC2" in [f.read_bytes() for f in bak.rglob("Mcd001.ps2")]     # przegrany na NAS
    c = profiles.pop_conflicts()
    assert len(c) == 1 and c[0]["file"].endswith("Mcd001.ps2")
    assert profiles.pop_conflicts() == []


def test_nas_backups_are_pruned(prof_env, monkeypatch):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    local = profiles.local_dir(pid, "pcsx2", "memcards") / "a.ps2"
    real = profiles.time.strftime
    for i in range(14):
        _write(local, f"v{i}".encode())
        os.utime(local, (1900000000 + i * 100, 1900000000 + i * 100))
        monkeypatch.setattr(profiles.time, "strftime", lambda f, i=i: f"2026-{i:02d}")
        profiles.sync_up(cfg, pid, "pcsx2", ["memcards"])
    monkeypatch.setattr(profiles.time, "strftime", real)
    assert len(list((Path(cfg["profiles_nas"]) / "Gracz" / "_backup").iterdir())) == profiles.NAS_BACKUPS


def test_resume_moves_between_computers(prof_env, monkeypatch):
    from emustart import library
    cfg, tmp = prof_env
    monkeypatch.setattr(paths, "DATA", tmp / "data")
    pid = profiles.first_id()
    state = tmp / "state.p2s"
    _write(state, b"STAN")
    g1 = {"id": 7, "es": "ps2", "name": "Gra (Europe)"}
    assert profiles.resume_put(cfg, pid, g1, "pcsx2", str(state), 1000.0)
    g2 = {"id": 42, "es": "ps2", "name": "Gra (Europe)"}                   # inny id na PC2
    launcher.resume_from_nas(cfg, pid, g2)
    r = library.get_resume(pid, 42)
    assert r["family"] == "pcsx2" and Path(r["path"]).read_bytes() == b"STAN"
    profiles.resume_drop(cfg, pid, g2)                                     # zużyty na PC2
    library.set_resume(pid, 7, "pcsx2", str(state), 1000.0)                # PC1 ma starą kopię
    launcher.resume_from_nas(cfg, pid, g1)
    assert library.get_resume(pid, 7) is None


def test_genres_are_unified():
    from emustart import metadata
    for raw in ("RPG", "Role-Playing", "Role playing games", "role-playing."):
        assert metadata.norm_genres(raw) == "RPG"
    assert metadata.norm_genres("Beat'em Up, Beat 'em Up / Fighting.") == "Beat 'em Up, Fighting"
    assert metadata.norm_genres("Racing / Driving") == "Racing"
    assert metadata.norm_genres("Puzzle-Game") == "Puzzle"
    assert metadata.norm_genres("TTL * Ball & Paddle") == "Breakout"
    assert metadata.norm_genres("N/A") == ""
    assert metadata.norm_genres("Visual Novel") == "Visual Novel"


def test_hide_tag_groups():
    from emustart import library
    g = library.tag_groups
    assert g("Sonic (USA) (Beta 2)") == {"beta"}
    assert g("Gra (Japan) (Possible Proto)") == {"beta"}
    assert g("Q (USA) (Demo) (Kiosk)") == {"demo"}
    assert g("K (Japan) (Joystick hack bootleg)") == {"pirate"}
    assert g("[BIOS] Super NES CD-ROM (Japan)") == {"program"}
    assert g("Z (USA) (Unl)") == {"unl"}
    assert g("Betrayal at Krondor (USA)") == set() and g("X (USA) (Rev 1)") == set()
    assert library.is_hidden("Sonic (USA) (Beta 2)", ["beta"]) and not library.is_hidden("Sonic (USA) (Beta 2)", ["demo"])


def test_hide_settings_are_per_profile(prof_env, monkeypatch):
    from emustart import api as api_mod, config
    cfg, tmp = prof_env
    monkeypatch.setattr(config, "save", lambda c: None)
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg = cfg
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    a._profile = first
    cfg["hide_beta"] = True
    a._user_save()
    a._profile = ola
    profiles.json_set(cfg, ola, "emustart.json", {"games_logo": True})    # profil sprzed tej wersji
    a._user_load(ola)
    assert cfg["hide_beta"] is False and cfg["hide_arcade_clones"] is True
    a._user_load(first)
    assert cfg["hide_beta"] is True


def test_nas_folder_rename_and_other_pc_follows(prof_env):
    from emustart import library
    cfg, tmp = prof_env
    pid = profiles.first_id()
    profiles.rename(pid, "Jezus")                                          # stara wersja: folder został
    assert profiles.get(pid)["nas_name"] == "Gracz"
    root = Path(cfg["profiles_nas"])
    _write(root / "Gracz" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2", b"SAVE")
    _write(root / "Gracz" / "lock", b'{"host": "INNY-PC", "time": %d}' % int(profiles.time.time()))
    assert "INNY-PC" in profiles.nas_rename(cfg, pid)                     # gra na innym komputerze
    (root / "Gracz" / "lock").unlink()
    assert profiles.nas_rename(cfg, pid) == ""
    assert profiles.get(pid)["nas_name"] == "Jezus"
    assert (root / "Jezus" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2").read_bytes() == b"SAVE"
    assert (root / "Gracz" / profiles.MOVED).is_file()

    # drugi komputer: ma ten profil jeszcze pod starym folderem
    with library.db() as c:
        c.execute("UPDATE profiles SET nas_name='Gracz' WHERE id=?", (pid,))
    assert profiles.import_from_nas(cfg) == []                              # nie powstaje duplikat
    assert profiles.get(pid)["nas_name"] == "Jezus"


def test_msu1_zip_is_fully_extracted(tmp_path):
    import zipfile
    z = tmp_path / "Gra (USA) (MSU1).zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Gra_(USA)_(MSU1).sfc", b"R" * 100)
        f.writestr("Gra_(USA)_(MSU1).msu", b"M")
        f.writestr("Gra_(USA)_(MSU1)-1.pcm", b"P" * 500)
    assert launcher._zip_multi(z)
    s = launcher.Session.__new__(launcher.Session)
    s.run_dir = tmp_path / "run"
    info = {"exts": "sfc,smc,zip"}
    rom = s._extract(z, info)
    assert rom.name.endswith(".sfc")
    assert {p.name for p in rom.parent.iterdir()} == {"Gra_(USA)_(MSU1).sfc", "Gra_(USA)_(MSU1).msu",
                                                       "Gra_(USA)_(MSU1)-1.pcm"}
    bad = tmp_path / "Bez gry.zip"
    with zipfile.ZipFile(bad, "w") as f:
        f.writestr("x.msu", b"M")
        f.writestr("x-1.pcm", b"P")
    s.run_dir = tmp_path / "run2"
    with pytest.raises(launcher.LaunchError):
        s._extract(bad, info)


def test_post_game_sync_runs_in_background():
    import threading
    gate = threading.Event()
    launcher.start_post(lambda: gate.wait(5), "Gra")
    assert launcher.post_busy() == "Gra"                 # UI wraca od razu, zapis trwa
    assert not launcher.wait_post(timeout=0.05)
    gate.set()
    assert launcher.wait_post(timeout=5) and launcher.post_busy() == ""


# ── zabezpieczenia ──

def test_settings_copy_has_no_secrets_but_machine_keeps_them(prof_env):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    home = tmp / "emu" / "RetroArch"
    _write(home / "retroarch.cfg", b'video_driver = "vulkan"\ncheevos_token = "SEKRET123"\n'
                                    b'cheevos_password = "haslo"\nnetplay_show_passworded = "true"\n')
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    s = _session(cfg, pid)
    s._profile_finish(ra, s._profile_prepare(ra))
    for f in [profiles.LOCAL / str(pid) / "settings" / "retroarch" / "retroarch.cfg",
              Path(cfg["profiles_nas"]) / "Gracz" / "settings" / "retroarch" / "retroarch.cfg"]:
        t = f.read_text(encoding="utf-8")
        assert "SEKRET123" not in t and "haslo" not in t and 'netplay_show_passworded = "true"' in t
    # kolejna gra: kopia profilu wgrana, sekrety komputera zostają
    s = _session(cfg, pid)
    s._profile_finish(ra, s._profile_prepare(ra))
    t = (home / "retroarch.cfg").read_text(encoding="utf-8")
    assert 'cheevos_token = "SEKRET123"' in t and 'cheevos_password = "haslo"' in t


def test_scrub_existing_copies(prof_env):
    cfg, tmp = prof_env
    f = Path(cfg["profiles_nas"]) / "Gracz" / "_backup" / "x" / "settings" / "duckstation" / "-" / "settings.ini"
    _write(f, b"[Cheevos]\nUsername = a\nToken = ABCDEF\n")
    machine = profiles.LOCAL / "_machine" / "duckstation" / "settings.ini"
    _write(machine, b"[Cheevos]\nToken = ZOSTAJE\n")
    assert profiles.scrub_settings_copies(cfg) == 1
    assert "ABCDEF" not in f.read_text(encoding="utf-8") and "Username = a" in f.read_text(encoding="utf-8")
    assert "ZOSTAJE" in machine.read_text(encoding="utf-8")


def test_resume_index_rejects_paths(prof_env, monkeypatch):
    cfg, tmp = prof_env
    monkeypatch.setattr(paths, "DATA", tmp / "data")
    pid = profiles.first_id()
    d = Path(cfg["profiles_nas"]) / "Gracz" / "resume"
    _write(tmp / "tajne.txt", b"X")
    d.mkdir(parents=True)
    (d / "index.json").write_text(json.dumps({"ps2/Gra": {"family": "pcsx2", "file": "../../../tajne.txt",
                                                          "created": 1}}), encoding="utf-8")
    assert profiles.resume_pull(cfg, pid, {"id": 1, "es": "ps2", "name": "Gra"}) is None
    profiles.resume_drop(cfg, pid, {"id": 1, "es": "ps2", "name": "Gra"})
    assert (tmp / "tajne.txt").exists()


def test_json_written_locally_is_sent_to_nas_later(prof_env, monkeypatch):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    monkeypatch.setattr(profiles, "nas_online", lambda c: False)
    profiles.ra_set(cfg, pid, {"user": "u", "token": "t"})
    nas = Path(cfg["profiles_nas"]) / "Gracz" / profiles.RA_FILE
    assert not nas.exists()
    monkeypatch.setattr(profiles, "nas_online", lambda c: True)
    assert profiles.ra_get(cfg, pid)["user"] == "u"
    assert json.loads(nas.read_text(encoding="utf-8"))["user"] == "u"


def test_server_rejects_foreign_host_and_api_without_header(tmp_path):
    import urllib.request
    import urllib.error
    from emustart import server

    class Api:
        def ping(self):
            return "pong"
    base = server.start(0, dev_api=Api())
    port = base.rsplit(":", 1)[1]

    def call(path, host, data=None, hdr=None):
        req = urllib.request.Request(base + path, data=data, headers={"Host": host, **(hdr or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as ex:
            return ex.code
    assert call("/index.html", f"127.0.0.1:{port}") == 200
    assert call("/index.html", f"evil.example:{port}") == 403
    assert call("/api/ping", f"127.0.0.1:{port}", b"[]") == 403
    assert call("/api/ping", f"127.0.0.1:{port}", b"[]", {"X-EmuStart": "1"}) == 200
    server.DEV_API = None


def test_cleanup_old_run_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "RUN_TMP", tmp_path)
    old, new = tmp_path / "1_1", tmp_path / "2_2"
    old.mkdir(); new.mkdir()
    os.utime(old, (1, 1))
    assert launcher.cleanup_run_dirs() == 1 and new.exists() and not old.exists()


# ── Amiga: IPF, WHDLoad ──

def test_whdload_titles_and_folders(tmp_path):
    from emustart import systems, scanner
    assert systems.whd_title("1869_v1.0_De_AGA_1653") == ("1869", "(v1.0) (De) (AGA)")
    assert systems.whd_title("WhereInTheWorldIsCarmenSandiego_v0.1_NTSC_2479")[0] == "Where In The World Is Carmen Sandiego"
    assert systems.whd_title("242_v1.2_Fairlight&VirtualDreams", demo=True) == ("242", "(v1.2) (Fairlight & Virtual Dreams)")
    whd = tmp_path / "WHDLoad"
    for sub in ("Games", "Demos", "Magazines"):
        (whd / sub).mkdir(parents=True)
    found, unknown = scanner.collect_systems({"rom_roots": [str(whd)]})
    assert set(found) == {"amigawhdgames", "amigawhddemos"}
    assert [d.name for d in unknown] == ["Magazines"]


def test_amiga_multidisk_zip_becomes_m3u(tmp_path):
    import zipfile
    z = tmp_path / "Sensible World of Soccer (Europe).zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("Sensible World of Soccer (Europe) (Disk 2).ipf", b"B" * 10)
        f.writestr("Sensible World of Soccer (Europe) (Disk 1).ipf", b"A" * 5)
    s = launcher.Session.__new__(launcher.Session)
    s.run_dir = tmp_path / "run"
    info = {"exts": "ipf,adf,zip"}
    m3u = s._extract(z, info, m3u_ok=True)
    assert m3u.suffix == ".m3u"
    assert m3u.read_text(encoding="utf-8").splitlines() == ["Sensible World of Soccer (Europe) (Disk 1).ipf",
                                                           "Sensible World of Soccer (Europe) (Disk 2).ipf"]
    s.run_dir = tmp_path / "run2"
    assert s._extract(z, info, m3u_ok=False).name.endswith("(Disk 1).ipf")      # bez m3u: pierwsza dyskietka


def test_capsimg_install_from_tar(tmp_path, monkeypatch):
    import io
    import tarfile
    from emustart import installer
    ra = tmp_path / "emu" / "RetroArch"
    _write(ra / "retroarch.exe", b"")
    _write(ra / "retroarch.cfg", b'system_directory = ":\\system"\n')
    assert installer.ra_system_dir(ra) == ra / "system"
    assert installer.amiga_extras(str(tmp_path / "emu"))[0]["kind"] == "capsimg"
    assert installer.amiga_kickstart_missing(str(tmp_path / "emu"))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as t:
        for name, data in (("../evil.dll", b"X"), ("CAPSImg/Windows/x86-64/capsimg.dll", b"DLL")):
            ti = tarfile.TarInfo(name); ti.size = len(data); t.addfile(ti, io.BytesIO(data))
    monkeypatch.setattr(installer, "_capsimg_url", lambda: {"url": "x", "size": 3, "version": "v5", "name": "c.tar.xz"})
    monkeypatch.setattr(installer, "_download", lambda url, dest, p, c: dest.write_bytes(buf.getvalue()))
    monkeypatch.setattr(installer, "_save_version", lambda k, v: None)
    job = installer.Job({"emu_root": str(tmp_path / "emu")}, [])
    job._install(tmp_path / "emu", {"kind": "capsimg", "key": "capsimg", "label": "capsimg"})
    assert (ra / "system" / "capsimg.dll").read_bytes() == b"DLL"
    assert not (ra / "evil.dll").exists() and not (tmp_path / "emu" / "evil.dll").exists()
    assert installer.amiga_extras(str(tmp_path / "emu")) == []


def test_bios_copied_from_bios_dir(tmp_path):
    from emustart import bios
    ra = tmp_path / "RetroArch"
    _write(ra / "retroarch.cfg", b'system_directory = ":\\system"\n')
    _write(ra / "info" / "puae_libretro.info", b'firmware0_path = "kick34005.A500"\nfirmware1_path = "kick40068.A1200"\n'
                                                b'firmware2_path = "../zly.rom"\n')
    src = tmp_path / "bios"
    _write(src / "kick34005.A500", b"KS13")
    _write(src / "capsimg.dll", b"DLL")
    _write(src / "zly.rom", b"X")
    _write(ra / "system" / "kick40068.A1200", b"MOJ")                    # istniejący zostaje
    _write(src / "kick40068.A1200", b"INNY")
    assert bios.core_name('-L "C:\\RA\\cores\\puae_libretro.dll" -f') == "puae"
    got = bios.sync({"bios_dir": str(src)}, ra, "puae", ("capsimg.dll",))
    assert sorted(got) == ["capsimg.dll", "kick34005.A500"]
    assert (ra / "system" / "kick40068.A1200").read_bytes() == b"MOJ"
    assert not (ra / "zly.rom").exists()
    assert bios.sync({}, ra, "puae") == []


def test_whdload_kickstarts_copied_to_devs(tmp_path):
    from emustart import bios
    ra = tmp_path / "RetroArch"
    _write(ra / "retroarch.cfg", b'system_directory = ":\\system"\n')
    _write(ra / "system" / "kick34005.A500", b"KS13")
    _write(tmp_path / "bios" / "kick40068.A1200", b"KS31")
    saves = tmp_path / "saves"
    _write(saves / "PUAE" / "WHDLoad" / "Devs" / "Kickstarts" / "kick34005.A500.RTB", b"RTB")
    got = bios.whdload_kickstarts({"bios_dir": str(tmp_path / "bios")}, ra, saves)
    ks = saves / "PUAE" / "WHDLoad" / "Devs" / "Kickstarts"
    assert sorted(got) == ["kick34005.A500", "kick40068.A1200"]
    assert (ks / "kick34005.A500").read_bytes() == b"KS13" and (ks / "kick40068.A1200").read_bytes() == b"KS31"
    assert bios.whdload_kickstarts({}, ra, saves) == []                  # drugi raz nic
