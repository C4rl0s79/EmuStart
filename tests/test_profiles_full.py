"""Profile per gracz: save'y po czystej instalacji, ustawienia emulatorów i EmuStart,
RetroAchievements (RetroArch, DuckStation, PCSX2), ochrona dysku przy grafikach."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from emustart import art, ingame, launcher, paths, profiles

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
    # konto zalogowane wcześniej w DuckStation → przejmuje je pierwszy profil
    ingame.ini_set(duck._ini(), {("Cheevos", "Username"): "caros", ("Cheevos", "Token"): "TOK1"})
    assert profiles.ra_for_launch(cfg, first, duck)["user"] == "caros"
    profiles.ra_set(cfg, ola, {"user": "ola", "token": "TOK2", "hardcore": True})

    # gra Ola w PCSX2: jej konto, token w secrets.ini, hardcore
    s = _session(cfg, ola)
    s._profile_finish(ps2, s._profile_prepare(ps2))
    assert ingame.ini_section(ps2._ini(), "Achievements")["Username"] == "ola"
    assert ingame.ini_section(ps2._ini(), "Achievements")["ChallengeMode"] == "true"
    assert ingame.ini_section(ps2._secrets(), "Achievements")["Token"] == "TOK2"
    assert ps2.read_cheevos() == {"user": "ola", "token": "TOK2"}

    # potem pierwszy profil w DuckStation: jego konto wraca
    s = _session(cfg, first)
    s._profile_finish(duck, s._profile_prepare(duck))
    assert duck.read_cheevos() == {"user": "caros", "token": "TOK1"}

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
