"""EmuStart Server (emustart/srvapi.py) i EmuStart na PC (emustart/api.py) — podział ról."""

from pathlib import Path

from emustart import api, config, library, srvapi


def _write(p: Path, data: bytes = b"x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_server_scans_folders_and_serves_details(tmp_path):
    roms = tmp_path / "roms"
    _write(roms / "snes" / "Super Mario World (USA).sfc")
    cfg = config.load()
    cfg["rom_roots"] = [str(roms)]
    config.save(cfg)
    s = srvapi.ServerApi(start_background=False)
    assert s.rescan()["ok"]
    import time
    end = time.monotonic() + 20
    while s.scan_status()["running"] and time.monotonic() < end:
        time.sleep(0.05)
    assert s.scan_status()["result"]["ok"]
    assert [x["es"] for x in s.list_systems()] == ["snes"]
    g = s.list_games("snes")[0]
    d = s.game_detail(g["id"])
    assert d["title"] == "Super Mario World" and d["rel"].endswith(".sfc") and d["variants"] == []


def test_pc_client_has_no_server_jobs():
    """EmuStart na PC nie skanuje folderów i nie pobiera grafik całej biblioteki — robi to
    serwer (panel serwera); PC bierze bibliotekę z serwera."""
    for name in ("art_overview", "art_start", "art_cancel", "art_status", "launchbox_update",
                 "launchbox_status", "import_pylinks_keys", "system_rescan", "_arcade_meta",
                 "_rescan_systems"):
        assert not hasattr(api.Api, name), name
    for name in ("art_overview", "art_start", "rescan", "list_games", "game_detail", "_arcade_meta"):
        assert hasattr(srvapi.ServerApi, name), name


def test_pc_refresh_needs_server(tmp_path):
    a = api.Api.__new__(api.Api)
    a._cfg = config.load()
    a._scan = {"running": False, "text": "", "done": 0, "total": 0, "result": None}
    assert a.rescan()["ok"]
    import time
    end = time.monotonic() + 5
    while a._scan["running"] and time.monotonic() < end:
        time.sleep(0.02)
    assert not a._scan["result"]["ok"] and "zaloguj" in a._scan["result"]["reason"]
    assert library.systems_summary() == []


def test_server_edits_game_art_meta_and_system_look(tmp_path, monkeypatch):
    """Panel serwera: grafiki i opis jednej gry, logo/nazwa systemu — urządzenia biorą je
    z serwera (wcześniej ustawiało się je na PC)."""
    from emustart import art_sources, metadata, scanner
    roms = tmp_path / "roms"
    _write(roms / "snes" / "Super Mario World (USA).sfc")
    _write(roms / "snes" / "Zelda 100% (USA).sfc")
    scanner.scan_system("snes", roms / "snes")
    s = srvapi.ServerApi(start_background=False)
    assert [g["name"] for g in s.games_search("mario")] == ["Super Mario World (USA)"]
    assert [g["name"] for g in s.games_search("100%")] == ["Zelda 100% (USA)"]   # % dosłownie
    assert s.games_search("_") == []                                        # _ dosłownie, nie „dowolny znak”
    gid = s.games_search("mario", "snes")[0]["id"]
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    monkeypatch.setattr(art_sources, "fetch", lambda url, **k: png)
    assert s.art_choose(gid, "box", "https://x/box.png")["ok"]
    e = s.game_edit(gid)
    assert e["ok"] and e["box"].startswith("/media/snes/") and not e["snap"]
    assert s.art_clear(gid, "box")["ok"] and not s.game_edit(gid)["box"]
    assert not s.art_choose(gid, "zle", "https://x")["ok"]
    assert s.meta_save(gid, {"title": "Mario", "description": "Opis", "hack": "x"})["ok"]
    assert metadata.get(gid)["_edits"] == {"title": "Mario", "description": "Opis"}
    assert s.system_set("snes", {"name": "Super Nintendo", "glow": True, "enabled": False})["ok"]
    look = {x["es"]: x for x in s.systems_look()}["snes"]
    assert look["display"] == "Super Nintendo" and look["glow"] and not look["enabled"]
    assert s.system_set("snes", {"name": ""})["ok"]                          # pusta — domyślna nazwa
    assert {x["es"]: x for x in s.systems_look()}["snes"]["display"] == look["default_name"]
    assert not s.system_set("nieznany", {"name": "x"})["ok"]
    assert s.system_logo_choose("snes", "none")["url"] == ""                  # bez logo


def test_recommended_emulator_set_in_panel(tmp_path):
    """Zalecany emulator i rdzeń systemu ustawia panel serwera (wcześniej wysyłał je PC)."""
    from emustart import libsync, remote, scanner
    _write(tmp_path / "roms" / "snes" / "A (USA).sfc")
    scanner.scan_system("snes", tmp_path / "roms" / "snes")
    s = srvapi.ServerApi(start_background=False)
    assert s.system_set("snes", {"emu_rec": "retroarch", "core_rec": "bsnes"})["ok"]
    assert not s.system_set("snes", {"emu_rec": "inny"})["ok"]
    assert not s.system_set("snes", {"core_rec": "../x"})["ok"]
    look = {x["es"]: x for x in s.systems_look()}["snes"]
    assert look["emu_rec"] == "retroarch" and look["core_rec"] == "bsnes" and look["core_default"]
    remote.API = s
    try:
        rec = {x["es"]: x for x in libsync.server_systems()}["snes"]
        assert rec["emu_rec"] == "retroarch" and rec["core"] == "bsnes"
    finally:
        remote.API = None


def test_server_version_grows_with_art_and_description_changes(tmp_path, monkeypatch):
    """Zmiana grafiki albo opisu podbija wersję systemu — urządzenia pobierają go od nowa;
    biblioteka dla PC niesie czasy grafik i opisy."""
    from emustart import art_sources, libsync, remote, scanner
    _write(tmp_path / "roms" / "snes" / "A (USA).sfc")
    scanner.scan_system("snes", tmp_path / "roms" / "snes")
    s = srvapi.ServerApi(start_background=False)
    remote.API = s
    try:
        v1 = libsync.server_systems()[0]["scanned"]
        gid = s.games_search("A")[0]["id"]
        png = bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]) + b"0" * 64
        monkeypatch.setattr(art_sources, "fetch", lambda url, **k: png)
        import time
        time.sleep(0.01)
        assert s.art_choose(gid, "box", "https://x/a.png")["ok"]
        v2 = libsync.server_systems()[0]["scanned"]
        assert v2 > v1
        s.meta_save(gid, {"description": "Opis"})
        assert libsync.server_systems()[0]["scanned"] >= v2
        row = libsync.server_games("snes")[0]
        assert row["art_m"].get("box") and row["meta"]["edits"] == {"description": "Opis"}
    finally:
        remote.API = None


