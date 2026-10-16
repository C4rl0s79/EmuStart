import hashlib
import io
import json
import zipfile

import pytest

from emustart import savesync_client


class FakeAdapter:
    family = "retroarch"

    def save_dirs(self):
        return []

    def state_dirs(self):
        return []


def test_bundle_kinds_for_retroarch():
    assert savesync_client._bundle_kinds(FakeAdapter()) == ["saves", "states"]


def test_bundle_for_uses_core():
    manifest = {"emulators": {"retroarch": {"cores": {
        "snes9x": {"bundles": {"saves": {"revision": 3}}},
        "bsnes": {"bundles": {"saves": {"revision": 7}}},
    }}}}
    assert savesync_client._bundle_for(manifest, FakeAdapter(), "snes9x", "saves")["revision"] == 3
    assert savesync_client._bundle_for(manifest, FakeAdapter(), "bsnes", "saves")["revision"] == 7


def test_state_key_separates_cores():
    game = {"id": 12}
    assert savesync_client._state_key(game, "retroarch", "snes9x", "saves") != \
        savesync_client._state_key(game, "retroarch", "bsnes", "saves")


def test_pack_creates_zip_with_relative_paths(tmp_path):
    file = tmp_path / "save.srm"
    file.write_bytes(b"save")
    payload = savesync_client._pack([(file, "retroarch/Snes9x/save.srm")])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert archive.namelist() == ["retroarch/Snes9x/save.srm"]
        assert archive.read("retroarch/Snes9x/save.srm") == b"save"


def test_sha_matches_server_format():
    assert savesync_client._sha(b"data") == hashlib.sha256(b"data").hexdigest()


def test_device_id_from_server_key():
    assert savesync_client._device_id({"server_key": "dev:abc123"}) == "abc123"
    assert savesync_client._device_id({"server_key": ""}) == "local"
