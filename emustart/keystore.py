"""keystore — klucz urządzenia (EC P-256) do logowania podpisem (emustart/enroll.py).

Klucz prywatny nie opuszcza urządzenia:
- Windows z TPM: klucz tworzony w TPM („Microsoft Platform Crypto Provider”, NCrypt) — nie da
  się go wyeksportować; podpis robi TPM;
- Windows bez TPM (albo TPM nie działa): klucz programowy zaszyfrowany DPAPI (tylko to konto
  Windows na tym komputerze może go odszyfrować);
- inne systemy (przyszły Linux): plik z uprawnieniami tylko dla właściciela.

`create(name)` → (rodzaj, klucz publiczny PEM); `sign(name, dane)` → podpis DER; `delete(name)`.
"""

from __future__ import annotations

import ctypes
import hashlib
import logging
import os
from pathlib import Path

from emustart import paths

log = logging.getLogger("emustart.keystore")

TPM_PROVIDER = "Microsoft Platform Crypto Provider"
NCRYPT_ECDSA_P256 = "ECDSA_P256"
ECCPUBLIC_BLOB = "ECCPUBLICBLOB"
USE_TPM = True                   # testy: False (bez kluczy w prawdziwym TPM)


def _dir() -> Path:
    return paths.DATA / "klucze"


# ── Windows: TPM przez NCrypt ──
class _NC:
    def __init__(self):
        self.dll = ctypes.WinDLL("ncrypt.dll")

    def _ok(self, r: int, what: str) -> None:
        if r != 0:
            raise OSError(f"{what}: 0x{r & 0xFFFFFFFF:08X}")

    def provider(self):
        h = ctypes.c_void_p()
        self._ok(self.dll.NCryptOpenStorageProvider(ctypes.byref(h), ctypes.c_wchar_p(TPM_PROVIDER), 0),
                 "TPM niedostępny")
        return h

    def free(self, h) -> None:
        if h and h.value:
            self.dll.NCryptFreeObject(h)

    def create(self, name: str) -> bytes:
        prov = self.provider()
        key = ctypes.c_void_p()
        try:
            self._ok(self.dll.NCryptCreatePersistedKey(prov, ctypes.byref(key), ctypes.c_wchar_p(NCRYPT_ECDSA_P256),
                                                       ctypes.c_wchar_p(name), 0, 0x80),   # NCRYPT_OVERWRITE_KEY_FLAG
                     "tworzenie klucza w TPM")
            self._ok(self.dll.NCryptFinalizeKey(key, 0), "zapis klucza w TPM")
            return self._export(key)
        finally:
            self.free(key)
            self.free(prov)

    def _open(self, name: str):
        prov = self.provider()
        key = ctypes.c_void_p()
        r = self.dll.NCryptOpenKey(prov, ctypes.byref(key), ctypes.c_wchar_p(name), 0, 0)
        self.free(prov)
        self._ok(r, "klucz w TPM")
        return key

    def _export(self, key) -> bytes:
        n = ctypes.c_ulong()
        self._ok(self.dll.NCryptExportKey(key, None, ctypes.c_wchar_p(ECCPUBLIC_BLOB), None, None, 0,
                                          ctypes.byref(n), 0), "eksport klucza publicznego")
        buf = ctypes.create_string_buffer(n.value)
        self._ok(self.dll.NCryptExportKey(key, None, ctypes.c_wchar_p(ECCPUBLIC_BLOB), None, buf, n,
                                          ctypes.byref(n), 0), "eksport klucza publicznego")
        return buf.raw[:n.value]

    def public(self, name: str) -> bytes:
        key = self._open(name)
        try:
            return self._export(key)
        finally:
            self.free(key)

    def sign(self, name: str, digest: bytes) -> bytes:
        key = self._open(name)
        try:
            n = ctypes.c_ulong()
            self._ok(self.dll.NCryptSignHash(key, None, digest, len(digest), None, 0, ctypes.byref(n), 0), "podpis")
            buf = ctypes.create_string_buffer(n.value)
            self._ok(self.dll.NCryptSignHash(key, None, digest, len(digest), buf, n, ctypes.byref(n), 0), "podpis")
            return buf.raw[:n.value]
        finally:
            self.free(key)

    def delete(self, name: str) -> None:
        key = self._open(name)
        self.dll.NCryptDeleteKey(key, 0)               # zwalnia uchwyt


def _blob_to_pem(blob: bytes) -> str:
    """BCRYPT_ECCPUBLIC_BLOB (ECS1, długość, X, Y) → PEM klucza publicznego."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    size = int.from_bytes(blob[4:8], "little")
    x = int.from_bytes(blob[8:8 + size], "big")
    y = int.from_bytes(blob[8 + size:8 + 2 * size], "big")
    pub = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    return pub.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _raw_to_der(sig: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    h = len(sig) // 2
    return encode_dss_signature(int.from_bytes(sig[:h], "big"), int.from_bytes(sig[h:], "big"))


# ── klucz programowy (DPAPI na Windows, plik 0600 gdzie indziej) ──
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt = ctypes.WinDLL("crypt32.dll")
    kernel = ctypes.WinDLL("kernel32.dll")
    src = ctypes.create_string_buffer(data, len(data))
    inp = _Blob(len(data), ctypes.cast(src, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    ok = fn(ctypes.byref(inp), None, None, None, None, 0x1, ctypes.byref(out))   # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI: " + str(ctypes.get_last_error()))
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel.LocalFree(out.pbData)


def _soft_path(name: str) -> Path:
    return _dir() / (hashlib.sha1(name.encode()).hexdigest()[:16] + ".key")


def _soft_save(name: str, pem: bytes) -> None:
    p = _soft_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        p.write_bytes(b"DPAPI" + _dpapi(pem, True))
    else:
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)


def _soft_load(name: str):
    from cryptography.hazmat.primitives import serialization
    raw = _soft_path(name).read_bytes()
    pem = _dpapi(raw[5:], False) if raw.startswith(b"DPAPI") else raw
    return serialization.load_pem_private_key(pem, None)


# ── interfejs ──
def create(name: str, prefer_tpm: bool | None = None) -> tuple[str, str]:
    """Nowy klucz urządzenia → (rodzaj: tpm|dpapi|plik, klucz publiczny PEM)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    if os.name == "nt" and (USE_TPM if prefer_tpm is None else prefer_tpm):
        try:
            pem = _blob_to_pem(_NC().create(name))
            log.info("klucz urządzenia w TPM (%s)", name)
            return "tpm", pem
        except OSError as ex:
            log.info("TPM: %s — klucz programowy (DPAPI)", ex)
    k = ec.generate_private_key(ec.SECP256R1())
    _soft_save(name, k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    pem = k.public_key().public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return ("dpapi" if os.name == "nt" else "plik"), pem


def sign(name: str, kind: str, data: bytes) -> bytes:
    """Podpis ECDSA-SHA256 (DER) kluczem urządzenia."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    if kind == "tpm":
        return _raw_to_der(_NC().sign(name, hashlib.sha256(data).digest()))
    return _soft_load(name).sign(data, ec.ECDSA(hashes.SHA256()))


def delete(name: str, kind: str) -> None:
    try:
        if kind == "tpm":
            _NC().delete(name)
        else:
            _soft_path(name).unlink(missing_ok=True)
    except OSError as ex:
        log.info("usuwanie klucza %s: %s", name, ex)
