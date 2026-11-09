"""Grafiki użytkowników i wybory grafik: serwer (emustart/userart.py, /v1/userart) i klient silnika (artsync.py)."""

from __future__ import annotations

import json

import keyhelp
import pytest

from emustart import paths, userart
from test_three_devices import _req, srv  # noqa: F401  (fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40
GIF = b"GIF89a" + b"y" * 40


@pytest.fixture()
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "srv")


def test_upload_only_images_dedupes_and_validates(data):
    a = userart.upload("Ala", "navgrp:grp:pc", "logo", PNG)
    assert a["ext"] == "png"
    assert userart.upload("Bob", "navgrp:grp:pc", "logo", PNG)["sha"] == a["sha"]
    assert len(list((paths.DATA / "userart" / "files").iterdir())) == 1
    for game, kind, body in (("x", "logo", b"<html>"), ("../x", "logo", PNG), ("x", "zly", PNG), ("x", "logo", b"")):
        with pytest.raises(userart.ArtError):
            userart.upload("Ala", game, kind, body)


def test_gallery_shows_everyones_uploads_mine_first(data):
    userart.upload("Bob", "gog:1", "grid", GIF)
    userart.upload("Ala", "gog:1", "grid", PNG)
    userart.upload("Ala", "gog:2", "grid", PNG)
    items = userart.gallery("Ala", "gog:1", "grid")["items"]
    assert [(i["by"], i["mine"]) for i in items] == [("Ala", True), ("Bob", False)]
    bob = items[1]["sha"]
    userart.remove("Ala", "gog:1", "grid", bob)                     # cudzego wpisu nie usuniesz
    assert len(userart.gallery("Ala", "gog:1", "grid")["items"]) == 2
    userart.remove("Bob", "gog:1", "grid", bob)
    assert len(userart.gallery("Ala", "gog:1", "grid")["items"]) == 1
    assert userart.file_path(bob)[0] is not None                    # plik zostaje: ktoś mógł go wybrać


def test_choices_newest_wins_per_account_and_no_local_paths(data):
    sha = userart.upload("Ala", "gog:1", "grid", PNG)["sha"]
    key = "tv|gog:1|grid"
    u = {"type": "user", "sha": sha, "ext": "png"}
    assert userart.sync("Ala", [{"key": key, "choice": u, "at": 10}])["choices"][key]["choice"] == u
    r = userart.sync("Ala", [{"key": key, "choice": {"type": "none"}, "at": 5}])    # starszy — bez zmian
    assert r["choices"][key]["choice"] == u
    r = userart.sync("Ala", [{"key": key, "choice": None, "at": 20}])               # usunięcie wyboru też się synchronizuje
    assert r["choices"][key] == {"choice": None, "at": 20.0}
    r = userart.sync("Ala", [{"key": "desktop|gog:1|grid", "choice": {"type": "file", "file": "C:/x.png"}, "at": 30},
                             {"key": "zly", "choice": u, "at": 30}, {"key": "tv|../x|grid", "choice": u, "at": 30}])
    assert set(r["choices"]) == {key}                               # ścieżka lokalna i błędne klucze odrzucone
    assert userart.sync("Bob", [])["choices"] == {}                 # inne konto — osobne wybory


def test_http_auth_identity_and_file(srv, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(paths, "DATA", tmp_path / "srv")
    assert _req(srv, "GET", "/v1/userart/gallery?game=gog:1&kind=grid")[0] == 401
    assert _req(srv, "POST", "/v1/userart/upload?game=gog:1&kind=grid", body=PNG)[0] == 401
    _d, ala = keyhelp.device("Ala", games=True)
    _d, bob = keyhelp.device("Bob", games=True, name="bob")
    st, r = _req(srv, "POST", "/v1/userart/upload?game=gog:1&kind=grid&account=Bob", ala, body=PNG)
    assert st == 200 and r["sha"]
    st, g = _req(srv, "GET", "/v1/userart/gallery?game=gog:1&kind=grid", bob)
    assert st == 200 and g["items"][0]["by"] == "Ala" and not g["items"][0]["mine"]       # konto z tokenu, nie z zapytania
    st, f = _req(srv, "GET", f"/v1/userart/file/{r['sha']}", bob)
    assert st == 200 and f == PNG
    assert _req(srv, "GET", "/v1/userart/file/..%2findex.json", bob)[0] in (400, 404)
    assert _req(srv, "POST", "/v1/userart/upload?game=gog:1&kind=grid", ala, body=b"<script>")[0] == 400
    st, c = _req(srv, "POST", "/v1/userart/choices", bob, body={"items": [{"key": "tv|gog:1|grid", "at": 5, "choice": {"type": "user", "sha": r["sha"], "ext": "png"}}]})
    assert st == 200 and c["choices"]["tv|gog:1|grid"]["choice"]["sha"] == r["sha"]        # kopia cudzej grafiki
    assert json.loads((paths.DATA / "userart" / "index.json").read_text(encoding="utf-8"))["accounts"]["Ala"]["choices"] == {}


def test_gallery_without_game_lists_whole_server(data):
    png = b"\x89PNG\r\n\x1a\n"
    userart.upload("Ala", "gog:1", "logo", png + b"a")
    userart.upload("Bob", "navgrp:grp:pc", "logo", png + b"b")
    userart.upload("Bob", "gog:2", "grid", png + b"c")
    items = userart.gallery("Ala", "", "logo")["items"]
    assert [(i["by"], i["game"]) for i in items] == [("Ala", "gog:1"), ("Bob", "navgrp:grp:pc")]
    with pytest.raises(userart.ArtError):
        userart.gallery("Ala", "", "zly")


def test_web_choice_only_from_known_hosts(data):
    ok = {"type": "web", "url": "https://images.igdb.com/igdb/image/upload/t_cover_big/x.jpg"}
    assert userart._valid_choice(ok) == ok
    with pytest.raises(userart.ArtError):
        userart._valid_choice({"type": "web", "url": "https://zly.example/x.jpg"})
