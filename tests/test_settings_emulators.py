"""Ustawienia gracza w emulatorach samodzielnych (Dolphin, PPSSPP, RPCS3) i opcje rdzeni RetroArcha:
każdy profil ma swoje, przechodzą przez serwer, a wartości tego komputera (karta grafiki, ścieżki,
urządzenia dźwięku, okno) zostają na komputerze."""

from __future__ import annotations

from pathlib import Path

import pytest

from emustart import ingame, profiles

from test_core import _write, env  # noqa: F401  (fixture)
from test_profiles_full import _session, prof_env  # noqa: F401  (fixture)


def _nas_settings(cfg, name: str, family: str) -> Path:
    return Path(cfg["profiles_nas"]) / name / "settings" / "windows" / family


def _play(cfg, pid, adapter, change=None) -> None:
    """Gra profilu: ustawienia profilu → emulator, zmiana w trakcie gry, emulator → profil."""
    s = _session(cfg, pid)
    names = s._profile_prepare(adapter)
    if change:
        change()
    s._release_dirs()
    s._profile_finish(adapter, names)


# ── Dolphin ──

def _dolphin(tmp: Path) -> ingame.Dolphin:
    home = tmp / "emu" / "Dolphin"
    _write(home / "portable.txt", b"")
    c = home / "User" / "Config"
    _write(c / "Dolphin.ini", b"[General]\nISOPath0 = D:\\gry\\gc\nISOPaths = 1\nWirelessMac = 00:11\n\n"
                              b"[Core]\nGFXBackend = Vulkan\nCPUThread = True\n\n[DSP]\nBackend = Cubeb\n")
    _write(c / "GFX.ini", b"[Settings]\nInternalResolution = 2\n\n[Hardware]\nAdapter = 0\n")
    _write(c / "GCPadNew.ini", b"[GCPad1]\nDevice = XInput/0/Gamepad\nButtons/A = `Button A`\n")
    _write(c / "Qt.ini", b"[mainwindow]\ngeometry = AAA\n")
    _write(c / "Logger.ini", b"[Options]\nWriteToFile = True\n")
    _write(home / "User" / "GameSettings" / "GALE01.ini", b"[Video_Settings]\nInternalResolution = 3\n")
    return ingame.Dolphin(str(home / "Dolphin.exe"))


def test_dolphin_settings_per_player_keep_machine_values(prof_env):
    cfg, tmp = prof_env
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    ad = _dolphin(tmp)
    c = ad.settings_base() / "Config"
    gs = ad.settings_base() / "GameSettings" / "GALE01.ini"

    def ola_changes():
        ingame.ini_set(c / "GFX.ini", {("Settings", "InternalResolution"): "4"})
        ingame.ini_set(c / "GCPadNew.ini", {("GCPad1", "Buttons/A"): "`Button B`"})
        gs.write_text("[Video_Settings]\nInternalResolution = 5\n")
    _play(cfg, ola, ad, ola_changes)
    nas = _nas_settings(cfg, "Ola", "dolphin")
    assert (nas / "Config" / "GFX.ini").is_file() and (nas / "GameSettings" / "GALE01.ini").is_file()
    assert not (nas / "Config" / "Qt.ini").exists() and not (nas / "Config" / "Logger.ini").exists()

    _play(cfg, first, ad, lambda: ingame.ini_set(c / "GFX.ini", {("Settings", "InternalResolution"): "1"}))
    assert ingame.ini_section(c / "GFX.ini", "Settings")["InternalResolution"] == "1"

    # ten sam emulator na innym komputerze: inna karta, inne ścieżki gier, inne okno
    ingame.ini_set(c / "GFX.ini", {("Hardware", "Adapter"): "1"})
    ingame.ini_set(c / "Dolphin.ini", {("Core", "GFXBackend"): "D3D12", ("General", "ISOPath0"): "E:\\gc"})
    (c / "Qt.ini").write_text("[mainwindow]\ngeometry = BBB\n")
    s = _session(cfg, ola)
    names = s._profile_prepare(ad)
    assert ingame.ini_section(c / "GFX.ini", "Settings")["InternalResolution"] == "4"
    assert ingame.ini_section(c / "GFX.ini", "Hardware")["Adapter"] == "1"
    core = ingame.ini_section(c / "Dolphin.ini", "Core")
    assert core["GFXBackend"] == "D3D12" and core["CPUThread"] == "True"
    assert ingame.ini_section(c / "Dolphin.ini", "General")["ISOPath0"] == "E:\\gc"
    assert ingame.ini_section(c / "GCPadNew.ini", "GCPad1")["Buttons/A"] == "`Button B`"
    assert "InternalResolution = 5" in gs.read_text()
    assert "BBB" in (c / "Qt.ini").read_text()
    s._release_dirs()
    s._profile_finish(ad, names)


