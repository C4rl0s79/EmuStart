"""Tłumaczenia jako wersje gry: rozpoznanie, skan (schowane pod oryginałem),
wybór wersji w profilu, emulator oryginału."""

from __future__ import annotations

from pathlib import Path

import pytest

from emustart import api as api_mod, library, paths, profiles, scanner, translations


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "data")
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "data" / "lib.sqlite")
    library._local.con = None
    yield tmp_path
    library._local.con = None


def _write(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * 100)


def test_names():
    assert translations.base_title("Seiken Densetsu 3 (Japan) [T-En by Neill Corlett]") == "seiken densetsu 3"
    assert translations.lang_of("Mother 3 (Japan) [T+Eng v1.3]") == "en"
    assert translations.lang_of("Gra (Japan)", "[T-Pl] Kolekcja") == "pl"
    assert translations.lang_of("Gra (Japan)") == ""
    assert translations.label("Gra (Japan) [T-En by X]", "en") == "[T-En by X]"
    assert translations.label("Gra (Japan)", "pl") == "Tłumaczenie (PL)"
    assert translations.is_pool("[T-En] Collection") and translations.is_pool("Translations")
    assert translations.is_pool("T-Eng") and translations.lang_of("T-Eng") == "en" and translations.is_pool("t+pol")
    assert not translations.is_pool("Tetris") and not translations.is_pool("Atari - ST")
    assert not translations.is_pool("Nintendo - Super Nintendo Entertainment System")
    assert translations.strip_pool_tag("Nintendo - Super Nintendo Entertainment System [T-En]") == \
        "Nintendo - Super Nintendo Entertainment System"


def test_scan_pool_in_games_folder(env):
    roms = env / "roms"
    _write(roms / "snes" / "Seiken Densetsu 3 (Japan).sfc")
    _write(roms / "snes" / "Chrono Trigger (USA).sfc")
    pool = roms / "[T-En] Collection" / "snes"
    _write(pool / "Seiken Densetsu 3 (Japan) [T-En by Neill Corlett].sfc")
    _write(pool / "Tengai Makyou Zero (Japan) [T-En by DDS].sfc")       # bez oryginału
    _write(roms / "[T-Pl] Kolekcja" / "snes" / "Seiken Densetsu 3 (Japan).sfc")   # język z nazwy kolekcji

    found, _unknown = scanner.collect_systems({"rom_root": str(roms), "systems": {}})
    assert sorted(p.parent.name for p in found["snes"]) == ["[T-En] Collection", "[T-Pl] Kolekcja", "roms"]
    scanner.scan_system("snes", found["snes"])
    rows = {(Path(r["src"]).name, r["rel"].replace("\\", "/")): dict(r) for r in library.db().execute("SELECT * FROM games")}
    org = rows[("snes", "Seiken Densetsu 3 (Japan).sfc")]
    en = rows[("[T-En] Collection", "snes/Seiken Densetsu 3 (Japan) [T-En by Neill Corlett].sfc")]
    zero = rows[("[T-En] Collection", "snes/Tengai Makyou Zero (Japan) [T-En by DDS].sfc")]
    assert org["trans"] == "" and org["hidden"] == 0
    assert en["trans"] == "en" and en["hidden"] == translations.HIDDEN and en["tbase"] == org["tbase"]
    assert zero["trans"] == "en" and zero["hidden"] == 0                 # widoczna, bo nie ma oryginału
    # ta sama nazwa pliku co oryginał — osobna wersja (język z nazwy kolekcji)
    pl = rows[("[T-Pl] Kolekcja", "snes/Seiken Densetsu 3 (Japan).sfc")]
    assert pl["trans"] == "pl" and pl["hidden"] == translations.HIDDEN
    listed = [g["name"] for g in library.list_games("snes", 1, False)]
    assert "Seiken Densetsu 3 (Japan) [T-En by Neill Corlett]" not in listed
    assert "Tengai Makyou Zero (Japan) [T-En by DDS]" in listed


def test_system_folder_with_tag(env):
    roms = env / "roms"
    _write(roms / "Nintendo - Super Nintendo Entertainment System" / "Gra (Japan).sfc")
    _write(roms / "Nintendo - Super Nintendo Entertainment System [T-En]" / "Gra (Japan) (Hack).sfc")
    found, _u = scanner.collect_systems({"rom_root": str(roms), "systems": {}})
    assert len(found["snes"]) == 2
    scanner.scan_system("snes", found["snes"])
    tr = library.db().execute("SELECT * FROM games WHERE trans!=''").fetchone()
    assert tr["trans"] == "en" and tr["hidden"] == translations.HIDDEN


def test_variant_choice_and_emulator(env, monkeypatch):
    roms = env / "roms"
    _write(roms / "snes" / "Mother 3 (Japan).sfc")
    pool = roms / "Translations" / "snes"
    _write(pool / "Mother 3 (Japan) [T-En by Tomato].sfc")
    _write(pool / "Mother 3 (Japan) [T-Pl by Ktoś].sfc")
    found, _u = scanner.collect_systems({"rom_root": str(roms), "systems": {}})
    scanner.scan_system("snes", found["snes"])
    store = {}
    monkeypatch.setattr(profiles, "json_get", lambda cfg, pid, name: store.get((pid, name)))
    monkeypatch.setattr(profiles, "json_set", lambda cfg, pid, name, data: store.__setitem__((pid, name), data))
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._profile = {}, 1
    org_id = library.db().execute("SELECT id FROM games WHERE trans=''").fetchone()["id"]
    g = library.game(org_id)
    vs = a._variants(g)
    assert [(v["lang"], v["label"]) for v in vs] == [("en", "[T-En by Tomato]"), ("pl", "[T-Pl by Ktoś]")]
    assert a._variant_choice(g) is None
    pl = vs[1]
    assert a.set_variant(org_id, pl["id"])["ok"]
    assert store[(1, "variants.json")] == {"snes/Mother 3 (Japan).sfc": "snes/Mother 3 (Japan) [T-Pl by Ktoś].sfc"}
    assert a._variant_choice(g)["id"] == pl["id"]
    a._profile = 2                                    # inny gracz — oryginał
    assert a._variant_choice(g) is None
    a._profile = 1
    assert not a.set_variant(org_id, 999999)["ok"]
    assert a.set_variant(org_id, 0)["ok"] and a._variant_choice(g) is None
    # tłumaczenie bez własnego emulatora bierze emulator wybrany dla oryginału
    with library.db() as c:
        c.execute("INSERT INTO game_emu VALUES(?,?,?,?)", (org_id, "bsnes", "C:/ra/retroarch.exe", "-L bsnes"))
    assert library.game_emu(library.game(pl["id"]))["label"] == "bsnes"


