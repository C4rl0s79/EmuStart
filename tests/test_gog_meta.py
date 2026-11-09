"""Opisy i grafiki gier GOG z folderów GOG Managera obok archiwum depotów (emustart/gogmeta.py): indeks,
katalog bez kluczy CD, trasa grafik z autoryzacją i ochroną ścieżek, kopia grafik w kliencie (bez serwera)."""

from __future__ import annotations

import json

import keyhelp
import pytest

from emustart import gogarchive as ga, goglib, gogmeta, goginstall as gi, paths
from goghelp import PID, build_archive
from test_three_devices import _req, srv  # noqa: F401  (fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"P" * 20
JPG = b"\xff\xd8\xff" + b"J" * 20


@pytest.fixture()
def arc(tmp_path):
    root = tmp_path / "GOG" / "_depots"
    build_archive(root)
    game = tmp_path / "GOG" / "testowa_gra"
    game.mkdir()
    (game / "product_testowa_gra.json").write_text(json.dumps({
        "id": int(PID), "slug": "testowa_gra", "title": "Testowa Gra",
        "description": {"lead": "<b>Krótko</b>", "full": "<p>Pierwszy akapit &amp; dalej.</p><ul><li>punkt</li></ul>"},
        "release_date": "2001-01-01T00:00:00+0000"}), encoding="utf-8")
    (game / "galaxy_testowa_gra.json").write_text(json.dumps({
        "releaseDate": "2002-03-04T00:00:00+02:00", "developers": ["Studio"], "publishers": ["Wydawca"],
        "tags": ["RPG"]}), encoding="utf-8")
    (game / "game-details.json").write_text(json.dumps({"cdKey": "TAJNY-KLUCZ"}), encoding="utf-8")
    (game / "boxart_testowa_gra.jpg").write_bytes(JPG)
    (game / "titlelogo_testowa_gra.png").write_bytes(PNG)
    (game / "logo_testowa_gra.jpg").write_bytes(JPG)
    (tmp_path / "GOG" / "Zla Nazwa").mkdir()                     # nie slug GOG — pomijany
    (tmp_path / "GOG" / "Zla Nazwa" / "product_Zla Nazwa.json").write_text(json.dumps({"id": 5}))
    ga.INDEX.update(root="", builds={}, rejected=[], error="", running=False)
    ga.refresh(str(root), wait=True)
    yield root, game
    ga.INDEX.update(root="", builds={}, rejected=[], error="", running=False)
    gogmeta.clear()


def test_catalog_has_description_and_no_keys(arc):
    g = ga.game(PID)
    info = g["info"]
    assert info["description"] == "Pierwszy akapit & dalej.\n\n• punkt"
    assert info["lead"] == "Krótko" and info["release"] == "2002-03-04"
    assert (info["developers"], info["publishers"], info["tags"]) == (["Studio"], ["Wydawca"], ["RPG"])
    assert info["art"] == ["grid", "logo"]
    assert "TAJNY" not in json.dumps(ga.catalog()) and "dir" not in info
    assert len(gogmeta.META["games"]) == 1                      # folder ze złą nazwą pominięty


def test_art_prefers_transparent_logo_and_stays_in_folder(arc):
    _root, game = arc
    assert gogmeta.art_path(PID, "logo").name == "titlelogo_testowa_gra.png"
    assert gogmeta.art_path(PID, "grid").read_bytes() == JPG
    assert gogmeta.art_path(PID, "hero") is None and gogmeta.art_path(PID, "../x") is None
    assert gogmeta.art_path("999", "grid") is None


def test_html_to_text():
    assert gogmeta.html_to_text("<script>x</script>A<br>B<h2>C</h2>&quot;D&quot;") == 'A\nB\nC\n"D"'


