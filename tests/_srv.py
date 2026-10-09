"""Pomocnik testów: folder profili przez serwer EmuStart (nasfs.RemoteFs) zamiast SMB."""

from __future__ import annotations

from pathlib import Path

from emustart import nasfs, netsrc, remote, server

KEY = "k" * 32


class _FakeApi:
    _cfg = {"server_token": KEY}


def use_server(cfg: dict, nas: Path, monkeypatch) -> None:
    nas.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(remote, "API", _FakeApi())
    monkeypatch.setattr(remote, "NAS_ROOT", nas)
    monkeypatch.setattr(remote, "NAS_ID", nasfs.root_id(nas, create=True))
    url = server.start(0, bind="127.0.0.1")
    cfg.update(server_url=url.replace("http://", ""), server_key=KEY)
    netsrc._info.clear()
    nasfs.reset()
    assert isinstance(nasfs.backend(cfg), nasfs.RemoteFs)
