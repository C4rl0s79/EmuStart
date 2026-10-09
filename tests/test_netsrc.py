"""Gry z serwera EmuStart (netsrc): szukanie po (system, ścieżka), pobieranie blokami
przez HTTP z łączeniem bloków, zapas SMB przy innym rozmiarze, keep-alive."""

from __future__ import annotations

import os

import pytest

from emustart import cache, library, netsrc, partial, remote, scanner, server

from test_core import _write, env  # noqa: F401  (fixture)

KEY = "k" * 32


class _FakeApi:
    _cfg = {"server_token": KEY}


@pytest.fixture()
def srv(env, monkeypatch):
    cfg, roms, tmp = env
    monkeypatch.setattr(remote, "API", _FakeApi())
    url = server.start(0, bind="127.0.0.1")
    port = int(url.rsplit(":", 1)[1])
    cfg.update(server_url=f"127.0.0.1:{port}", server_key=KEY)
    netsrc._info.clear()
    yield cfg, roms, tmp
    netsrc._info.clear()


def _game(roms, data: bytes):
    _write(roms / "psx" / "Cue Game (USA).cue", b'FILE "Cue Game (USA) (Track 1).bin" BINARY\n')
    _write(roms / "psx" / "Cue Game (USA) (Track 1).bin", data)
    scanner.scan_system("psx", roms / "psx")
    gid = library.db().execute("SELECT id FROM games").fetchone()["id"]
    return library.game(gid)


def test_info_and_wrong_key(srv):
    cfg, _roms, _tmp = srv
    i = netsrc.info(cfg, fresh=True)
    assert i["ok"] and "find" in i["features"] and i["rtt"] < 1
    bad = {**cfg, "server_key": "zly"}
    assert netsrc.info(bad, fresh=True) == {"ok": False, "reason": "zły klucz serwera"}
    assert netsrc.sources({**cfg, "server_games": False}, {"es": "psx"}) == {}
    assert netsrc.info({}, fresh=True)["ok"] is False


def test_download_from_server_coalesced(srv, monkeypatch):
    cfg, roms, tmp = srv
    monkeypatch.setattr(partial, "BLOCK", 64 * 1024)
    monkeypatch.setattr(partial, "MIN_SIZE", 64 * 1024)
    data = os.urandom(64 * 1024 * 9 + 123)
    g = _game(roms, data)
    srcs = netsrc.sources(cfg, g)
    assert set(srcs) == {r for r, _s, _m in g["files"]}         # oba pliki są na serwerze
    big = next(s for s in srcs.values() if s.size == len(data))

    p = partial.Partial(big, tmp / "out" / "game.bin", len(data), 1000.0)
    assert p.coalesce == netsrc.COALESCE
    calls = []
    orig = netsrc.RemoteReader._get
    monkeypatch.setattr(netsrc.RemoteReader, "_get", lambda self, off, n: calls.append(n) or orig(self, off, n))
    p.run(streams=2)
    assert (tmp / "out" / "game.bin").read_bytes() == data
    assert max(calls) == netsrc.COALESCE * partial.BLOCK              # kolejne bloki jednym zapytaniem
    assert len(calls) < p.blocks


def test_copy_game_from_server_without_nas(srv, monkeypatch):
    cfg, roms, tmp = srv
    monkeypatch.setattr(partial, "BLOCK", 64 * 1024)
    monkeypatch.setattr(partial, "MIN_SIZE", 64 * 1024)
    data = os.urandom(300 * 1024)
    g = _game(roms, data)
    srcs = netsrc.sources(cfg, g)
    prog = cache.Progress(g["size"], len(g["files"]))
    import threading
    # NAS „niedostępny”: folder gier z innej ścieżki — wszystko musi przyjść z serwera
    cache.copy_game(cfg, g, tmp / "brak-nas", prog, threading.Event(), srcs=srcs)
    assert cache.is_complete(cfg, g)
    assert cache.local_path(cfg, g).with_name("Cue Game (USA) (Track 1).bin").read_bytes() == data


def test_size_mismatch_falls_back_to_smb(srv):
    cfg, roms, _tmp = srv
    g = _game(roms, b"x" * 5000)
    g["files"] = [(r, s + 1 if r.endswith(".bin") else s, m) for r, s, m in g["files"]]
    srcs = netsrc.sources(cfg, g)
    assert [r for r in srcs] == ["Cue Game (USA).cue"]                # .bin: inny rozmiar — SMB


def test_reader_reconnects_after_close(srv):
    cfg, roms, _tmp = srv
    data = os.urandom(10000)
    g = _game(roms, data)
    f = next(s for s in netsrc.sources(cfg, g).values() if s.size == len(data))
    with netsrc.open_src(f) as r:
        assert r.read(0) == b""
        r.seek(100)
        assert r.read(50) == data[100:150]
        first = r.conn
        assert r.read(50) == data[150:200] and r.conn is first        # to samo połączenie (HTTP/1.1)
        r.close()
        r.seek(9990)
        assert r.read(100) == data[9990:]                              # koniec pliku: tylko to, co jest
        assert r.read(10) == b""


def test_guess_url_from_unc():
    assert netsrc.guess_url([r"\\100.85.254.31\EMU_ROMS\ROMS"]) == "http://100.85.254.31:8740"
    assert netsrc.guess_url([r"C:\gry"]) in ("", )