def test_dolphin_retroachievements_token_not_in_profile_copy(prof_env):
    cfg, tmp = prof_env
    pid = profiles.first_id()
    ad = _dolphin(tmp)
    ra = ad.settings_base() / "Config" / "RetroAchievements.ini"
    _write(ra, b"[Achievements]\nEnabled = True\nUsername = caros\nApiToken = SEKRET\nHardcoreEnabled = True\n")
    _play(cfg, pid, ad)
    copy = profiles.LOCAL / str(pid) / "settings" / "dolphin" / "Config" / "RetroAchievements.ini"
    assert "SEKRET" not in copy.read_text() and "HardcoreEnabled = True" in copy.read_text()
    _play(cfg, pid, ad)
    assert "ApiToken = SEKRET" in ra.read_text()          # konto tego komputera zostaje


# ── PPSSPP ──

def _ppsspp(tmp: Path) -> ingame.PPSSPP:
    home = tmp / "emu" / "PPSSPP"
    sysd = home / "memstick" / "PSP" / "SYSTEM"
    _write(sysd / "ppsspp.ini", b"[General]\nCurrentDirectory = D:/gry/psp\nWindowX = 10\nLanguage = pl_PL\n\n"
                                b"[Graphics]\nGraphicsBackend = 3 (VULKAN)\nVulkanDevice = GPU-A\n"
                                b"InternalResolution = 2\n\n[Sound]\nAudioDevice = Glosniki\n\n"
                                b"[SystemParam]\nNickName = Gracz\nMacAddress = aa:bb\n\n"
                                b"[Recent]\nFileName0 = D:/gry/psp/a.iso\n")
    _write(sysd / "controls.ini", b"[ControlMapping]\nUp = 1-19\n")
    _write(sysd / "ULUS10041_ppsspp.ini", b"[Graphics]\nInternalResolution = 3\n")
    return ingame.PPSSPP(str(home / "PPSSPPWindows64.exe"))


def test_ppsspp_settings_per_player_keep_machine_values(prof_env):
    cfg, tmp = prof_env
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    ad = _ppsspp(tmp)
    sysd = ad.home / "memstick" / "PSP" / "SYSTEM"
    ini, game = sysd / "ppsspp.ini", sysd / "ULUS10041_ppsspp.ini"

    def ola_changes():
        ingame.ini_set(ini, {("Graphics", "InternalResolution"): "5", ("SystemParam", "NickName"): "Ola"})
        ingame.ini_set(sysd / "controls.ini", {("ControlMapping", "Up"): "1-51"})
        game.write_text("[Graphics]\nInternalResolution = 6\n")
    _play(cfg, ola, ad, ola_changes)
    assert (_nas_settings(cfg, "Ola", "ppsspp") / "memstick" / "PSP" / "SYSTEM" / "ULUS10041_ppsspp.ini").is_file()
    _play(cfg, first, ad, lambda: ingame.ini_set(ini, {("SystemParam", "NickName"): "Pierwszy"}))

    ingame.ini_set(ini, {("Graphics", "VulkanDevice"): "GPU-B", ("Sound", "AudioDevice"): "Sluchawki",
                         ("General", "CurrentDirectory"): "E:/psp", ("SystemParam", "MacAddress"): "cc:dd"})
    s = _session(cfg, ola)
    names = s._profile_prepare(ad)
    g, sp = ingame.ini_section(ini, "Graphics"), ingame.ini_section(ini, "SystemParam")
    assert g["InternalResolution"] == "5" and g["VulkanDevice"] == "GPU-B"
    assert sp["NickName"] == "Ola" and sp["MacAddress"] == "cc:dd"
    assert ingame.ini_section(ini, "Sound")["AudioDevice"] == "Sluchawki"
    assert ingame.ini_section(ini, "General")["CurrentDirectory"] == "E:/psp"
    assert ingame.ini_section(sysd / "controls.ini", "ControlMapping")["Up"] == "1-51"
    assert "InternalResolution = 6" in game.read_text()
    s._release_dirs()
    s._profile_finish(ad, names)


