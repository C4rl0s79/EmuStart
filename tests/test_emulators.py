"""Emulatory z serwera: magazyn serwera (emustart/emustore.py) pobiera wydania i składa
wersje z manifestem, komputer (emustart/installer.py) wyrównuje do nich swój folder
emulatorów — tylko z serwera, bez ruszania konfiguracji i zapisów użytkownika."""

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from emustart import emucatalog, emustore, installer, library, netsrc, remote, server


def _zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


RELEASES: dict = {}


@pytest.fixture()
def emu_server(tmp_path, monkeypatch):
    """Serwer z magazynem emulatorów (wydania z „internetu” podstawione) i klient z endpointem."""
    cfg_srv = {"emu_store_dir": str(tmp_path / "store"), "server_token": "tok", "systems": {}}

    class A:
        _cfg = cfg_srv
    monkeypatch.setattr(remote, "API", A())
    with library.db() as c:
        for es, disp in (("psx", "PlayStation"), ("lynx", "Lynx"), ("amiga", "Amiga")):
            c.execute("INSERT OR REPLACE INTO systems(es, display, rom_dir, games, scanned) VALUES(?,?,?,?,0)",
                      (es, disp, es, 1))
    RELEASES.clear()
    RELEASES.update({
        "duckstation": ("v1", "ds.zip", _zip({"duckstation-qt-x64-ReleaseLTCG.exe": b"EXE1",
                                              "settings.ini": b"DOMYSLNE", "resources/a.txt": b"A"})),
        "retroarch": ("1.0", "ra.zip", _zip({"RetroArch-Win64/retroarch.exe": b"RA",
                                             "RetroArch-Win64/retroarch.cfg": b"DOMYSLNY"})),
        "core-handy": ("nightly-1", "handy.zip", _zip({"handy_libretro.dll": b"HANDY"})),
        "core-puae": ("nightly-1", "puae.zip", _zip({"puae_libretro.dll": b"PUAE"})),
    })
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w:xz") as t:
        for name, data in (("../evil.dll", b"X"), ("CAPSImg/Windows/x86-64/capsimg.dll", b"CAPS")):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    RELEASES["capsimg"] = ("v5", "c.tar.xz", tar.getvalue())

    def latest(item):
        ver, name, _data = RELEASES[item]
        out = {"url": f"https://x/{item}/{ver}/{name}", "version": ver, "name": name,
               "strip_root": item == "retroarch"}
        if item == "retroarch":
            out["extra"] = {"info": "https://x/info.zip"}
        return out

    def download(url, dest, progress=None, cancel=None):
        if url.endswith("info.zip"):
            data = _zip({"handy_libretro.info": 'corename = "Handy"\ndatabase = "Atari - Lynx"\n',
                         "puae_libretro.info": 'corename = "PUAE"\ndatabase = "Commodore - Amiga"\n'})
        else:
            data = RELEASES[url.split("/")[3]][2]
        Path(dest).write_bytes(data)
    monkeypatch.setattr(emucatalog, "all_cores", lambda: ["handy", "puae"])
    monkeypatch.setattr(emucatalog, "latest", latest)
    monkeypatch.setattr(emucatalog, "download", download)
    url = server.start(0, bind="127.0.0.1")
    host, port = url.removeprefix("http://").split(":")
    monkeypatch.setattr(netsrc, "endpoint", lambda cfg: (host, int(port), "tok"))
    return cfg_srv


def _sync(cfg, es_list):
    installer.refresh_index(cfg)
    job = installer.Job(cfg, installer.sync_items(cfg, es_list), quiet_busy=True)
    job.start()
    job.thread.join(30)
    return job.status()


