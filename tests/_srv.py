"""Pomocnik testów: folder profili przez serwer EmuStart (nasfs.RemoteFs) zamiast SMB.

Klient łączy się jak prawdziwy — portem szyfrowanym z przypiętym certyfikatem serwera
(od 0.43 zwykły port obsługuje API tylko z tego komputera); klucz: wspólny klucz serwera,
który działa tylko lokalnie (127.0.0.1)."""

from __future__ import annotations

from pathlib import Path

from emustart import nasfs, netsrc, paths, remote, server, tls

KEY = "k" * 32


class _FakeApi:
    _cfg = {"server_token": KEY}


def tls_server(monkeypatch) -> tuple[str, str]:
    """Port szyfrowany serwera na 127.0.0.1 → (adres https, odcisk certyfikatu)."""
    cert, key, fp = tls.ensure(paths.SERVER)
    port = server.start_tls(0, tls.server_context(cert, key), bind="127.0.0.1")
    monkeypatch.setattr(remote, "TLS", {"port": port, "fp": fp})
    return f"https://127.0.0.1:{port}", fp


def use_server(cfg: dict, nas: Path, monkeypatch) -> None:
    nas.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(remote, "API", _FakeApi())
    monkeypatch.setattr(remote, "NAS_ROOT", nas)
    monkeypatch.setattr(remote, "NAS_ID", nasfs.root_id(nas, create=True))
    url, fp = tls_server(monkeypatch)
    cfg.update(server_url=url, server_fp=fp, server_key=KEY)
    netsrc._info.clear()
    nasfs.reset()
    assert isinstance(nasfs.backend(cfg), nasfs.RemoteFs)
