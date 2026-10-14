"""Panel serwera → Konfiguracja: foldery sprawdzane tak, jak widzi je serwer (emustart/srvconfig.py)."""

from emustart import config, srvconfig


class FakeApi:
    def __init__(self, cfg):
        self._cfg = cfg
        self.rescans = 0

    def rescan(self):
        self.rescans += 1
        return {"ok": True}

    def _arcade_meta(self):
        pass


def test_get_shows_what_server_sees(tmp_path):
    roms = tmp_path / "roms"
    (roms / "psx").mkdir(parents=True)
    (roms / "cokolwiek").mkdir()
    bios = tmp_path / "bios"
    bios.mkdir()
    (bios / "scph5501.bin").write_bytes(b"x")
    cfg = config.load()
    cfg.update(rom_roots=[str(roms)], bios_dir=str(bios), emu_root=str(tmp_path / "nie-ma"))
    f = {x["key"]: x for x in srvconfig.get(cfg)["fields"]}
    assert f["rom_roots"]["status"][0] == {"ok": True, "text": "systemów: 1 (folderów: 2)"}
    assert f["bios_dir"]["status"]["text"] == "plików: 1"
    assert not f["emu_root"]["status"]["ok"]                       # serwer nie widzi folderu
    assert f["backup_dir"]["status"]["text"].startswith("domyślnie:")


def test_set_validates_and_applies(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    api = FakeApi(config.load())
    r = srvconfig.set_value(api, "rom_roots", [str(a), str(tmp_path / "brak")])
    assert not r["ok"] and "nie widzi" in r["reason"] and api.rescans == 0
    assert not srvconfig.set_value(api, "rom_roots", [])["ok"]     # bez folderu z grami — nie
    assert not srvconfig.set_value(api, "bios_dir", "wzgledna\\sciezka")["ok"]
    assert srvconfig.set_value(api, "rom_roots", [str(b), str(a), str(b)])["ok"]
    assert config.load()["rom_roots"] == [str(b), str(a)] and api.rescans == 1   # zapisane, skan
    assert srvconfig.set_value(api, "rom_roots", [str(b), str(a)])["ok"] and api.rescans == 1  # bez zmian
    assert srvconfig.set_value(api, "bios_dir", str(a))["ok"] and config.load()["bios_dir"] == str(a)
    assert srvconfig.set_value(api, "bios_dir", "")["ok"]          # opcjonalny — można wyczyścić
    assert not srvconfig.set_value(api, "nieznane", "x")["ok"]


def test_unc_leaves_local_paths():
    assert srvconfig.unc(r"C:\gry") == r"C:\gry"
    assert srvconfig.unc(r"\\NAS\udzial\gry") == r"\\NAS\udzial\gry"
