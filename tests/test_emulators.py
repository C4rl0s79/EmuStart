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
    # spoza manifestu serwer nic nie wyda
    assert emustore.file_path(emu_server, "duckstation", "v1", "../index.json") is None
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


def test_pack_refuses_files_outside_manifest(emu_server):
    import http.client
    import json
    emustore.update(emu_server)
    host, port, _k = netsrc.endpoint({})
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("POST", "/v1/emulators/pack", body=json.dumps(
        {"item": "duckstation", "version": "v1", "files": ["../../index.json", "settings.ini"]}).encode(),
        headers={"Authorization": "Bearer tok", "Content-Type": "application/json"})
    r = c.getresponse()
    data = r.read()
    assert r.status == 200
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.namelist() == ["settings.ini"]
    c = http.client.HTTPConnection(host, port, timeout=10)
    c.request("POST", "/v1/emulators/pack", body=b'{"item": "duckstation", "version": "v1", "files": "*"}',
              headers={"Content-Type": "application/json"})
    assert c.getresponse().status == 401                                         # bez klucza — nic


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
    # ustawienie sesji EmuStart stoi niżej — wygrywa (RetroArch bierze ostatnią wartość)
    assert lines.index('savestate_thumbnail_enable = "true"') > lines.index('savestate_thumbnail_enable = "false"')
    srvadmin.emu_policy_set(A(), {"family": "duckstation", "reset": True})
    assert emupolicy.effective(emu_server)["duckstation"] == emupolicy.DEFAULTS["duckstation"]
