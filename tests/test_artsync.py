"""Klient silnika grafik użytkowników (emustart/artsync.py) z prawdziwym serwerem testowym: wgranie, wybory między
urządzeniami, kopia lokalna działająca bez serwera."""

from __future__ import annotations

import pytest

from emustart import artsync, paths

from test_core import _write, env  # noqa: F401  (fixture)
from test_savesync_server import srv  # noqa: F401  (fixture)
from test_syncclient import PC, TEL, net  # noqa: F401  (fixture)

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 40


@pytest.fixture()
def two(net, tmp_path, monkeypatch):
    cfgs, tmp = net
    monkeypatch.setattr(paths, "DATA", tmp_path / "dane")
    _write(tmp / "logo.png", PNG)
    return cfgs, tmp


def test_upload_on_one_device_shows_and_downloads_on_another(two):
    cfgs, tmp = two
    up = artsync.upload(cfgs[PC], PC, str(tmp / "logo.png"), "navgrp:grp:pc", "logo")
    assert up["ext"] == "png"
    g = artsync.gallery(cfgs[TEL], TEL, "navgrp:grp:pc", "logo")
    assert g["items"][0]["sha"] == up["sha"] and g["items"][0]["mine"]
    choice = {"type": "user", "sha": up["sha"], "ext": "png"}
    key = "tv|navgrp:grp:pc|logo"
    artsync.sync(cfgs[PC], PC, [{"key": key, "choice": choice, "at": 100}])
    assert artsync.sync(cfgs[TEL], TEL, [])["choices"][key]["choice"] == choice

    # plik jest już w kopii po wgraniu; po skasowaniu kopii pobiera się z serwera i zgadza się skrót
    cached = artsync._cache() / f"{up['sha']}.png"
    cached.unlink()
    p = artsync.file(cfgs[TEL], TEL, up["sha"], "png")["path"]
    assert open(p, "rb").read() == PNG
    off = {k: v for k, v in cfgs[TEL].items() if k != "dev"}
    assert artsync.file(off, TEL, up["sha"], "png")["path"] == p       # bez serwera: kopia lokalna
    assert artsync.file(cfgs[TEL], TEL, "0" * 64, "png") == {"path": ""}
    assert artsync.file(cfgs[TEL], TEL, "../x", "png") == {"path": ""}
