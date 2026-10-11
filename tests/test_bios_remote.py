"""Serwer: BIOS-y rdzeni RetroArcha dla telefonu (/v1/bios) — lista z pliku .info,
plik po ścieżce, bez wychodzenia poza foldery BIOS-ów."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from emustart import remote, server

from test_core import _write


def _get(base, path, key="k"):
    req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as ex:
        return ex.code, ex.read()


def test_bios_for_core_and_file(tmp_path, monkeypatch):
    ra = tmp_path / "RetroArch"
    _write(ra / "retroarch.exe", b"")
    _write(ra / "info" / "puae_libretro.info",
           b'firmware0_path = "kick34005.A500"\nfirmware1_path = "kick40068.A1200"\nfirmware2_path = "../x"\n')
    _write(ra / "info" / "swanstation_libretro.info", b'firmware0_path = "scph5501.bin"\n')
    bios = tmp_path / "bios"
    _write(bios / "kick40068.A1200", b"KS31")
    _write(bios / "scph5501.bin", b"PS1")
    _write(tmp_path / "sekret.txt", b"nie")

    class Api:
        _cfg = {"server_token": "k", "bios_dir": str(bios),
                "systems": {"amiga": {"exe": str(ra / "retroarch.exe")}}}
    monkeypatch.setattr(remote, "API", Api())
    base = server.start(0)
    st, body = _get(base, "/v1/bios?core=puae")
    assert st == 200 and json.loads(body)["files"] == ["kick40068.A1200"]   # tylko te, które serwer ma
    assert json.loads(_get(base, "/v1/bios?core=swanstation")[1])["files"] == ["scph5501.bin"]
    assert _get(base, "/v1/bios?name=kick40068.A1200") == (200, b"KS31")
    assert _get(base, "/v1/bios?name=../sekret.txt")[0] == 400
    assert _get(base, "/v1/bios?name=kick34005.A500")[0] == 404
    assert _get(base, "/v1/bios?name=kick40068.A1200", key="zly")[0] == 401


def test_arcade_game_files_include_parent_and_bios(env, monkeypatch):
    """Telefon dostaje grę arcade z rodzicem i BIOS-em (neogeo.zip) — jak Windows."""
    from emustart import library, scanner
    cfg, roms, tmp = env
    _write(roms / "fbneo" / "mslug2.zip", b"g" * 10)
    _write(roms / "fbneo" / "neogeo.zip", b"b" * 7)
    scanner.scan_system("fbneo", roms / "fbneo")
    with library.db() as c:
        c.execute("INSERT INTO arcade_sets(name, title, romof) VALUES('mslug2', 'Metal Slug 2', 'neogeo')")
        c.execute("INSERT INTO arcade_sets(name, title, romof) VALUES('neogeo', 'Neo Geo', '')")
    gid = next(r["id"] for r in library.db().execute("SELECT id, rel FROM games") if r["rel"] == "mslug2.zip")
    g = library.game(gid)
    deps = remote._arcade_deps(g)
    assert [(r, s) for r, _p, s in deps] == [("neogeo.zip", 7)]


from test_core import env  # noqa: E402,F401  (fixture)