def test_server_store_builds_versions_with_manifest(emu_server):
    need = emustore.needed(emu_server)
    assert need["duckstation"] == ["psx"] and need["retroarch"] == ["amiga", "lynx"]
    assert "capsimg" in need and "core-puae" in need
    r = emustore.update(emu_server)
    assert r["ok"] and not r["errors"], r
    m = emustore.manifest(emu_server, "duckstation", "v1")
    assert set(m["files"]) == {"duckstation-qt-x64-ReleaseLTCG.exe", "settings.ini", "resources/a.txt"}
    ra = emustore.manifest(emu_server, "retroarch", "1.0")["files"]
    assert "retroarch.exe" in ra and "info/handy_libretro.info" in ra        # bez folderu nadrzędnego
    assert list(emustore.manifest(emu_server, "capsimg", "v5")["files"]) == ["capsimg.dll"]
    # na serwerze zostaje samo archiwum (i manifest) — emulatory nie są tam uruchamiane
    vdir = emustore.version_dir(emu_server, "duckstation", "v1")
    assert sorted(p.name for p in vdir.iterdir()) == [".archive.zip", ".manifest.json"]
    assert emustore.update(emu_server)["updated"] == []                      # ta sama wersja — nic


def test_client_installs_missing_and_updates_keeping_user_files(emu_server, tmp_path, monkeypatch):
    emustore.update(emu_server)
    root = tmp_path / "emu"
    # emulator zastany w folderze (np. z RetroBata): stary exe, własne ustawienia i karty pamięci
    (root / "DuckStation" / "memcards").mkdir(parents=True)
    (root / "DuckStation" / "duckstation-qt-x64-ReleaseLTCG.exe").write_bytes(b"STARY")
    (root / "DuckStation" / "settings.ini").write_bytes(b"MOJE")
    (root / "DuckStation" / "memcards" / "1.mcd").write_bytes(b"KARTA")
    cfg = {"emu_root": str(root), "systems": {}}
    st = _sync(cfg, ["psx", "lynx", "amiga"])
    assert st["errors"] == [], st
    ds = root / "DuckStation"
    assert (ds / "duckstation-qt-x64-ReleaseLTCG.exe").read_bytes() == b"EXE1"     # przejęty i zaktualizowany
    assert (ds / "settings.ini").read_bytes() == b"MOJE"                           # konfiguracja zostaje
    assert (ds / "memcards" / "1.mcd").read_bytes() == b"KARTA"                    # spoza manifestu — nietknięte
    assert (ds / "portable.txt").is_file()
    assert (root / "RetroArch" / "retroarch.exe").read_bytes() == b"RA"
    assert (root / "RetroArch" / "cores" / "handy_libretro.dll").read_bytes() == b"HANDY"
    assert (root / "RetroArch" / "system" / "capsimg.dll").read_bytes() == b"CAPS"
    assert not (root / "evil.dll").exists() and not (root / "RetroArch" / "evil.dll").exists()
    assert cfg["systems"]["lynx"]["label"] == "RetroArch: Handy"
    assert not list((root / ".emustart-tmp").glob("*"))
    assert installer.sync_items(cfg, ["psx", "lynx", "amiga"]) == []           # wszystko aktualne

    # nowa wersja na serwerze: komputer podmienia tylko zmienione pliki programu
    RELEASES["duckstation"] = ("v2", "ds.zip", _zip({"duckstation-qt-x64-ReleaseLTCG.exe": b"EXE2",
                                                     "settings.ini": b"DOMYSLNE2", "resources/a.txt": b"A"}))
    emustore.update(emu_server)
    assert installer.sync_items(cfg, ["psx"]) == []                              # indeks jeszcze stary
    st = _sync(cfg, ["psx"])
    assert st["installed"] == ["DuckStation"]
    assert (ds / "duckstation-qt-x64-ReleaseLTCG.exe").read_bytes() == b"EXE2"
    assert (ds / "settings.ini").read_bytes() == b"MOJE"
    idx = emustore.load_index(emu_server)["items"]["duckstation"]
    assert idx["version"] == "v2" and idx["previous"] == "v1"                    # poprzednia zostaje

    # trzecia wersja przy działającym emulatorze: podmiana odłożona, nic nie zepsute
    RELEASES["duckstation"] = ("v3", "ds.zip", _zip({"duckstation-qt-x64-ReleaseLTCG.exe": b"EXE3"}))
    emustore.update(emu_server)
    assert not emustore.version_dir(emu_server, "duckstation", "v1").exists()     # zostają dwie wersje
    monkeypatch.setattr(installer, "_locked", lambda p: True)
    st = _sync(cfg, ["psx"])
    assert st["postponed"] == ["DuckStation"] and st["errors"] == []
    assert (ds / "duckstation-qt-x64-ReleaseLTCG.exe").read_bytes() == b"EXE2"


