"""Klucz certyfikatu serwera: zaszyfrowany na dysku (hasło w DPAPI komputera), przeniesienie
serwera na inny komputer hasłem przeniesienia (emustart/tls.py)."""

import os

import pytest

from emustart import secure, tls

pytestmark = pytest.mark.skipif(os.name != "nt", reason="DPAPI — Windows")


def test_new_key_is_encrypted_and_loads(tmp_path):
    cert, key, fp = tls.ensure(tmp_path)
    assert b"ENCRYPTED" in key.read_bytes() and b"BEGIN PRIVATE KEY" not in key.read_bytes()
    assert (tmp_path / tls.PASS).is_file() and tls.STATE["state"] == "ok"
    tls.server_context(cert, key)                                      # wczytuje się hasłem z DPAPI
    assert tls.ensure(tmp_path)[2] == fp                               # drugi start — ten sam certyfikat


def test_plain_key_from_older_server_is_converted(tmp_path):
    from cryptography.hazmat.primitives import serialization
    cert, key, fp = tls.ensure(tmp_path)
    k = serialization.load_pem_private_key(key.read_bytes(), password=tls.key_password(tmp_path))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))     # jak serwer 0.44
    (tmp_path / tls.PASS).unlink()
    assert tls.ensure(tmp_path)[2] == fp
    assert b"ENCRYPTED" in key.read_bytes()


def test_moved_folder_is_locked_until_transfer_password(tmp_path):
    cert, key, fp = tls.ensure(tmp_path)
    with pytest.raises(ValueError):
        tls.import_move(tmp_path, "Haslo-przeniesienia1")                # bez pliku przeniesienia
    tls.export_move(tmp_path, "Haslo-przeniesienia1", {"art_keys": {"sgdb_key": "ABC"}})
    # „inny komputer”: hasło klucza zapisane DPAPI innej maszyny — tu nie do odszyfrowania
    (tmp_path / tls.PASS).write_bytes(b"\x01\x00obce dpapi")
    assert tls.ensure(tmp_path) is None and tls.STATE["state"] == "locked"
    with pytest.raises(ValueError, match="Złe hasło"):
        tls.import_move(tmp_path, "zle-haslo")
    extra = tls.import_move(tmp_path, "Haslo-przeniesienia1")
    assert extra == {"art_keys": {"sgdb_key": "ABC"}}
    assert not (tmp_path / tls.MOVE).exists()                          # plik przeniesienia sprzątnięty
    got = tls.ensure(tmp_path)
    assert got and got[2] == fp and tls.STATE["state"] == "ok"        # ten sam certyfikat — urządzenia działają
    tls.server_context(got[0], got[1])


def test_machine_dpapi_roundtrip():
    blob = secure.protect_machine(b"tajne")
    assert blob and blob != b"tajne" and secure.unprotect_machine(blob) == b"tajne"
    assert secure.unprotect_machine(b"smieci") is None