def test_http_art_requires_auth_and_only_archived_games(srv, arc):  # noqa: F811
    assert _req(srv, "GET", f"/v1/gog/games/{PID}/art/grid")[0] == 401
    _did, no_games = keyhelp.device("Gosc", games=False, name="bez gier")
    assert _req(srv, "GET", f"/v1/gog/games/{PID}/art/grid", no_games)[0] == 403
    _did, tok = keyhelp.device("Gracz", games=True)
    st, data = _req(srv, "GET", f"/v1/gog/games/{PID}/art/grid", tok)
    assert st == 200 and data == JPG
    for path in (f"/v1/gog/games/{PID}/art/hero", f"/v1/gog/games/{PID}/art/..%2fgame-details",
                 f"/v1/gog/games/{PID}/art/details", "/v1/gog/games/999/art/grid"):
        assert _req(srv, "GET", path, tok)[0] == 404, path


class FakeArt:
    def __init__(self):
        self.calls = 0
        self.down = False

    def art(self, pid, kind):
        self.calls += 1
        if self.down:
            raise OSError("serwer niedostępny")
        p = gogmeta.art_path(pid, kind)
        return (p.read_bytes(), "image/png" if p.suffix == ".png" else "image/jpeg") if p else (None, "")


def test_client_keeps_a_copy_and_works_without_server(arc, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path / "dane")
    src = FakeArt()
    svc = goglib.GogService(lambda: {}, source_fn=lambda: src)
    r = svc.art(PID, "logo")
    assert r["path"].endswith("logo.png") and open(r["path"], "rb").read() == PNG
    assert svc.art(PID, "hero") == {"path": ""}                # brak grafiki zapamiętany
    calls = src.calls
    src.down = True
    assert svc.art(PID, "logo")["path"].endswith("logo.png")     # z kopii, bez serwera
    assert svc.art(PID, "hero") == {"path": ""}
    assert src.calls == calls                                   # świeże kopie nie pytają serwera
    with pytest.raises(ValueError):
        svc.art(PID, "../x")
    assert str(gi.gog_dir()).startswith(str(tmp_path / "dane"))


def test_vertical_art_over_http(srv, arc, tmp_path, monkeypatch):  # noqa: F811
    """serwer (folder gry GOG Managera) → /v1/gog/…/art → klient silnika → kopia na dysku → bez serwera."""
    import threading
    from emustart import netsrc
    host, port, _nas = srv
    _did, tok = keyhelp.device("Gracz", games=True)
    src = gi.ServerSource.__new__(gi.ServerSource)
    src.netsrc, src.ep, src._local = netsrc, (host, port, tok), threading.local()
    assert src.art(PID, "grid") == (JPG, "image/jpeg")
    assert src.art(PID, "logo") == (PNG, "image/png")
    assert src.art(PID, "hero") == (None, "")
    monkeypatch.setattr(paths, "DATA", tmp_path / "dane")
    svc = goglib.GogService(lambda: {}, source_fn=lambda: src)
    p = svc.art(PID, "grid")["path"]
    assert open(p, "rb").read() == JPG
    svc._source_fn = lambda: (_ for _ in ()).throw(OSError("brak serwera"))
    assert svc.art(PID, "grid")["path"] == p


def test_http_saverules_requires_auth(srv, arc, tmp_path, monkeypatch):  # noqa: F811
    from emustart import saverules
    monkeypatch.setattr(paths, "DATA", tmp_path / "srvdata")
    monkeypatch.setattr(saverules, "MIN_ENTRIES", 1)
    monkeypatch.setattr(saverules, "_index", None)
    saverules.install(f"Testowa Gra:\n  files:\n    <base>/save: {{tags: [save]}}\n  gog: {{id: {PID}}}\n".encode())
    assert _req(srv, "GET", f"/v1/gog/games/{PID}/saverules")[0] == 401
    _did, tok = keyhelp.device("Gracz", games=True)
    st, r = _req(srv, "GET", f"/v1/gog/games/{PID}/saverules", tok)
    assert st == 200 and r["source"] == "ludusavi" and r["files"][0]["path"] == "<base>/save"
    assert _req(srv, "GET", "/v1/gog/games/..%2fx/saverules", tok)[0] == 404


