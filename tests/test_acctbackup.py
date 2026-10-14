"""Panel serwera → konta: przeglądanie profilu i kopie zapasowe (emustart/acctbackup.py)."""

import os
import time
import zipfile

import pytest

from emustart import accounts, acctbackup, config, remote


@pytest.fixture()
def prof(tmp_path, monkeypatch):
    root = tmp_path / "profiles"
    p = root / "Jezus"
    (p / "save" / "psx").mkdir(parents=True)
    (p / "save" / "psx" / "gra.srm").write_bytes(b"stary zapis")
    (p / "emustart.json").write_text("{}", encoding="utf-8")
    (p / "_backup" / "x").mkdir(parents=True)
    (p / "_backup" / "x" / "duzy.bin").write_bytes(b"0" * 1000)
    monkeypatch.setattr(remote, "NAS_ROOT", root)
    cfg = config.load()
    cfg["backup_dir"] = str(tmp_path / "kopie")
    accounts.reset()
    return cfg, p


def test_browse_and_backup_without_file_history(prof):
    cfg, p = prof
    b = acctbackup.browse("Jezus")
    assert {x["name"] for x in b["parts"]} == {"save", "(pliki konta)"}     # bez _backup
    r = acctbackup.backup(cfg, "Jezus")
    with zipfile.ZipFile(acctbackup._dir(cfg) / "Jezus" / r["file"]) as z:
        names = z.namelist()
    assert "_konto.json" in names and "profil/save/psx/gra.srm" in names
    assert not any("_backup" in n for n in names)
    assert acctbackup.backups(cfg, "Jezus")[0]["file"] == r["file"]
    with pytest.raises(ValueError):
        acctbackup.browse("..\\inne")


def test_restore_keeps_current_state_first(prof):
    cfg, p = prof
    first = acctbackup.backup(cfg, "Jezus")["file"]
    (p / "save" / "psx" / "gra.srm").write_bytes(b"nowy zapis")
    (p / "save" / "psx" / "druga.srm").write_bytes(b"tylko teraz")
    r = acctbackup.restore(cfg, "Jezus", first)
    assert r["ok"] and r["files"] == 2
    assert (p / "save" / "psx" / "gra.srm").read_bytes() == b"stary zapis"
    assert (p / "save" / "psx" / "druga.srm").is_file()          # plików spoza kopii nie rusza
    with zipfile.ZipFile(acctbackup._dir(cfg) / "Jezus" / r["before"]) as z:   # stan sprzed — w kopii
        assert z.read("profil/save/psx/gra.srm") == b"nowy zapis"
    assert not acctbackup.restore(cfg, "Jezus", "..\\x.zip")["ok"]


def test_restore_rejects_paths_outside_profile(prof):
    cfg, p = prof
    d = acctbackup._dir(cfg) / "Jezus"
    d.mkdir(parents=True)
    with zipfile.ZipFile(d / "Jezus-zla.zip", "w") as z:
        z.writestr("profil/../../poza.txt", b"x")
    r = acctbackup.restore(cfg, "Jezus", "Jezus-zla.zip")
    assert not r["ok"] and not (p.parent.parent / "poza.txt").exists()


def test_keep_limit_and_auto(prof, monkeypatch):
    cfg, p = prof
    cfg["backup_keep"] = 2
    for _ in range(3):
        acctbackup.backup(cfg, "Jezus")
    assert len(acctbackup.backups(cfg, "Jezus")) == 2
    assert acctbackup.auto_once(cfg) == 0                         # świeża kopia — nic
    for b in acctbackup.backups(cfg, "Jezus"):                    # kopie sprzed 2 dni
        f = acctbackup._dir(cfg) / "Jezus" / b["file"]
        old = time.time() - 2 * 86400
        os.utime(f, (old, old))
    old = time.time() - 3 * 86400
    for f in p.rglob("*"):
        os.utime(f, (old, old))
    assert acctbackup.auto_once(cfg) == 0                         # bez zmian od kopii — nic
    (p / "save" / "psx" / "gra.srm").write_bytes(b"zmiana")
    assert acctbackup.auto_once(cfg) == 1
    cfg["backup_auto"] = False
    assert acctbackup.auto_once(cfg) == 0