def test_merge_platforms_with_dedupe(tmp_path):
    """Dwa foldery tej samej platformy (x360 i xbox360): scalenie — jedna lista, ta sama gra
    raz, platforma scalona znika razem ze swoimi grami; rozdzielenie przywraca obie."""
    import time
    roms = tmp_path / "roms"
    _write(roms / "xbox360" / "Halo 3 (USA).iso")
    _write(roms / "x360" / "Halo 3 (USA).iso")
    _write(roms / "x360" / "Gears (USA).iso")
    cfg = config.load()
    cfg["rom_roots"] = [str(roms)]
    config.save(cfg)
    s = srvapi.ServerApi(start_background=False)

    def scan():
        assert s.rescan()["ok"] or True
        end = time.monotonic() + 20
        while s.scan_status()["running"] and time.monotonic() < end:
            time.sleep(0.05)
    scan()
    look = {x["es"]: x for x in s.systems_look()}
    assert {"x360", "xbox360"} <= set(look) and look["x360"]["dirs"][0].endswith("x360")
    assert not s.system_merge("x360", "x360")["ok"] and not s.system_merge("nie-ma", "xbox360")["ok"]
    assert s.system_merge("x360", "xbox360")["ok"]
    scan()
    look = {x["es"]: x for x in s.systems_look()}
    assert "x360" not in look and look["xbox360"]["merged"] == ["x360"] and len(look["xbox360"]["dirs"]) == 2
    names = sorted(g["name"] for g in s.list_games("xbox360"))
    assert names == ["Gears (USA)", "Halo 3 (USA)"]                       # Halo raz
    assert library.db().execute("SELECT COUNT(*) FROM games WHERE es='x360'").fetchone()[0] == 0
    assert not s.system_merge("xbox360", "x360")["ok"]                      # scalona nie może być celem
    assert s.system_merge("x360", "")["ok"]
    scan()
    assert {"x360", "xbox360"} <= {x["es"] for x in s.systems_look()}