def test_client_never_downloads_emulators_from_internet():
    """Tylko z serwera: moduł komputera nie zna adresów wydań w internecie."""
    src = Path(installer.__file__).read_text(encoding="utf-8")
    for host in ("api.github.com", "buildbot.libretro.com", "dolphin-emu.org", "eden-emu", "7-zip.org"):
        assert host not in src, host
    assert not hasattr(installer, "STANDALONE") and not hasattr(installer, "CORES")


def test_archive_download_resumes_and_needs_key(emu_server, tmp_path):
    """Wersja jako jedno archiwum (zamiast tysięcy małych plików): pobieranie wznawia się
    od przerwanego miejsca; bez klucza serwer nic nie wyda."""
    import http.client
    emustore.update(emu_server)
    arch = emustore.archive_path(emu_server, "duckstation", "v1")
    with zipfile.ZipFile(arch) as z:                                 # bez plików magazynu
        assert sorted(z.namelist()) == ["duckstation-qt-x64-ReleaseLTCG.exe", "resources/a.txt", "settings.ini"]
    data = arch.read_bytes()
    ep = netsrc.endpoint({})
    dest = tmp_path / "ds.zip"
    (tmp_path / "ds.zip.part").write_bytes(data[:10])                # przerwane wcześniej
    installer.refresh_index({})
    installer._fetch_archive(ep, "duckstation", "v1", dest, None, None)
    assert dest.read_bytes() == data and not (tmp_path / "ds.zip.part").exists()
    c = http.client.HTTPConnection(ep[0], ep[1], timeout=10)
    c.request("GET", "/v1/emulators/archive?item=duckstation&version=v1")
    assert c.getresponse().status == 401
    c = http.client.HTTPConnection(ep[0], ep[1], timeout=10)
    c.request("GET", "/v1/emulators/archive?item=duckstation&version=..%2F..", headers={"Authorization": "Bearer tok"})
    assert c.getresponse().status == 404


def test_two_jobs_at_once_do_not_collide(emu_server, tmp_path):
    """Okno „Pobierz brakujące emulatory” i wyrównanie w tle naraz — jedno po drugim, bez
    WinError 32 i fałszywego „emulator działa”."""
    emustore.update(emu_server)
    cfg = {"emu_root": str(tmp_path / "emu"), "systems": {}}
    installer.refresh_index(cfg)
    jobs = [installer.Job(cfg, installer.sync_items(cfg, ["psx", "lynx", "amiga"])) for _ in range(2)]
    for j in jobs:
        j.start()
    for j in jobs:
        j.thread.join(30)
    assert all(j.status()["errors"] == [] for j in jobs), [j.status() for j in jobs]
    assert (tmp_path / "emu" / "DuckStation" / "duckstation-qt-x64-ReleaseLTCG.exe").read_bytes() == b"EXE1"


