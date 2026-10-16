import hashlib
import json

import pytest

from emustart import savesync


@pytest.fixture()
def sync_root(tmp_path, monkeypatch):
    root = tmp_path / "savesync"
    root.mkdir()
    monkeypatch.setattr(savesync, "_root", lambda: root)
    return root


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_manifest_starts_empty(sync_root):
    manifest = savesync.manifest_for_client("acc", "snes", "chrono")
    assert manifest["revision"] == 0
    assert manifest["emulators"] == {}


def test_upload_commit_creates_revision(sync_root):
    data = b"save-bundle"
    init = savesync.init_upload("acc", "dev", "snes", "chrono", "retroarch", "snes9x",
                                "saves", 0, _sha(data), len(data))
    assert init["ok"]

    stored = savesync.store_upload(init["upload_url"].rsplit("/", 1)[1], data)
    assert stored["ok"]

    commit = savesync.commit_upload("acc", "dev", init["upload_id"], 0, _sha(data), len(data))
    assert commit["ok"]
    assert commit["revision"] == 1

    manifest = savesync.manifest_load("acc", "snes", "chrono")
    assert manifest["revision"] == 1
    bundle = manifest["emulators"]["retroarch"]["cores"]["snes9x"]["bundles"]["saves"]
    assert bundle["sha256"] == _sha(data)


def test_stale_base_revision_conflicts(sync_root):
    data = b"first"
    init = savesync.init_upload("acc", "dev", "snes", "chrono", "retroarch", "snes9x",
                                "saves", 0, _sha(data), len(data))
    savesync.store_upload(init["upload_url"].rsplit("/", 1)[1], data)
    savesync.commit_upload("acc", "dev", init["upload_id"], 0, _sha(data), len(data))

    second = b"second"
    init2 = savesync.init_upload("acc", "dev", "snes", "chrono", "retroarch", "snes9x",
                                 "saves", 0, _sha(second), len(second))
    assert init2.get("conflict") is True
    assert init2["current_revision"] == 1


def test_commit_verifies_sha256(sync_root):
    data = b"payload"
    init = savesync.init_upload("acc", "dev", "snes", "chrono", "retroarch", "snes9x",
                                "saves", 0, _sha(data), len(data))
    token = init["upload_url"].rsplit("/", 1)[1]
    assert savesync.store_upload(token, b"tampered")["ok"] is False


def test_previous_bundle_is_backed_up(sync_root):
    first = b"first"
    init = savesync.init_upload("acc", "dev", "snes", "chrono", "retroarch", "snes9x",
                                "saves", 0, _sha(first), len(first))
    savesync.store_upload(init["upload_url"].rsplit("/", 1)[1], first)
    savesync.commit_upload("acc", "dev", init["upload_id"], 0, _sha(first), len(first))

    second = b"second"
    init2 = savesync.init_upload("acc", "dev", "snes", "chrono", "retroarch", "snes9x",
                                 "saves", 1, _sha(second), len(second))
    savesync.store_upload(init2["upload_url"].rsplit("/", 1)[1], second)
    result = savesync.commit_upload("acc", "dev", init2["upload_id"], 1, _sha(second), len(second))
    assert result["ok"]

    backups = list((sync_root / "acc" / "snes" / "chrono" / "backups").rglob("saves.zip"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == first


def test_retroarch_cores_are_separate(sync_root):
    for core, payload in (("snes9x", b"snes9x"), ("bsnes", b"bsnes")):
        init = savesync.init_upload("acc", "dev", "snes", "chrono", "retroarch", core,
                                    "saves", 0, _sha(payload), len(payload))
        savesync.store_upload(init["upload_url"].rsplit("/", 1)[1], payload)
        result = savesync.commit_upload("acc", "dev", init["upload_id"], 0, _sha(payload), len(payload))
        assert result["ok"]

    manifest = savesync.manifest_load("acc", "snes", "chrono")
    cores = manifest["emulators"]["retroarch"]["cores"]
    assert cores["snes9x"]["bundles"]["saves"]["sha256"] == _sha(b"snes9x")
    assert cores["bsnes"]["bundles"]["saves"]["sha256"] == _sha(b"bsnes")
