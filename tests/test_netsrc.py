"""Gry z serwera EmuStart (netsrc): szukanie po (system, ścieżka), pobieranie blokami
przez HTTP z łączeniem bloków, zapas SMB przy innym rozmiarze, keep-alive."""

from __future__ import annotations

import os

import pytest

from emustart import cache, library, netsrc, partial, remote, scanner

from test_core import _write, env  # noqa: F401  (fixture)

KEY = "k" * 32


class _FakeApi:
    _cfg = {"server_token": KEY}


@pytest.fixture()
def srv(env, monkeypatch):
    cfg, roms, tmp = env
    from _srv import tls_server
    monkeypatch.setattr(remote, "API", _FakeApi())
    url, fp = tls_server(monkeypatch)               # klient łączy się szyfrowanym portem (0.43)
    cfg.update(server_url=url, server_fp=fp, server_key=KEY)
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
    assert netsrc.info(bad, fresh=True) == {"ok": False, "reason": "zły klucz serwera", "key_rejected": True}
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
    bak = tmp / "nas" / "Ania" / "_backup" / "20260101-000000-pc" / "save" / "pcsx2" / "memcards" / "Mcd001.ps2.zst"
    assert bak.stat().st_size < 10_000                              # pusta karta 8 MB w kopii: kilka KB
    restored = tmp / "przywrocona.ps2"
    nasfs.restore_backup(bak, restored)
    assert restored.read_bytes() == card
    # ta sama treść jeszcze raz — bez nowej kopii zapasowej, tylko data
    fs.write("Ania/save/pcsx2/memcards/Mcd001.ps2", b"nowa", 3000.0,
             backup="Ania/_backup/20260101-000001-pc/save/pcsx2/memcards/Mcd001.ps2")
    assert not (tmp / "nas" / "Ania" / "_backup" / "20260101-000001-pc").exists()
    assert fs.read("Ania/save/pcsx2/memcards/Mcd001.ps2") == b"nowa"
    assert fs.scan("Ania/save") == {"pcsx2/memcards/Mcd001.ps2": (4, 3000.0)}


def test_remote_rejects_paths_outside_profiles(env, monkeypatch):
    from _srv import KEY
    cfg, tmp = _nas_server(env, monkeypatch)
    host, port = cfg["server_url"].removeprefix("https://").split(":")
    for p in ("../x", "..\\x", "C:/Windows/x", ".emustart-root"):
        c = netsrc.PinnedHTTPS(host, int(port), cfg["server_fp"])
        c.request("PUT", "/v1/nas/file?p=" + p.replace("\\", "%5C"), body=b"zle",
                  headers={"Authorization": f"Bearer {KEY}"})
        assert c.getresponse().status == 400, p
    c = netsrc.PinnedHTTPS(host, int(port), cfg["server_fp"])
    c.request("GET", "/v1/nas/list?p=", headers={"Authorization": "Bearer zly"})
    assert c.getresponse().status == 401
    assert not (tmp / "x").exists()


def test_profiles_only_through_server(env, monkeypatch):
    """Zapisy i konta tylko przez serwer; serwer nie odpowiada albo PC nie jest zalogowany —
    tryb offline (zapisy czekają w kolejce)."""
    from emustart import nasfs
    cfg, tmp = _nas_server(env, monkeypatch, nas_name="inny")
    (tmp / "inny").mkdir()
    nasfs.reset()
    assert isinstance(nasfs.backend(cfg), nasfs.RemoteFs)
    off = {**cfg, "server_url": "https://127.0.0.1:1"}             # serwer nie odpowiada
    netsrc._info.clear(); nasfs.reset()
    fs = nasfs.backend(off)
    assert isinstance(fs, nasfs.RemoteFs) and not fs.online()
    fs = nasfs.backend({"profiles_nas": str(tmp / "inny")})           # bez zalogowania
    assert fs is nasfs.OFFLINE and not fs.online()
    with pytest.raises(OSError):
        fs.write("Jezus/save/x.srm", b"zapis")