def test_media_store_one_file_per_system_and_migration(tmp_path):
    """Grafiki w jednym pliku na system (media/<system>.media) zamiast tysięcy plików;
    stare pliki przenoszone i usuwane dopiero po odczycie kontrolnym."""
    import os as _os
    from emustart import art, mediastore, paths
    old = paths.MEDIA / "snes"
    old.mkdir(parents=True)
    (old / "A (USA).box.png").write_bytes(b"okladka A")
    (old / "B (USA).snap.png").write_bytes(b"zrzut B")
    _os.utime(old / "A (USA).box.png", (1000, 1000))
    mediastore.put("snes", "B (USA).snap.png", b"nowszy zrzut", 9e9)   # nowsza wersja już w bazie
    assert mediastore.migrate() == 2
    assert not old.exists() and (paths.MEDIA / "snes.media").is_file()
    assert mediastore.get("snes", "A (USA).box.png") == (b"okladka A", 1000.0)
    assert mediastore.get("snes", "B (USA).snap.png")[0] == b"nowszy zrzut"
    assert art.media_url("snes", "A (USA)", "box").endswith("?v=1000")
    assert mediastore.rename("snes", "A (USA).box.png", "A2.box.png") and art.has("snes", "A2", "box")
    assert art.delete("snes", "A2", "box") and not art.has("snes", "A2", "box")
    assert mediastore.file_name("snes/A.box.png") == ("snes", "A.box.png")
    assert mediastore.file_name("_systems/snes.png") == ("_systems", "snes.png")
    assert mediastore.file_name("_inne/x.png") is None and mediastore.file_name("../x/y") is None
    (paths.MEDIA / "_systems").mkdir()
    (paths.MEDIA / "_systems" / "psx.custom.svg").write_bytes(b"<svg/>")
    (paths.MEDIA / "_systems" / "gb.none").write_bytes(b"")
    assert mediastore.migrate() == 2 and not (paths.MEDIA / "_systems").exists()
    assert mediastore.get("_systems", "psx.custom.svg")[0] == b"<svg/>" and mediastore.exists("_systems", "gb.none")   # „brak logo” też


def test_bundled_platform_logos_in_one_file(tmp_path, monkeypatch):
    """Wbudowane logo platform w programie — jeden plik assets/systems.media (zamiast 40 PNG
    w _internal); adresy /assets/systems/<kod>.png bez zmian."""
    import sys as _sys
    import urllib.request
    from emustart import logos, mediastore, paths, server
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.pack_assets import build
    src = tmp_path / "src"
    src.mkdir()
    (src / "PSX.png").write_bytes(b"\x89PNG logo psx")
    assets = tmp_path / "assets"
    assert build(src, assets / "systems.media") == 1
    monkeypatch.setattr(paths, "ASSETS", assets)
    monkeypatch.setitem(server.ROUTES, "/assets/", assets)
    monkeypatch.setattr(mediastore, "_bundled", {})
    assert logos.bundled("PSX") == "/assets/systems/PSX.png" and logos.bundled("NIEMA") == ""
    assert not (assets / "systems").exists()                          # bez osobnych plików
    base = server.start(0)
    assert urllib.request.urlopen(base + "/assets/systems/PSX.png", timeout=10).read() == b"\x89PNG logo psx"