# ── RPCS3 (config.yml) ──

YML = """Audio:
  Audio Device: "@@@default@@@"
  Renderer: Cubeb
Core:
  PPU Decoder: Recompiler (LLVM)
Video:
  Renderer: Vulkan
  Resolution Scale: 100
  Vulkan:
    Adapter: GPU-A
    Exclusive Fullscreen Mode: Automatic
  Write Color Buffers: true
"""


def test_yml_pick_and_set_nested_keys(tmp_path):
    f = tmp_path / "config.yml"
    f.write_text(YML)
    rules = [("Video/Vulkan", "Adapter"), ("Audio", "Audio Device")]
    assert ingame.yml_pick(f, rules) == {("Video/Vulkan", "Adapter"): "GPU-A",
                                         ("Audio", "Audio Device"): '"@@@default@@@"'}
    ingame.yml_set(f, {("Video/Vulkan", "Adapter"): "GPU-B", ("Video", "Resolution Scale"): "150",
                       ("Core", "SPU Decoder"): "Recompiler (LLVM)", ("Brak", "Klucz"): "x"})
    text = f.read_text()
    assert "    Adapter: GPU-B\n" in text and "  Resolution Scale: 150\n" in text
    assert "  SPU Decoder: Recompiler (LLVM)\nVideo:" in text          # na końcu istniejącej sekcji
    assert "Brak" not in text                                          # bez sekcji — nic nie dopisuje
    assert ingame.yml_pick(f, [("Video", "Renderer")]) == {("Video", "Renderer"): "Vulkan"}
    assert ingame.cfg_blank(YML, [("Video/Vulkan", "Adapter")], "config.yml").count("GPU-A") == 0


def _rpcs3(tmp: Path) -> ingame.RPCS3:
    home = tmp / "emu" / "RPCS3"
    _write(home / "config" / "config.yml", YML.encode())
    _write(home / "config" / "custom_configs" / "config_NPUB31752.yml", b"Video:\n  Resolution Scale: 200\n")
    _write(home / "config" / "input_configs" / "global" / "Default.yml", b"Player 1 Input:\n  Handler: XInput\n")
    _write(home / "config" / "vfs.yml", b"$(EmulatorDir): D:/emu/RPCS3/\n")
    _write(home / "GuiConfigs" / "CurrentSettings.ini", b"[main_window]\ngeometry=AAA\n")
    return ingame.RPCS3(str(home / "rpcs3.exe"))


def test_rpcs3_settings_per_player_keep_machine_values(prof_env):
    cfg, tmp = prof_env
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    ad = _rpcs3(tmp)
    yml = ad.home / "config" / "config.yml"

    _play(cfg, ola, ad, lambda: ingame.yml_set(yml, {("Video", "Resolution Scale"): "300"}))
    nas = _nas_settings(cfg, "Ola", "rpcs3")
    assert (nas / "config" / "custom_configs" / "config_NPUB31752.yml").is_file()
    assert not (nas / "config" / "vfs.yml").exists() and not (nas / "GuiConfigs").exists()
    _play(cfg, first, ad, lambda: ingame.yml_set(yml, {("Video", "Resolution Scale"): "100"}))

    ingame.yml_set(yml, {("Video/Vulkan", "Adapter"): "GPU-B", ("Audio", "Audio Device"): "Sluchawki"})
    s = _session(cfg, ola)
    names = s._profile_prepare(ad)
    got = ingame.yml_pick(yml, [("Video", "Resolution Scale"), ("Video/Vulkan", "Adapter"), ("Audio", "Audio Device")])
    assert got == {("Video", "Resolution Scale"): "300", ("Video/Vulkan", "Adapter"): "GPU-B",
                   ("Audio", "Audio Device"): "Sluchawki"}
    s._release_dirs()
    s._profile_finish(ad, names)


# ── RetroArch: wspólny plik opcji rdzeni ──