def test_required_settings_from_server_applied_before_game(emu_server, tmp_path):
    """Wymagane ustawienia z panelu serwera: komputer dostaje je z indeksem emulatorów i nakłada
    przed grą (ini — w pliku emulatora, RetroArch — w konfiguracji sesji)."""
    from emustart import emupolicy, ingame, srvadmin

    class A:
        _cfg = emu_server
    # domyślne, zanim komputer cokolwiek dostał
    assert installer.policy("duckstation") == {"Main|ConfirmPowerOff": "false"}
    assert not srvadmin.emu_policy_set(A(), {"family": "pcsx2", "text": "bez znaku rownosci"})["ok"]
    assert not srvadmin.emu_policy_set(A(), {"family": "retroarch", "text": "Sekcja|klucz = 1"})["ok"]
    assert not srvadmin.emu_policy_set(A(), {"family": "duckstation", "text": "Main|X = a\"b"})["ok"]
    r = srvadmin.emu_policy_set(A(), {"family": "duckstation",
                                     "text": "# komentarz\nMain|ConfirmPowerOff = false\nDisplay|VSync = true"})
    assert r["ok"], r
    srvadmin.emu_policy_set(A(), {"family": "retroarch", "text": "video_vsync = true\nsavestate_thumbnail_enable = false"})
    installer.refresh_index({})
    assert installer.policy("duckstation") == {"Main|ConfirmPowerOff": "false", "Display|VSync": "true"}

    ds_home = tmp_path / "DuckStation"
    ds_home.mkdir()
    (ds_home / "settings.ini").write_text("[Main]\nConfirmPowerOff = true\n[Display]\nVSync = false\n", encoding="utf-8")
    ds = ingame.DuckStation(str(ds_home / "duckstation-qt-x64-ReleaseLTCG.exe"))
    ds.apply_policy(installer.policy("duckstation"))
    ini = (ds_home / "settings.ini").read_text(encoding="utf-8")
    assert "ConfirmPowerOff = false" in ini and "VSync = true" in ini
    # emulator jeszcze nieuruchamiany: pliku nie zakładamy (zrobi to sam, z kreatorem)
    pc = ingame.PCSX2(str(tmp_path / "PCSX2" / "pcsx2-qt.exe"))
    pc.apply_policy({"UI|ConfirmShutdown": "false"})
    assert not pc.policy_ini().exists()

    ra_home = tmp_path / "RetroArch"
    ra_home.mkdir()
    (ra_home / "retroarch.cfg").write_text('savefile_directory = "default"\n', encoding="utf-8")
    ra = ingame.RetroArch(str(ra_home / "retroarch.exe"))
    ra.apply_policy(installer.policy("retroarch"))
    ra.launch_args(tmp_path / "run", resume=False)
    lines = (tmp_path / "run" / "emustart_ra.cfg").read_text(encoding="utf-8").splitlines()
    assert lines[0] == 'video_vsync = "true"'
    # ustawienie sesji EmuStart wygrywa: ten sam klucz z serwera nie trafia do pliku (RetroArch
    # bierze pierwszą wartość)
    assert 'savestate_thumbnail_enable = "true"' in lines and 'savestate_thumbnail_enable = "false"' not in lines
    srvadmin.emu_policy_set(A(), {"family": "duckstation", "reset": True})
    assert emupolicy.effective(emu_server)["duckstation"] == emupolicy.DEFAULTS["duckstation"]


