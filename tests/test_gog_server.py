"""Archiwum depotów GOG na serwerze: indeks (kompletność, ścieżki, sekrety) i autoryzowane /v1/gog/…"""

from __future__ import annotations

import json
import os

import keyhelp
import pytest

from emustart import gogarchive as ga
from goghelp import BUILD, PID, all_chunk_files, build_archive
from test_three_devices import _req, srv  # noqa: F401  (fixture)


@pytest.fixture()
def arc(tmp_path):
    root = tmp_path / "_depots"
    info = build_archive(root)
    ga.INDEX.update(root="", builds={}, rejected=[], error="", running=False)
    yield root, info
    ga.INDEX.update(root="", builds={}, rejected=[], error="", running=False)


def _index(root):
    ga.refresh(str(root), wait=True)
    return ga.status()


def test_complete_archive_is_indexed(arc):
    root, _ = arc
    st = _index(root)
    assert st["error"] == "" and st["games"] == 1 and st["builds"] == 1 and not st["rejected"]
    g = ga.game(PID)
    b = g["builds"][0]
    assert b["build_id"] == BUILD and b["languages"] == ["en", "pl"]
    assert {d["id"]: d["has_files"] for d in b["dlcs"]} == {"2001": True, "2002": False}
    assert b["dependencies"] == ["MSVC2017"] and len(b["hash"]) == 64


@pytest.mark.parametrize("damage", ["state", "missing_chunk", "short_chunk", "missing_manifest"])
def test_incomplete_archive_is_not_published(tmp_path, damage):
    root = tmp_path / "_depots"
    build_archive(root, state="incomplete" if damage == "state" else "complete")
    if damage == "missing_chunk":
        all_chunk_files(root)[0].unlink()
    if damage == "short_chunk":
        p = all_chunk_files(root)[0]
        p.write_bytes(p.read_bytes()[:-1])
    if damage == "missing_manifest":
        next((root / "manifests" / "dep").rglob("*.json")).unlink()
    st = _index(root)
    assert st["builds"] == 0 and len(st["rejected"]) == 1
    assert ga.catalog() == []


def test_unsafe_manifest_path_is_rejected(tmp_path):
    root = tmp_path / "_depots"
    build_archive(root, extra_items=[{"type": "DepotFile", "path": "..\\..\\Windows\\evil.dll", "chunks": []}])
    st = _index(root)
    assert st["builds"] == 0 and "niebezpieczna ścieżka" in st["rejected"][0]["reason"]


def test_foreign_or_newer_format_is_refused(tmp_path):
    root = tmp_path / "x"
    root.mkdir()
    (root / "archive.json").write_text(json.dumps({"format": "goggui-depot-archive", "version": 9}))
    assert "obsługuje" in _index(root)["error"]
    (root / "archive.json").write_text(json.dumps({"format": "inne"}))
    assert "obcy format" in _index(root)["error"]


@pytest.mark.parametrize("p", ["C:\\Windows\\x", "\\\\nas\\share\\x", "a/../../x", "a/con.txt", "a/file.txt:ads",
                               "a/nazwa. ", "a/b\x01c", "", "/"])
def test_safe_rel_rejects(p):
    with pytest.raises(ga.UnsafePath):
        ga.safe_rel(p)


def test_safe_rel_accepts_relative():
    assert ga.safe_rel("\\bin\\game.exe") == "bin/game.exe"
    assert ga.safe_rel("./a/./b.txt") == "a/b.txt"


def test_manifest_has_no_secrets(arc):
    root, _ = arc
    _index(root)
    m = ga.build_manifest(PID, BUILD)
    raw = json.dumps(m)
    assert "SEKRET-NIE-WYSYLAC" not in raw and "clientSecret" not in raw and "clientId" not in raw
    assert m["hash"] == ga.game(PID)["builds"][0]["hash"]
    assert set(m["manifests"]) == {"depot", "dep"}


def test_changed_record_is_refused(arc):
    root, _ = arc
    _index(root)
    rp = root / "products" / PID / f"{BUILD}.json"
    rec = json.loads(rp.read_text())
    rec["title"] = "zmieniony"
    rp.write_text(json.dumps(rec))
    os.utime(rp, (1, 1))
    with pytest.raises(ga.Changed):
        ga.build_manifest(PID, BUILD)


# ── HTTP: autoryzacja, uprawnienia, przypięcie i ścieżki ──

def test_http_requires_auth_and_games_permission(srv, arc):  # noqa: F811
    root, _ = arc
    _index(root)
    assert _req(srv, "GET", "/v1/gog/games")[0] == 401
    assert _req(srv, "GET", "/v1/gog/games", "zly-token")[0] == 401
    _did, no_games = keyhelp.device("Gosc", games=False, name="bez gier")
    assert _req(srv, "GET", "/v1/gog/games", no_games)[0] == 403
    _did, tok = keyhelp.device("Gracz", games=True)
    st, body = _req(srv, "GET", "/v1/gog/games", tok)
    assert st == 200 and body["games"][0]["product_id"] == PID
    st, info = _req(srv, "GET", "/v1/info", tok)
    assert "gog" in info["features"]


