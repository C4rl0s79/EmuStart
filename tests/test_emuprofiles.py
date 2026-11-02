"""Profile emulatorów graczy na serwerze (Xenia Edge: XUID): urządzenie zgłasza profile swojego konta,
administrator przypina profil do konta w panelu, urządzenie odczytuje przypięty profil."""

from __future__ import annotations

import pytest

from emustart import emuprofiles, paths, srvadmin

from test_savesync_server import req, srv  # noqa: F401  (fixture)

X1, X2 = "E030000000983487", "E0300000AAAABBBB"


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "SERVER", tmp_path / "server")
    emuprofiles._db = None
    yield
    emuprofiles._db = None


def test_report_and_pin(srv):
    assert req(srv, "PUT", "/v1/emuprofiles", body={"emu": "xenia_edge", "xuids": [X1, "zły", X2]})[0] == 200
    st = req(srv, "GET", "/v1/emuprofiles")[1]
    assert st["xenia_edge"]["pinned"] == "" and sorted(p["xuid"] for p in st["xenia_edge"]["reported"]) == [X1, X2]
    assert srvadmin.emu_profile_pin(None, {"name": "Jezus", "emu": "xenia_edge", "xuid": X1})["ok"]
    assert req(srv, "GET", "/v1/emuprofiles", dev="tel")[1]["xenia_edge"]["pinned"] == X1   # każde urządzenie konta
    assert req(srv, "GET", "/v1/emuprofiles", dev="ola-pc")[1]["xenia_edge"]["pinned"] == ""  # inne konto — swoje


def test_pin_only_reported_profile_or_unpin(srv):
    assert not srvadmin.emu_profile_pin(None, {"name": "Jezus", "emu": "xenia_edge", "xuid": X1})["ok"]
    req(srv, "PUT", "/v1/emuprofiles", body={"emu": "xenia_edge", "xuids": [X1]})
    assert not srvadmin.emu_profile_pin(None, {"name": "Brak", "emu": "xenia_edge", "xuid": X1})["ok"]
    assert not srvadmin.emu_profile_pin(None, {"name": "Jezus", "emu": "inny", "xuid": X1})["ok"]
    assert srvadmin.emu_profile_pin(None, {"name": "Jezus", "emu": "xenia_edge", "xuid": X1})["ok"]
    assert srvadmin.emu_profile_pin(None, {"name": "Jezus", "emu": "xenia_edge", "xuid": ""})["ok"]     # odpięcie
    assert srvadmin.emu_profiles(None, {"name": "Jezus"})["profiles"]["xenia_edge"]["pinned"] == ""


def test_profile_of_another_account_cannot_be_pinned(srv):
    """Profil zgłoszony przez urządzenie Oli nie może zostać przypięty kontu Jezus (cudze zapisy)."""
    req(srv, "PUT", "/v1/emuprofiles", dev="ola-pc", body={"emu": "xenia_edge", "xuids": [X2]})
    assert not srvadmin.emu_profile_pin(None, {"name": "Jezus", "emu": "xenia_edge", "xuid": X2})["ok"]
    assert srvadmin.emu_profile_pin(None, {"name": "Ola", "emu": "xenia_edge", "xuid": X2})["ok"]
