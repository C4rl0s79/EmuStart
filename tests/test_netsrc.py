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


# ── folder profili przez serwer ──

def _nas_server(env, monkeypatch, nas_name="nas"):
    from emustart import profiles
    from _srv import use_server
    cfg, _roms, tmp = env
    monkeypatch.setattr(profiles, "LOCAL", tmp / "profiles")
    cfg["profiles_nas"] = str(tmp / nas_name)
    use_server(cfg, tmp / "nas", monkeypatch)
    return cfg, tmp


def test_remote_write_is_compressed_and_backed_up_on_server(env, monkeypatch):
    from emustart import nasfs
    cfg, tmp = _nas_server(env, monkeypatch)
    fs = nasfs.backend(cfg)
    sent = []
    orig = fs._req
    monkeypatch.setattr(fs, "_req", lambda m, p, q, body=None, headers=None:
                        sent.append((m, len(body or b""), (headers or {}).get("Content-Encoding"))) or orig(m, p, q, body, headers))
    card = b"\xff" * (8 << 20)                                     # pusta karta PS2
    fs.write("Ania/save/pcsx2/memcards/Mcd001.ps2", card, 1000.0)
    put = [x for x in sent if x[0] == "PUT"][0]
    assert put[2] == "zstd" and put[1] < 10_000                     # 8 MB → kilka KB
    f = tmp / "nas" / "Ania" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2"
    assert f.read_bytes() == card and abs(f.stat().st_mtime - 1000.0) < 1
    fs.write("Ania/save/pcsx2/memcards/Mcd001.ps2", b"nowa", 2000.0,
             backup="Ania/_backup/20260101-000000-pc/save/pcsx2/memcards/Mcd001.ps2")
    assert (tmp / "nas" / "Ania" / "_backup" / "20260101-000000-pc" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2").read_bytes() == card
    assert fs.read("Ania/save/pcsx2/memcards/Mcd001.ps2") == b"nowa"
    assert fs.scan("Ania/save") == {"pcsx2/memcards/Mcd001.ps2": (4, 2000.0)}


def test_remote_rejects_paths_outside_profiles(env, monkeypatch):
    import http.client
    from _srv import KEY
    cfg, tmp = _nas_server(env, monkeypatch)
    host, port = cfg["server_url"].split(":")
    for p in ("../x", "..\\x", "C:/Windows/x", ".emustart-root"):
        c = http.client.HTTPConnection(host, int(port))
        c.request("PUT", "/v1/nas/file?p=" + p.replace("\\", "%5C"), body=b"zle",
                  headers={"Authorization": f"Bearer {KEY}"})
        assert c.getresponse().status == 400, p
    c = http.client.HTTPConnection(host, int(port))
    c.request("GET", "/v1/nas/list?p=", headers={"Authorization": "Bearer zly"})
    assert c.getresponse().status == 401
    assert not (tmp / "x").exists()


def test_other_folder_on_smb_means_smb(env, monkeypatch):
    from emustart import nasfs
    cfg, tmp = _nas_server(env, monkeypatch, nas_name="inny")      # SMB widzi inny folder niż serwer
    (tmp / "inny").mkdir()
    nasfs.reset()
    fs = nasfs.backend(cfg)
    assert isinstance(fs, nasfs.SmbFs) and fs.root == tmp / "inny"


def test_push_all_sends_existing_local_saves(env, monkeypatch):
    from emustart import profiles
    cfg, tmp = _nas_server(env, monkeypatch)
    pid = profiles.first_id()
    _write(profiles.local_dir(pid, "duckstation", "memcards") / "a.mcd", b"A" * 5000)
    _write(profiles.LOCAL / str(pid) / "settings" / "duckstation" / "settings.ini", b"[Main]\n")
    assert profiles.push_all(cfg) == 2
    nas = tmp / "nas" / profiles.get(pid)["nas_name"]
    assert (nas / "save" / "duckstation" / "memcards" / "a.mcd").read_bytes() == b"A" * 5000
    assert (nas / "settings" / "duckstation" / "settings.ini").is_file()
    assert profiles.push_all(cfg) == 0                             # drugi raz nic do wysłania


def test_system_look_pushed_to_server(srv, monkeypatch, tmp_path):
    from emustart import logos
    cfg, _roms, _tmp = srv
    server_cfg = {"server_token": KEY, "systems": {}}
    monkeypatch.setattr(remote.API, "_cfg", server_cfg)
    monkeypatch.setattr(remote.config, "save", lambda c: None)
    mine, theirs = tmp_path / "pc", tmp_path / "serwer"
    mine.mkdir(); theirs.mkdir()
    (mine / "lynx.custom.png").write_bytes(b"\x89PNG lynx")
    cfg["systems"] = {"lynx": {"logo": "custom", "logo_glow": True, "name": "Atari Lynx"},
                      "nes": {"logo": "none"}}
    monkeypatch.setattr(logos, "_dir", lambda: mine)
    sent = []
    monkeypatch.setattr(logos, "_store_custom", lambda es, data, ext: sent.append((es, data, ext)))
    assert logos.push_to_server(cfg) == 2
    assert sent == [("lynx", b"\x89PNG lynx", "png")]
    assert server_cfg["systems"]["lynx"] == {"logo": "custom", "logo_glow": True, "name": "Atari Lynx"}
    assert server_cfg["systems"]["nes"]["logo"] == "none"
    assert logos.push_to_server(cfg) == 0                         # bez zmian — nic nie idzie
    cfg["systems"]["lynx"]["logo_glow"] = False
    assert logos.push_to_server(cfg, ["lynx"]) == 1 and server_cfg["systems"]["lynx"]["logo_glow"] is False