@pytest.fixture()
def extras(arc):
    root, game = arc
    d = game / "extras"
    d.mkdir()
    (d / "soundtrack_mp3.zip").write_bytes(b"S" * 5000)
    (d / "manual.pdf").write_bytes(b"M" * 300)
    (d / "game_classic_1998_part1.zip").write_bytes(b"O" * 700)
    (d / "pobierane.zip.part").write_bytes(b"x")
    (d / "Thumbs.db").write_bytes(b"x")
    prod = json.loads((game / "product_testowa_gra.json").read_text(encoding="utf-8"))
    prod["downloads"] = {"bonus_content": [{"name": "Oryginalny soundtrack", "type": "audio", "total_size": 5000}]}
    (game / "product_testowa_gra.json").write_text(json.dumps(prod), encoding="utf-8")
    ga.refresh(str(root), wait=True)
    return root, game


def test_extras_listed_with_defaults_like_gog_manager(extras):
    items = {x["file"]: x for x in ga.game(PID)["info"]["extras"]}
    assert set(items) == {"soundtrack_mp3.zip", "manual.pdf", "game_classic_1998_part1.zip"}
    assert items["soundtrack_mp3.zip"]["name"] == "Oryginalny soundtrack" and items["soundtrack_mp3.zip"]["type"] == "audio"
    assert items["manual.pdf"]["default"] and items["soundtrack_mp3.zip"]["default"]
    assert not items["game_classic_1998_part1.zip"]["default"]                # stara wersja — domyślnie pominięta


def test_http_extra_requires_auth_and_stays_in_folder(srv, extras):  # noqa: F811
    x = next(i for i in ga.game(PID)["info"]["extras"] if i["file"] == "manual.pdf")
    path = f"/v1/gog/games/{PID}/extras/{x['id']}"
    assert _req(srv, "GET", path)[0] == 401
    _did, tok = keyhelp.device("Gracz", games=True)
    st, data = _req(srv, "GET", path, tok)
    assert st == 200 and data == b"M" * 300
    assert _req(srv, "GET", path, tok, headers={"Range": "bytes=100-"})[1] == b"M" * 200
    for bad in (f"/v1/gog/games/{PID}/extras/..%2fgame-details", f"/v1/gog/games/{PID}/extras/{'0' * 16}", f"/v1/gog/games/999/extras/{x['id']}"):
        assert _req(srv, "GET", bad, tok)[0] == 404, bad


def test_extras_download_job_over_http_resume_and_cancel_state(srv, extras, tmp_path, monkeypatch):  # noqa: F811
    import threading
    import time
    from emustart import netsrc
    host, port, _nas = srv
    _did, tok = keyhelp.device("Gracz", games=True)
    monkeypatch.setattr(paths, "DATA", tmp_path / "dane")
    src = gi.ServerSource.__new__(gi.ServerSource)
    src.netsrc, src.ep, src._local = netsrc, (host, port, tok), threading.local()
    monkeypatch.setattr(src, "catalog", ga.catalog, raising=False)
    svc = goglib.GogService(lambda: {}, source_fn=lambda: src)
    svc.catalog(refresh=True)
    base = str(tmp_path / "GOG Games")
    r = svc.extras(PID, base)
    assert r["target"].endswith("_extras\\testowa_gra") or r["target"].endswith("_extras/testowa_gra")
    assert not any(x["have"] for x in r["items"])
    pick = [x["id"] for x in r["items"] if x["default"]]
    # niedokończony plik z poprzedniej próby — dociągany od miejsca przerwania
    d = tmp_path / "GOG Games" / "_extras" / "testowa_gra"
    d.mkdir(parents=True)
    (d / "soundtrack_mp3.zip.part").write_bytes(b"S" * 2000)
    assert svc.enqueue("extras", PID, ids=pick, base=base, title="T")["ok"]
    for _ in range(200):
        if svc._last.get(PID):
            break
        time.sleep(0.02)
    assert svc._last[PID]["ok"], svc._last[PID]
    assert (d / "soundtrack_mp3.zip").read_bytes() == b"S" * 5000 and not (d / "soundtrack_mp3.zip.part").exists()
    assert (d / "manual.pdf").read_bytes() == b"M" * 300
    assert not (d / "game_classic_1998_part1.zip").exists()                   # niewybrany
    assert {x["file"]: x["have"] for x in svc.extras(PID, base)["items"]}["manual.pdf"]