def test_bare_t_eng_folder_same_names(env):
    """Folder „T-Eng” w folderze z grami albo jako osobny folder z grami; pliki o tych
    samych nazwach co oryginały."""
    roms, teng = env / "roms", env / "T-Eng"
    _write(roms / "snes" / "Mother 3 (Japan).sfc")
    _write(roms / "T-Eng" / "snes" / "Mother 3 (Japan).sfc")
    _write(teng / "Nintendo - Super Nintendo Entertainment System" / "Mother 3 (Japan).sfc")
    found, unknown = scanner.collect_systems({"rom_roots": [str(roms), str(teng)], "systems": {}})
    assert not unknown and len(found["snes"]) == 3
    scanner.scan_system("snes", found["snes"])
    rows = [dict(r) for r in library.db().execute("SELECT * FROM games ORDER BY trans")]
    assert [r["trans"] for r in rows] == ["", "en", "en"]
    assert all(r["hidden"] == translations.HIDDEN for r in rows[1:])
    listed = library.list_games("snes", 1, False)
    assert [g["name"] for g in listed] == ["Mother 3 (Japan)"]
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._profile = {}, 1
    assert len(a._variants(library.game(rows[0]["id"]))) == 2


def test_romvault_t_en_collection_layout(env):
    """Układ z RomVaulta: ROMS\\[T-En]Collection\\<System> [T-En] Collection\\ plus foldery
    bez tagu (Speed Hacks, MSU1) — wersje gry z etykietą folderu."""
    roms = env / "ROMS"
    _write(roms / "Nintendo - Super Nintendo Entertainment System" / "Bahamut Lagoon (Japan).sfc")
    _write(roms / "Nintendo - Super Nintendo Entertainment System" / "Gradius III (USA).sfc")
    _write(roms / "Sega - Mega Drive - Genesis" / "Sonic CD (USA).md")
    pool = roms / "[T-En]Collection"
    _write(pool / "Nintendo - Super Famicom [T-En] Collection" / "Bahamut Lagoon (Japan) [T-En by DeJap v1.2].sfc")
    _write(pool / "Nintendo - Super Famicom - Speed Hacks" / "Gradius III (USA) (Speed Hack).sfc")
    _write(pool / "Sega - Mega Drive - MSU-MD" / "Sonic CD (USA).md")
    _write(pool / "NEC - PC-6001 [T-En] Collection" / "Gra.zip")                 # system nieznany
    found, unknown = scanner.collect_systems({"rom_root": str(roms), "systems": {}})
    assert [d.name for d in unknown] == ["NEC - PC-6001 [T-En] Collection"]
    assert len(found["snes"]) == 3 and len(found["megadrive"]) == 2
    for es in ("snes", "megadrive"):
        scanner.scan_system(es, found[es])
    rows = {r["name"]: dict(r) for r in library.db().execute("SELECT * FROM games")}
    assert rows["Bahamut Lagoon (Japan) [T-En by DeJap v1.2]"]["trans"] == "en"
    assert rows["Gradius III (USA) (Speed Hack)"]["trans"] == "+Speed Hacks"
    assert all(r["hidden"] == translations.HIDDEN for r in rows.values() if r["trans"])
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._profile = {}, 1
    md = library.db().execute("SELECT * FROM games WHERE es='megadrive' AND trans=''").fetchone()
    assert [v["label"] for v in a._variants(library.game(md["id"]))] == ["MSU-MD"]
    assert [g["name"] for g in library.list_games("snes", 1, False)] == ["Bahamut Lagoon (Japan)", "Gradius III (USA)"]


def test_several_translations_same_game(env):
    roms = env / "roms"
    _write(roms / "snes" / "Mother 3 (Japan).sfc")
    pool = roms / "T-Eng" / "snes"
    _write(pool / "Mother 3 (Japan) [T-En by Tomato v1.3].sfc")
    _write(pool / "Mother 3 (Japan) [T-En by Other v1.0].sfc")
    _write(pool / "Mother 3 (Japan) (Rev 1).sfc")
    _write(pool / "Mother 3 (Japan) (Rev 2).sfc")
    found, _u = scanner.collect_systems({"rom_root": str(roms), "systems": {}})
    scanner.scan_system("snes", found["snes"])
    a = api_mod.Api.__new__(api_mod.Api)
    a._cfg, a._profile = {}, 1
    org = library.db().execute("SELECT id FROM games WHERE trans=''").fetchone()["id"]
    labels = sorted(v["label"] for v in a._variants(library.game(org)))
    assert labels == ["Tłumaczenie (EN) · (Japan) (Rev 1)", "Tłumaczenie (EN) · (Japan) (Rev 2)",
                      "[T-En by Other v1.0]", "[T-En by Tomato v1.3]"]