def test_all_retroarch_cores_on_server_and_alternatives_on_pc(emu_server, tmp_path, monkeypatch):
    """Serwer trzyma wszystkie rdzenie z buildbota; komputer dostaje dla systemu biblioteki
    zalecany rdzeń i wszystkie pasujące (wg plików .info) — np. bsnes obok snes9x."""
    with library.db() as c:
        c.execute("INSERT OR REPLACE INTO systems(es, display, rom_dir, games, scanned) VALUES('snes','SNES','snes',1,0)")
    RELEASES["snes9x"] = ("1.63", "snes9x.zip", _zip({"snes9x-x64.exe": b"SA"}))     # zalecany samodzielny
    RELEASES["core-snes9x"] = ("nightly-1", "s9x.zip", _zip({"snes9x_libretro.dll": b"S9X"}))
    RELEASES["core-bsnes"] = ("nightly-1", "bsnes.zip", _zip({"bsnes_libretro.dll": b"BSNES"}))
    RELEASES["core-fceumm"] = ("nightly-1", "fceumm.zip", _zip({"fceumm_libretro.dll": b"NES"}))
    monkeypatch.setattr(emucatalog, "all_cores", lambda: ["bsnes", "fceumm", "handy", "puae", "snes9x"])
    snes = 'database = "Nintendo - Super Nintendo Entertainment System|Nintendo - Super Nintendo Entertainment System Hacks"\n'
    info = _zip({"handy_libretro.info": 'database = "Atari - Lynx"\n', "puae_libretro.info": 'database = "Commodore - Amiga"\n',
                 "snes9x_libretro.info": snes, "bsnes_libretro.info": snes,
                 "fceumm_libretro.info": 'database = "Nintendo - Nintendo Entertainment System"\n'})
    orig = emucatalog.download

    def download(url, dest, progress=None, cancel=None):
        if url == emucatalog.INFO_URL:
            return Path(dest).write_bytes(info)
        return orig(url, dest, progress, cancel)
    monkeypatch.setattr(emucatalog, "download", download)
    r = emustore.update(emu_server)
    assert not r["errors"], r
    assert "core-fceumm" in r["updated"]                                       # spoza biblioteki też
    pub = emustore.public_index(emu_server)
    assert set(pub["systems"]["snes"]) == {"snes9x", "retroarch", "core-snes9x", "core-bsnes"}
    cfg = {"emu_root": str(tmp_path / "emu"), "systems": {}}
    st = _sync(cfg, ["snes"])
    assert st["errors"] == [], st
    cores = tmp_path / "emu" / "RetroArch" / "cores"
    assert (cores / "bsnes_libretro.dll").read_bytes() == b"BSNES" and (cores / "snes9x_libretro.dll").is_file()
    assert not (cores / "fceumm_libretro.dll").exists()                        # tylko dla systemów biblioteki


def test_retroarch_default_pad_and_auto_index(tmp_path):
    """Domyślnie: każdy zapis stanu z nowym numerem, LB — skróty: LB+A menu, LB+RB przyspieszenie,
    LB+Y zapis stanu, LB+↑/↓ slot, LB+R3 zrzut, LB+L3 nagrywanie (sterownik XInput)."""
    from emustart import emupolicy, ingame
    home = tmp_path / "RetroArch"
    home.mkdir()
    (home / "retroarch.cfg").write_text("", encoding="utf-8")
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    ra.apply_policy(emupolicy.DEFAULTS["retroarch"])
    ra.launch_args(tmp_path / "run", resume=False)
    lines = set((tmp_path / "run" / "emustart_ra.cfg").read_text(encoding="utf-8").splitlines())
    for line in ('savestate_auto_index = "true"', 'input_joypad_driver = "xinput"',
                 'input_menu_toggle_btn = "0"', 'input_enable_hotkey_btn = "4"',
                 'input_toggle_fast_forward_btn = "5"', 'input_save_state_btn = "3"'):
        assert line in lines, line


def test_retroarch_without_shader_for_system(tmp_path):
    """„Bez shadera” (czarny ekran przy niezgodnym shaderze): tylko na czas gry — linia w
    konfiguracji sesji, własne ustawienia shadera RetroArcha zostają."""
    from emustart import ingame
    home = tmp_path / "RetroArch"
    home.mkdir()
    (home / "retroarch.cfg").write_text('video_shader_enable = "true"\n', encoding="utf-8")
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    ra.launch_args(tmp_path / "run", resume=False)
    assert 'video_shader_enable = "false"' not in (tmp_path / "run" / "emustart_ra.cfg").read_text(encoding="utf-8")
    ra.shader_off = True
    ra.launch_args(tmp_path / "run2", resume=False)
    assert 'video_shader_enable = "false"' in (tmp_path / "run2" / "emustart_ra.cfg").read_text(encoding="utf-8")
    assert ra.after_session() == 0 or 'video_shader_enable = "true"' in (home / "retroarch.cfg").read_text(encoding="utf-8")