def test_retroarch_global_core_options_file_is_per_player(prof_env):
    cfg, tmp = prof_env
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    home = tmp / "emu" / "RetroArch"
    _write(home / "retroarch.cfg", b'video_driver = "vulkan"\nglobal_core_options = "true"\n')
    opts = home / "retroarch-core-options.cfg"
    _write(opts, b'reicast_internal_resolution = "640x480"\n')
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    _play(cfg, ola, ra, lambda: opts.write_text('reicast_internal_resolution = "1920x1440"\n'))
    _play(cfg, first, ra, lambda: opts.write_text('reicast_internal_resolution = "640x480"\n'))
    s = _session(cfg, ola)
    names = s._profile_prepare(ra)
    assert '"1920x1440"' in opts.read_text()
    s._release_dirs()
    s._profile_finish(ra, names)


# ── Vita3K (PS Vita): ux0\user — folder gracza (dowiązanie), config.yml — ustawienia gracza ──

VITA_CFG = 'backend-renderer: Vulkan\ngpu-idx: 0\nresolution-multiplier: 1\npref-path: ""\nuser-id: "00"\n'


def test_vita3k_folders(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    home = tmp_path / "Vita3K"
    _write(home / "Vita3K.exe", b"")
    ad = ingame.adapter_for(str(home / "Vita3K.exe"))
    assert ad.family == "vita3k"
    # domyślnie: system plików Vity w folderze użytkownika (SDL), config.yml obok exe
    assert ad.save_dirs() == [tmp_path / "appdata" / "Vita3K" / "Vita3K" / "ux0" / "user"]
    assert ad.settings_base() == home and ad.settings_files() == ["config.yml"]
    # pref-path z config.yml
    _write(home / "config.yml", f'pref-path: {tmp_path / "vita"}\n'.encode())
    (tmp_path / "vita").mkdir()
    assert ad.save_dirs() == [tmp_path / "vita" / "ux0" / "user"]
    # tryb przenośny: folder portable\ obok exe — wszystko w nim, system plików w portable\fs
    (home / "portable").mkdir()
    assert ad.save_dirs() == [home / "portable" / "fs" / "ux0" / "user"]
    assert ad.settings_base() == home / "portable"


def test_vita3k_saves_and_settings_per_player(prof_env):
    cfg, tmp = prof_env
    first = profiles.first_id()
    ola = profiles.create("Ola")["id"]
    home = tmp / "emu" / "Vita3K"
    _write(home / "portable" / "config.yml", VITA_CFG.encode())
    user = home / "portable" / "fs" / "ux0" / "user"
    _write(user / "00" / "savedata" / "PCSE00120" / "sce_sys" / "param.sfo", b"ZAPIS-PIERWSZEGO")
    ad = ingame.adapter_for(str(home / "Vita3K.exe"))
    yml = home / "portable" / "config.yml"

    _play(cfg, first, ad, lambda: ingame.yml_set(yml, {("", "resolution-multiplier"): "2"}))
    assert profiles._is_junction(user)
    def ola_plays():
        _write(user / "00" / "savedata" / "PCSE00120" / "sce_sys" / "param.sfo", b"ZAPIS-OLI")
        ingame.yml_set(yml, {("", "resolution-multiplier"): "3", ("", "gpu-idx"): "1"})
    _play(cfg, ola, ad, ola_plays)                       # Ola: pusty folder gracza, swój zapis

    ingame.yml_set(yml, {("", "gpu-idx"): "5"})          # karta tego komputera
    s = _session(cfg, first)
    names = s._profile_prepare(ad)
    assert (user / "00" / "savedata" / "PCSE00120" / "sce_sys" / "param.sfo").read_bytes() == b"ZAPIS-PIERWSZEGO"
    got = ingame.yml_pick(yml, [("", "resolution-multiplier|gpu-idx")])
    assert got == {("", "resolution-multiplier"): "2", ("", "gpu-idx"): "5"}
    s._release_dirs()
    s._profile_finish(ad, names)
    ola_dir = profiles.local_dir(ola, "vita3k", "user")
    assert (ola_dir / "00" / "savedata" / "PCSE00120" / "sce_sys" / "param.sfo").read_bytes() == b"ZAPIS-OLI"



# ── etap 5: ustawienia gracza przechodzą między klientami (dwa urządzenia, jeden serwer) ──

def _device(tmp: Path, name: str, monkeypatch):
    """Drugi klient: własny folder profili na urządzeniu i własne instalacje emulatorów (w teście baza
    profili wspólna — te same konta)."""
    monkeypatch.setattr(profiles, "LOCAL", tmp / name / "profiles")
    return tmp / name


def _cross(kind: str, root: Path):
    """(adapter, zmiana gracza, odczyt ustawienia gracza, wartość komputera B, odczyt wartości komputera)."""
    if kind == "dolphin":
        ad = _dolphin(root)
        f = ad.settings_base() / "Config" / "GFX.ini"
        return (ad, lambda: ingame.ini_set(f, {("Settings", "InternalResolution"): "6"}),
                lambda: ingame.ini_section(f, "Settings")["InternalResolution"],
                lambda: ingame.ini_set(f, {("Hardware", "Adapter"): "9"}),
                lambda: ingame.ini_section(f, "Hardware")["Adapter"])
    if kind == "ppsspp":
        ad = _ppsspp(root)
        f = ad.home / "memstick" / "PSP" / "SYSTEM" / "ppsspp.ini"
        return (ad, lambda: ingame.ini_set(f, {("Graphics", "InternalResolution"): "6"}),
                lambda: ingame.ini_section(f, "Graphics")["InternalResolution"],
                lambda: ingame.ini_set(f, {("Graphics", "VulkanDevice"): "9"}),
                lambda: ingame.ini_section(f, "Graphics")["VulkanDevice"])
    if kind == "rpcs3":
        ad = _rpcs3(root)
        f = ad.home / "config" / "config.yml"
        return (ad, lambda: ingame.yml_set(f, {("Video", "Resolution Scale"): "6"}),
                lambda: ingame.yml_pick(f, [("Video", "Resolution Scale")])[("Video", "Resolution Scale")],
                lambda: ingame.yml_set(f, {("Video/Vulkan", "Adapter"): "9"}),
                lambda: ingame.yml_pick(f, [("Video/Vulkan", "Adapter")])[("Video/Vulkan", "Adapter")])
    home = root / "emu" / "Vita3K"
    _write(home / "portable" / "config.yml", VITA_CFG.encode())
    ad = ingame.adapter_for(str(home / "Vita3K.exe"))
    f = home / "portable" / "config.yml"
    return (ad, lambda: ingame.yml_set(f, {("", "resolution-multiplier"): "6"}),
            lambda: ingame.yml_pick(f, [("", "resolution-multiplier")])[("", "resolution-multiplier")],
            lambda: ingame.yml_set(f, {("", "gpu-idx"): "9"}),
            lambda: ingame.yml_pick(f, [("", "gpu-idx")])[("", "gpu-idx")])


@pytest.mark.parametrize("kind", ["dolphin", "ppsspp", "rpcs3", "vita3k"])
def test_player_settings_reach_second_client(prof_env, monkeypatch, kind):
    cfg, tmp = prof_env
    ola = profiles.create("Ola")["id"]
    ad_a, change, read, _m, _r = _cross(kind, _device(tmp, "pc", monkeypatch))
    _play(cfg, ola, ad_a, change)                          # komputer A: Ola zmienia ustawienie
    ad_b, _c, read_b, machine_b, read_machine = _cross(kind, _device(tmp, "laptop", monkeypatch))
    machine_b()                                            # komputer B: inna karta grafiki
    s = _session(cfg, ola)
    names = s._profile_prepare(ad_b)                       # komputer B: ustawienia Oli z serwera
    assert read_b() == "6" and read_machine() == "9"
    s._release_dirs()
    s._profile_finish(ad_b, names)


# ── czas przygotowania do gry: kroki w logu „czas do startu” ──

def test_profile_prepare_records_step_times(prof_env):
    cfg, tmp = prof_env
    ad = _dolphin(tmp)
    s = _session(cfg, profiles.first_id())
    names = s._profile_prepare(ad)
    assert [n for n, _t in s._steps] == ["przeniesione profile", "blokada", "ustawienia", "konto RA, emulator",
                                         "foldery zapisów"]
    assert all(t >= 0 for _n, t in s._steps)
    s._release_dirs()
    s._profile_finish(ad, names)