def test_install_locks_program_folder_for_normal_users(monkeypatch, tmp_path):
    """--install: folder programu serwera tylko do odczytu dla zwykłych użytkowników (SYSTEM
    i administratorzy — pełne), żeby nikt nie podmienił DLL wykonywanej potem jako SYSTEM."""
    import subprocess
    from emustart import remote
    calls = []

    class R:
        returncode, stdout, stderr = 0, "", ""
    monkeypatch.setattr(subprocess, "run", lambda c, **k: calls.append(c) or R())
    remote.install_autostart(str(tmp_path / "EmuStartServer.exe"), 8740)
    acl = calls[0]
    assert acl[:3] == ["icacls", str(tmp_path), "/inheritance:r"]
    assert "*S-1-5-18:(OI)(CI)F" in acl and "*S-1-5-32-544:(OI)(CI)F" in acl and "*S-1-5-32-545:(OI)(CI)RX" in acl
    assert not any("S-1-5-11" in x for x in acl)                       # bez „Użytkownicy uwierzytelnieni: M”


def test_update_apply_records_panel_user_safely(monkeypatch):
    from emustart import srvadmin, updater

    class Job:
        info = {"version": "9"}

        def apply(self, argv, **k):
            return {"ok": False, "reason": "test"}
    monkeypatch.setitem(srvadmin._update, "job", Job())
    srvadmin.update_apply(None, {"panel_user": r"EMUSTATION\Jezus"})
    assert (updater.update_dir() / "panel-user.txt").read_text(encoding="utf-8") == r"EMUSTATION\Jezus"
    (updater.update_dir() / "panel-user.txt").unlink()
    srvadmin.update_apply(None, {"panel_user": "a\" & calc.exe"})
    assert not (updater.update_dir() / "panel-user.txt").exists()


def test_server_creates_profiles_folder_and_restores_approved_accounts(tmp_path, monkeypatch):
    r"""Świeży serwer bez ustawionego folderu profili: <serwer>\profiles zakładany przy starcie;
    konto zatwierdzone, gdy folderu nie było, dostaje swój folder (lista kont, „Kto gra?”)."""
    from emustart import devices, paths, remote
    monkeypatch.setattr(paths, "SERVER", tmp_path / "server")
    monkeypatch.setattr(remote, "NAS_ROOT", None)
    monkeypatch.setattr(devices, "accounts_in_use", lambda: {"Jezus", "../zle"})
    remote.init_nas({"profiles_nas": ""})
    assert remote.NAS_ROOT == tmp_path / "server" / "profiles"
    assert remote.profile_names() == ["Jezus"]
    assert not (tmp_path / "zle").exists()


def test_client_always_lists_its_own_account(monkeypatch):
    """Serwer nie podał listy kont — konto, którym urządzenie jest zalogowane, i tak jest
    na liście „Kto gra?” (wcześniej tylko „Inny użytkownik”)."""
    from emustart import nasfs, profiles

    class Down:
        def profiles(self):
            raise OSError("serwer: lista profili 404")
    monkeypatch.setattr(nasfs, "backend", lambda cfg: Down())
    monkeypatch.setattr(profiles, "resolve_moves", lambda cfg: None)
    monkeypatch.setattr(profiles, "account_of_device", lambda cfg: "Jezus")
    profiles.import_from_nas({})
    assert any(p["nas_name"] == "Jezus" for p in profiles.all_profiles())
    lst = profiles.visible({}, profiles.all_profiles())
    assert [p["nas_name"] for p in lst] == ["Jezus"]


def test_logo_candidates_from_web_themes_without_local_files(monkeypatch):
    """Serwer nie ma paczki ani RetroArcha z motywami — propozycje z motywów w internecie
    (ES / ES-DE, ikony RetroArcha), sprawdzane naraz; wybór tylko z tych adresów."""
    from emustart import logos
    asked = []
    monkeypatch.setattr(logos, "_exists", lambda u: asked.append(u) or "carbon" in u or "/systematic/" in u)
    c = logos.candidates({}, "psx")
    assert [x["source"] for x in c if x["id"].startswith("url:")] == ["Carbon", "RetroArch (systematic)"]
    assert any("Sony%20-%20PlayStation.png" in u for u in asked)
    assert len(asked) == len(logos.ES_THEMES) * len(logos.theme_names("psx")) + len(logos.RA_THEMES)
    assert not logos.choose({}, "psx", "url:http://10.0.0.1/admin")              # dowolny adres — nie