def test_ingame_reset_and_shader_commands(tmp_path, monkeypatch):
    """Menu w grze (A + Y): Reset gry i shader — komendy sieciowe RetroArcha."""
    from emustart import ingame
    ra = ingame.RetroArch(str(tmp_path / "retroarch.exe"))
    sent = []
    monkeypatch.setattr(ra, "_cmd", sent.append)
    assert ra.can_reset()
    ra.reset_game()
    ra.toggle_shader()
    assert sent == ["RESET", "SHADER_TOGGLE"]
    assert not ingame.DuckStation(str(tmp_path / "duckstation.exe")).can_reset()


def test_shader_toggle_from_emustart_menu_is_session_only(tmp_path, monkeypatch):
    """A + Y → Włącz / wyłącz shader: tylko do końca gry. Wcześniej RetroArch zapisywał zmianę
    przy wyjściu i EmuStart przenosił ją do retroarch.cfg (shader wyłączony na stałe)."""
    from emustart import ingame
    home = tmp_path / "RetroArch"
    home.mkdir()
    (home / "retroarch.cfg").write_text('video_shader_enable = "true"\nvideo_vsync = "true"\n', encoding="utf-8")
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    monkeypatch.setattr(ra, "_cmd", lambda c: None)
    args = ra.launch_args(tmp_path / "run", resume=False)
    sess = Path(args[args.index("--config") + 1])
    ra.toggle_shader()
    # RetroArch przy wyjściu zapisał kopię sesji: shader wyłączony (z menu EmuStart) i vsync (z menu RA)
    sess.write_text(sess.read_text(encoding="utf-8").replace('video_shader_enable = "true"', 'video_shader_enable = "false"')
                    .replace('video_vsync = "true"', 'video_vsync = "false"'), encoding="utf-8")
    ra.after_session()
    main = (home / "retroarch.cfg").read_text(encoding="utf-8")
    assert 'video_shader_enable = "true"' in main and 'video_vsync = "false"' in main


def test_first_run_without_wizard_with_bios(tmp_path, monkeypatch):
    """Świeżo zainstalowany PCSX2 / DuckStation: bez kreatora pierwszego uruchomienia i własnych
    aktualizacji, BIOS z serwera do folderu emulatora."""
    from emustart import ingame, libsync
    server = {"pcsx2/bios/ps2-0230a-20080220.bin": b"PS2", "pcsx2/bios/ps2-0230a-20080220.nvm": b"N",
              "scph5501.bin": b"PS1"}
    monkeypatch.setattr(libsync, "bios_list", lambda cfg, q: [r for r in server if
                                                             (r.startswith(q["dir"] + "/") if "dir" in q else "/" not in r)])

    def get(cfg, rel, dst):
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(server[rel])
        return True
    monkeypatch.setattr(libsync, "bios_from_server", get)
    pc = tmp_path / "PCSX2"
    pc.mkdir()
    (pc / "portable.ini").write_text("", encoding="utf-8")
    ingame.PCSX2(str(pc / "pcsx2-qt.exe")).first_run({})
    ini = (pc / "inis" / "PCSX2.ini").read_text(encoding="utf-8")
    assert "SetupWizardIncomplete = false" in ini and "SettingsVersion = 1" in ini
    assert "BIOS = ps2-0230a-20080220.bin" in ini and "CheckAtStartup = false" in ini
    assert (pc / "bios" / "ps2-0230a-20080220.bin").read_bytes() == b"PS2"
    # plik po kreatorze (jak u gracza): kreator „niedokończony”, BIOS pusty
    (pc / "inis" / "PCSX2.ini").write_text("[UI]\nSettingsVersion = 1\nSetupWizardIncomplete = true\n"
                                            "[Filenames]\nBIOS = \n", encoding="utf-8")
    ingame.PCSX2(str(pc / "pcsx2-qt.exe")).first_run({})
    ini = (pc / "inis" / "PCSX2.ini").read_text(encoding="utf-8")
    assert "SetupWizardIncomplete = false" in ini and "BIOS = ps2-0230a-20080220.bin" in ini

    ds = tmp_path / "DuckStation"
    ds.mkdir()
    (ds / "portable.txt").write_text("", encoding="utf-8")
    ingame.DuckStation(str(ds / "duckstation-qt-x64-ReleaseLTCG.exe")).first_run({})
    s = (ds / "settings.ini").read_text(encoding="utf-8")
    assert "SetupWizardIncomplete = false" in s and (ds / "bios" / "scph5501.bin").is_file()