def test_http_chunks_only_from_pinned_build(srv, arc):  # noqa: F811
    root, _ = arc
    _index(root)
    _did, tok = keyhelp.device("Gracz", games=True)
    st, man = _req(srv, "GET", f"/v1/gog/games/{PID}/builds/{BUILD}/manifest", tok)
    assert st == 200
    h = man["hash"]
    c = man["manifests"]["depot"][next(iter(man["manifests"]["depot"]))]["depot"]["items"][0]["chunks"][0]
    q = f"pid={PID}&build={BUILD}&hash={h}"
    st, data = _req(srv, "GET", f"/v1/gog/chunks/{c['compressedMd5']}?{q}", tok)
    assert st == 200 and isinstance(data, bytes) and len(data) == c["compressedSize"]
    # chunk spoza buildu (podrzucony do archiwum) — nie wydajemy
    stray = root / "chunks" / "ab" / "cd" / ("abcd" + "0" * 28)
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"x")
    assert _req(srv, "GET", f"/v1/gog/chunks/{'abcd' + '0' * 28}?{q}", tok)[0] == 404
    # inny hash (build zmienił się od przypięcia) — jednoznaczny błąd
    st, body = _req(srv, "GET", f"/v1/gog/chunks/{c['compressedMd5']}?pid={PID}&build={BUILD}&hash={'0' * 64}", tok)
    assert st == 409 and body.get("changed")
    # zniekształcone identyfikatory i próby wyjścia poza archiwum
    for path in (f"/v1/gog/chunks/..%2f..%2farchive.json?{q}", f"/v1/gog/games/..%2f{PID}",
                 f"/v1/gog/games/{PID}/builds/..%2f..%2farchive/manifest", f"/v1/gog/chunks/{c['compressedMd5']}?pid=../x&build={BUILD}&hash={h}"):
        assert _req(srv, "GET", path, tok)[0] == 404, path


def test_http_corrupt_chunk_on_server_is_reported(srv, arc):  # noqa: F811
    root, _ = arc
    _index(root)
    _did, tok = keyhelp.device("Gracz", games=True)
    man = _req(srv, "GET", f"/v1/gog/games/{PID}/builds/{BUILD}/manifest", tok)[1]
    c = man["manifests"]["depot"][next(iter(man["manifests"]["depot"]))]["depot"]["items"][0]["chunks"][0]
    p = ga.Archive(root).chunk_path(c["compressedMd5"])
    p.write_bytes(bytes(len(p.read_bytes())))               # ten sam rozmiar, zła treść
    st, body = _req(srv, "GET", f"/v1/gog/chunks/{c['compressedMd5']}?pid={PID}&build={BUILD}&hash={man['hash']}", tok)
    assert st == 409 and "uszkodzony" in body["error"]


# ── poprawki z przeglądu kodu ──

def _wait_index():
    import time
    for _ in range(250):
        if not ga.status()["running"]:
            return
        time.sleep(0.02)
    raise AssertionError("indeks nie skończył się")


def test_reverified_record_keeps_pinned_build(arc):
    root, _ = arc
    _index(root)
    h = ga.INDEX["builds"][(PID, BUILD)]["hash"]
    rp = root / "products" / PID / f"{BUILD}.json"
    rec = json.loads(rp.read_text())
    rec["verified"] = "2030-01-01T00:00:00"                          # GOG Manager: ponowna weryfikacja
    rp.write_text(json.dumps(rec, indent=1))
    os.utime(rp, (2, 2))
    assert ga.pinned(PID, BUILD, h)["hash"] == h                    # ten sam build — instalacje trwają dalej
    assert ga.build_manifest(PID, BUILD)["hash"] == h


def test_path_change_during_indexing_is_not_lost(arc, tmp_path, monkeypatch):
    import threading
    root, _ = arc
    other = tmp_path / "_depots2"
    build_archive(other)
    gate = threading.Event()
    real = ga.build_index

    def slow(r, deep=False):
        if r == str(root):
            gate.wait(5)
        return real(r, deep)
    monkeypatch.setattr(ga, "build_index", slow)
    ga.refresh(str(root))                       # indeksowanie A trwa…
    ga.refresh(str(other))                      # …admin zmienia ścieżkę na B
    gate.set()
    _wait_index()
    assert ga.status()["root"] == str(other) and ga.status()["builds"] == 1


def test_clearing_path_during_indexing_unpublishes(arc, monkeypatch):
    import threading
    root, _ = arc
    gate = threading.Event()
    real = ga.build_index

    def slow(r, deep=False):
        gate.wait(5)
        return real(r, deep)
    monkeypatch.setattr(ga, "build_index", slow)
    ga.refresh(str(root))
    ga.refresh("")                              # admin wyłącza GOG w trakcie indeksowania
    assert ga.status()["builds"] == 0
    gate.set()
    _wait_index()
    assert ga.status()["root"] == "" and ga.status()["builds"] == 0 and ga.catalog() == []
