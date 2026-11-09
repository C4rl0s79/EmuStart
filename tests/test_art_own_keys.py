"""Własne klucze użytkownika do IGDB / TheGamesDB: zapytania wprost do usług, tylko z podanych kluczy."""

from __future__ import annotations

from emustart import art_sources as a


def test_own_candidates_use_only_given_keys(monkeypatch):
    seen = []
    monkeypatch.setattr(a.Tgdb, "gallery", lambda self, t, p, k: seen.append(("tgdb", self.key, p)) or ["https://cdn.thegamesdb.net/b.jpg"])
    monkeypatch.setattr(a.Igdb, "gallery", lambda self, t, p, k: seen.append(("igdb", self.cid, p)) or ["https://images.igdb.com/c.jpg"])
    monkeypatch.setattr(a.Tgdb, "remaining", None)
    got = a.own_candidates({"tgdb_key": "T", "igdb_client_id": "I", "igdb_client_secret": "S"}, "", "Doom", "box")
    assert [c["source"] for c in got] == ["TheGamesDB", "IGDB"]
    assert seen == [("tgdb", "T", ""), ("igdb", "I", "")]


def test_no_keys_no_requests(monkeypatch):
    def boom(*_a):
        raise AssertionError("zapytanie bez klucza")
    monkeypatch.setattr(a, "fetch", boom)
    assert a.own_candidates({}, "", "Doom", "box") == []
    assert a.own_candidates({"tgdb_key": "T"}, "", "Doom", "icon") == []


def test_api_method_works_without_a_game(monkeypatch):
    from emustart.api import Api
    monkeypatch.setattr(a.Tgdb, "remaining", None)
    monkeypatch.setattr(a.Tgdb, "gallery", lambda self, t, p, k: ["https://cdn.thegamesdb.net/s.jpg"])
    r = Api.art_own_candidates(object(), 0, "Doom", "snap", {"tgdb_key": "T"})
    assert r["ok"] and r["options"] == [{"url": "https://cdn.thegamesdb.net/s.jpg", "source": "TheGamesDB"}]


def test_tgdb_gallery_lists_every_image_of_the_kind(monkeypatch):
    t = a.Tgdb("K")
    monkeypatch.setattr(a.Tgdb, "remaining", None)
    monkeypatch.setattr(t, "game", lambda title, plat, fields="": {"id": 7})
    monkeypatch.setattr(t, "_get", lambda url: {"data": {"base_url": {"original": "https://cdn.thegamesdb.net/o/"}, "images": {"7": [
        {"type": "screenshot", "filename": "s1.jpg"}, {"type": "fanart", "filename": "f1.jpg"}, {"type": "screenshot", "filename": "s2.jpg"},
        {"type": "boxart", "side": "front", "filename": "b.jpg"}, {"type": "boxart", "side": "back", "filename": "bb.jpg"},
        {"type": "clearlogo", "filename": "l.png"}]}}})
    base = "https://cdn.thegamesdb.net/o/"
    assert t.gallery("X", "", "snap") == [base + "f1.jpg", base + "s1.jpg", base + "s2.jpg"]
    assert t.gallery("X", "", "box") == [base + "b.jpg"]
    assert t.gallery("X", "", "logo") == [base + "l.png"]