def test_push_all_sends_existing_local_saves(env, monkeypatch):
    from emustart import profiles
    cfg, tmp = _nas_server(env, monkeypatch)
    pid = profiles.first_id()
    _write(profiles.local_dir(pid, "duckstation", "memcards") / "a.mcd", b"A" * 5000)
    _write(profiles.LOCAL / str(pid) / "settings" / "duckstation" / "settings.ini", b"[Main]\n")
    assert profiles.push_all(cfg) == 2
    nas = tmp / "nas" / profiles.get(pid)["nas_name"]
    assert (nas / "save" / "duckstation" / "memcards" / "a.mcd").read_bytes() == b"A" * 5000
    assert (nas / "settings" / "windows" / "duckstation" / "settings.ini").is_file()
    assert profiles.push_all(cfg) == 0                             # drugi raz nic do wysłania


def test_server_status_never_waits_for_the_network(srv, monkeypatch):
    """Pasek stanu przy starcie: bez czekania na wybór adresu i logowanie (telefon/PC czekały
    ponad 10 s z wyłączonym Tailscale); wynik dochodzi w tle."""
    import time as _t
    cfg, _roms, _tmp = srv
    netsrc._info.clear()
    slow = netsrc.info
    monkeypatch.setattr(netsrc, "info", lambda c, fresh=False: (_t.sleep(1.0), slow(c, fresh))[1])
    t = _t.monotonic()
    assert netsrc.info_nowait(cfg) is None and _t.monotonic() - t < 0.2       # od razu: „łączę…”
    for _ in range(50):
        if netsrc.info_nowait(cfg):
            break
        _t.sleep(0.1)
    assert netsrc.info_nowait(cfg)["ok"]


def test_arcade_version_gets_parent_from_server(srv):
    """Wersja gry FBA na PC: gra nadrzędna (i BIOS) z serwera, obok gry w pamięci podręcznej —
    wcześniej PC szukał ich tylko w swoich folderach, więc emulator zgłaszał brak plików."""
    from emustart import arcade
    cfg, roms, tmp = srv
    dat = tmp / "fbneo.dat"
    dat.write_text('<?xml version="1.0"?><datafile>'
                   '<game name="1941"><description>1941 (World)</description></game>'
                   '<game name="1941j" cloneof="1941" romof="1941"><description>1941 (Japan)</description></game>'
                   '</datafile>', encoding="utf-8")
    assert arcade.refresh_fbneo(force=True, url=dat.as_uri())["ok"]
    _write(roms / "fbneo" / "1941.zip", b"RODZIC")
    _write(roms / "fbneo" / "1941j.zip", b"WERSJA")
    scanner.scan_system("fbneo", roms / "fbneo")
    g = library.game(library.db().execute("SELECT id FROM games WHERE rel='1941j.zip'").fetchone()["id"])
    files, deps = netsrc.find(cfg, g)
    assert set(files) == {"1941j.zip"} and [(r, n) for r, _s, n in deps] == [("1941.zip", 6)]
    dst = tmp / "cache" / "fbneo" / "1941.zip"
    p = partial.Partial(deps[0][1], dst, 6, 0)
    p.run(streams=1)
    assert dst.read_bytes() == b"RODZIC"


def test_login_stops_at_definite_rejection(monkeypatch):
    """Serwer nie zna urządzenia (albo ma inny certyfikat): koniec prób od razu, bez czekania
    na martwe adresy, i bez kolejnej rundy przez REJECT_TTL — pasek nie wisi na „łączę”."""
    from emustart import netsrc
    asked = []

    def login(did, sp):
        asked.append(sp)
        if sp[1] == "martwy":
            raise TimeoutError("timed out")
        raise netsrc.Rejected("nieznane urządzenie")
    monkeypatch.setattr(netsrc, "_login", login)
    monkeypatch.setattr(netsrc, "_hint", [])
    monkeypatch.setattr(netsrc, "_cfg_ref", [{}])
    monkeypatch.setattr(netsrc, "candidates", lambda cfg: [("https", "martwy", 1), ("https", "tunel", 443),
                                                          ("https", "inny", 8741)])
    monkeypatch.setitem(netsrc._signer, "d1", ("n", "tpm"))
    netsrc.drop_token()
    assert netsrc.session_token("d1") == ""
    assert [a[1] for a in asked] == ["martwy", "tunel"]                    # „inny” już niepytany
    assert "zaloguj się ponownie" in netsrc.rejected()
    assert netsrc.session_token("d1") == "" and len(asked) == 2            # bez kolejnej rundy
    netsrc.drop_token()
    assert netsrc.rejected() == ""
