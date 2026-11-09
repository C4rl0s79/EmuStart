"""Własne grafiki użytkownika w kliencie Windows i plik z kluczami źródeł grafik."""

from __future__ import annotations

import pytest

from emustart import art_sources, artsync, ownart, paths

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40
URL = "https://images.igdb.com/a.png"


@pytest.fixture()
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(art_sources, "fetch", lambda u, *a, **k: PNG)


def test_choice_overlays_default_and_clear_restores(data):
    rows = [{"name": "Doom", "box": "/media/snes/d.png", "snap": "", "logo": ""}]
    ownart.choose({}, 1, "snes", "Doom", "box", URL)
    ownart.apply(rows, "snes", 1)
    assert rows[0]["box"].startswith("/media/_moje/") and rows[0]["snap"] == ""
    other = [{"name": "Doom", "box": "/media/snes/d.png", "snap": "", "logo": ""}]
    ownart.apply(other, "snes", 2)                     # inny profil nie widzi wyboru
    assert other[0]["box"] == "/media/snes/d.png"
    ownart.clear(1, "snes", "Doom", "box")
    back = [{"name": "Doom", "box": "/media/snes/d.png", "snap": "", "logo": ""}]
    ownart.apply(back, "snes", 1)
    assert back[0]["box"] == "/media/snes/d.png"


def test_refuses_unknown_host_and_non_image(data, monkeypatch):
    with pytest.raises(ValueError):
        ownart.choose({}, 1, "snes", "Doom", "box", "https://zly.example/a.png")
    monkeypatch.setattr(art_sources, "fetch", lambda u, *a, **k: b"nie obraz")
    with pytest.raises(ValueError):
        ownart.choose({}, 1, "snes", "Doom", "box", URL)


def test_sync_sends_as_tv_choices_and_takes_newer(data, monkeypatch):
    ownart.choose({}, 1, "snes", "Doom", "box", URL)
    gk = ownart.game_key("snes", "Doom")
    sent = {}

    def fake(cfg, pid, items):
        sent["items"] = items
        return {"choices": {f"tv|{gk}|hero": {"choice": {"type": "web", "url": URL}, "at": 9e9},
                            f"tv|{gk}|grid": {"choice": None, "at": 1},        # starszy: nie rusza
                            f"desktop|{gk}|logo": {"choice": {"type": "web", "url": URL}, "at": 9e9},
                            f"tv|steam:1|grid": {"choice": {"type": "web", "url": URL}, "at": 9e9}}}
    monkeypatch.setattr(artsync, "sync", fake)
    assert ownart.sync({}, 1) == 1
    assert sent["items"][0]["key"] == f"tv|{gk}|grid" and sent["items"][0]["choice"]["type"] == "web"
    rows = [{"name": "Doom", "box": "", "snap": "", "logo": ""}]
    ownart.apply(rows, "snes", 1)
    assert rows[0]["box"] and rows[0]["snap"] and not rows[0]["logo"]


def test_key_file_round_trip_and_wrong_password(tmp_path):
    keys = {"sgdb_key": "S", "tgdb_key": "T", "obcy": "x"}
    f = art_sources.export_keys(str(tmp_path), "Haslo!123456", keys)
    assert b'"S"' not in f.read_bytes()
    assert art_sources.open_keys(str(tmp_path), "Haslo!123456") == {"sgdb_key": "S", "tgdb_key": "T"}
    assert art_sources.open_keys(str(f), "Haslo!123456")["tgdb_key"] == "T"
    with pytest.raises(ValueError):
        art_sources.open_keys(str(f), "zle")
    with pytest.raises(ValueError):
        art_sources.open_keys(str(tmp_path / "brak"), "x")
