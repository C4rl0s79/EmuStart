"""Własne klucze użytkownika do IGDB / TheGamesDB: zapytania wprost do usług, tylko z podanych kluczy."""

from __future__ import annotations

from emustart import art_sources as a


def test_own_candidates_use_only_given_keys(monkeypatch):
    seen = []
    monkeypatch.setattr(a.Tgdb, "images", lambda self, t, p: seen.append(("tgdb", self.key, p)) or {"box": "https://cdn.thegamesdb.net/b.jpg"})
    monkeypatch.setattr(a.Igdb, "images", lambda self, t, p: seen.append(("igdb", self.cid, p)) or {"box": "https://images.igdb.com/c.jpg"})
    monkeypatch.setattr(a.Tgdb, "remaining", None)
    got = a.own_candidates({"tgdb_key": "T", "igdb_client_id": "I", "igdb_client_secret": "S"}, "", "Doom", "box")
    assert [c["source"] for c in got] == ["TheGamesDB", "IGDB"]
    assert seen == [("tgdb", "T", ""), ("igdb", "I", "")]


def test_no_keys_no_requests_and_logo_is_not_offered(monkeypatch):
    def boom(*_a):
        raise AssertionError("zapytanie bez klucza")
    monkeypatch.setattr(a, "fetch", boom)
    assert a.own_candidates({}, "", "Doom", "box") == []
    assert a.own_candidates({"tgdb_key": "T"}, "", "Doom", "logo") == []


def test_api_method_works_without_a_game(monkeypatch):
    from emustart.api import Api
    monkeypatch.setattr(a.Tgdb, "remaining", None)
    monkeypatch.setattr(a.Tgdb, "images", lambda self, t, p: {"snap": "https://cdn.thegamesdb.net/s.jpg"})
    r = Api.art_own_candidates(object(), 0, "Doom", "snap", {"tgdb_key": "T"})
    assert r["ok"] and r["options"] == [{"url": "https://cdn.thegamesdb.net/s.jpg", "source": "TheGamesDB"}]