def test_admin_pc_sends_pylinks_keys_to_server(monkeypatch):
    """Klucze PyLinks odszyfrowuje komputer administratora, serwer dostaje je połączeniem
    szyfrowanym i szyfruje swoim kluczem."""
    from emustart import art_sources, secure
    a = api.Api.__new__(api.Api)
    a._cfg = {}
    sent = {}
    monkeypatch.setattr(a, "_is_admin", lambda: True, raising=False)
    monkeypatch.setattr(art_sources, "import_pylinks_keys",
                        lambda path="": {"source": "x", "sgdb_key": secure.protect("SG"), "tgdb_key": secure.protect("TG"),
                                         "igdb_client_id": "", "igdb_client_secret": ""})
    monkeypatch.setattr(a, "_admin_call", lambda m, p, b=None: sent.update(m=m, p=p, b=b) or {"ok": True}, raising=False)
    r = a.art_keys_push()
    assert r["ok"] and sent == {"m": "PUT", "p": "art_keys", "b": {"sgdb_key": "SG", "tgdb_key": "TG"}}
    monkeypatch.setattr(a, "_is_admin", lambda: False, raising=False)
    assert not a.art_keys_push()["ok"]


def test_server_imports_art_keys_file_and_deletes_it(monkeypatch):
    """server\art_keys.json (jawne klucze) — przy starcie zaszyfrowane w ustawieniach, plik znika."""
    import json
    from emustart import art_sources, paths, srvadmin
    monkeypatch.setattr("emustart.config.save", lambda c: None)
    paths.SERVER.mkdir(parents=True, exist_ok=True)
    f = paths.SERVER / "art_keys.json"
    f.write_text(json.dumps({"sgdb_key": "jawny-klucz-sgdb-123", "tgdb_key": "TG", "inne": "x"}), encoding="utf-8")

    class A:
        _cfg = {}
    a = A()
    assert srvadmin.import_keys_file(a) == 2
    assert not f.exists()
    assert "jawny-klucz-sgdb-123" not in json.dumps(a._cfg)                    # zaszyfrowane
    assert art_sources.keys(a._cfg)["sgdb_key"] == "jawny-klucz-sgdb-123"
    assert srvadmin.import_keys_file(a) == 0


def test_library_delta_only_changed_games():
    """Zmiana grafiki / opisu jednej gry: urządzenia pobierają tylko ją, nie cały system
    (serwer pobierający grafiki podbijał wersję systemu — PC co 5 minut ściągał wszystko)."""
    import time
    from emustart import libsync
    with library.db() as c:
        c.execute("INSERT OR REPLACE INTO systems(es, display, rom_dir, games, scanned) VALUES('nes','NES','nes',2,100)")
        for rel in ("A.nes", "B.nes"):
            c.execute("INSERT INTO games(es, rel, name, title, files, size, seen) VALUES('nes',?,?,?,'[]',1,1)",
                      (rel, rel[:-4], rel[:-4]))
    ids = {r["rel"]: r["id"] for r in library.db().execute("SELECT id, rel FROM games WHERE es='nes'")}
    before = time.time()
    full0 = next(s for s in libsync.server_systems() if s["es"] == "nes")["full"]
    library.bump_rev_game(ids["B.nes"])
    s = next(s for s in libsync.server_systems() if s["es"] == "nes")
    assert s["scanned"] > before and s["full"] == full0                  # zmiana, ale nie pełna
    assert [g["rel"] for g in libsync.server_games("nes", before)] == ["B.nes"]
    assert len(libsync.server_games("nes")) == 2
    library.bump_rev("nes")                                              # zmiana całego systemu
    assert next(s for s in libsync.server_systems() if s["es"] == "nes")["full"] > full0