def test_server_lists_bios_folder(emu_server, tmp_path, monkeypatch):
    import http.client
    import json
    bios = tmp_path / "bios"
    (bios / "pcsx2" / "bios").mkdir(parents=True)
    (bios / "pcsx2" / "bios" / "ps2-0230a-20080220.bin").write_bytes(b"PS2")
    emu_server["bios_dir"] = str(bios)
    host, port, _k = netsrc.endpoint({})
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("GET", "/v1/bios?dir=pcsx2/bios", headers={"Authorization": "Bearer tok"})
    assert json.loads(c.getresponse().read())["files"] == ["pcsx2/bios/ps2-0230a-20080220.bin"]
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("GET", "/v1/bios?dir=../..", headers={"Authorization": "Bearer tok"})
    assert c.getresponse().status == 400


def test_pad_shortcuts_lb(monkeypatch):
    """LB + przycisk → skrót klawiszowy emulatora (Dolphin: slot wybiera EmuStart, Shift+F1…F8)."""
    from emustart import hotkey, ingame, winutil
    sent = []
    monkeypatch.setattr(winutil, "send_keys", lambda k, hold=0.08: sent.append(k))
    monkeypatch.setattr(hotkey.time, "sleep", lambda s: None)
    d = ingame.Dolphin.__new__(ingame.Dolphin)
    d._hotkey = lambda name, default: default
    m = hotkey.GameMenu.__new__(hotkey.GameMenu)
    m.adapter, m.slot = d, 1
    m._shortcut("slot_up")
    m._shortcut("slot_up")
    m._shortcut("save_here")
    m._shortcut("save_next")
    m._shortcut("screenshot")
    m._shortcut("record")
    assert sent == ["SHIFT+F3", "SHIFT+F4", "F9", ""] and m.slot == 4
    p = ingame.PCSX2.__new__(ingame.PCSX2)
    p._hk = lambda name, default="": default
    p.save_key = lambda: "F1"
    m.adapter, sent[:] = p, []
    m._shortcut("save_next")
    m._shortcut("slot_down")
    m._shortcut("menu")
    assert sent == ["F2", "F1", "SHIFT+F2", "ESCAPE"]


def test_retroarch_pad_hotkeys_default_once(tmp_path):
    """Skróty LB + … to domyślna konfiguracja: raz wpisane do retroarch.cfg (inne skróty na tych
    przyciskach — np. wyjście na A — wyłączone); potem zmiana gracza w retroarch.cfg zostaje."""
    from emustart import ingame
    home = tmp_path / "RetroArch"
    home.mkdir()
    (home / "retroarch.cfg").write_text('input_exit_emulator_btn = "0"' + chr(10) + 'input_rewind_btn = "6"' + chr(10) +
                                        'input_player1_a_btn = "0"' + chr(10), encoding="utf-8")
    ra = ingame.RetroArch(str(home / "retroarch.exe"))
    ra.launch_args(tmp_path / "run", resume=False)
    cfg = ingame._cfg_map(home / "retroarch.cfg")
    assert cfg["input_enable_hotkey_btn"] == '"4"' and cfg["input_menu_toggle_btn"] == '"0"'
    assert cfg["input_state_slot_increase_btn"] == '"h0up"' and cfg["input_screenshot_btn"] == '"9"'
    assert cfg["input_exit_emulator_btn"] == '"nul"'
    assert cfg["input_rewind_btn"] == '"6"' and cfg["input_player1_a_btn"] == '"0"'
    ingame.ini_set(home / "retroarch.cfg", {(None, "input_enable_hotkey_btn"): '"7"'})   # gracz zmienił
    ra.launch_args(tmp_path / "run2", resume=False)
    assert ingame._cfg_map(home / "retroarch.cfg")["input_enable_hotkey_btn"] == '"7"'
