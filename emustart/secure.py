"""secure — szyfrowanie sekretów: TPM → DPAPI → jawny (Windows, ctypes).

Kopia core/secure.py z PyLinksWeb z jedną zmianą: nazwa klucza TPM jest
parametrem. EmuStart szyfruje własnym kluczem (EmuStartSecretsKey), a klucze
importowane z PyLinks odszyfrowuje kluczem PyLinks (PyLinksSecretsKey) — oba
leżą w TPM tego samego konta Windows.

Preferuje sprzętowy TPM (NCrypt „Microsoft Platform Crypto Provider", klucz
RSA-2048 nieeksportowalny, RSA-OAEP) dla małych sekretów (≤180 B: klucze API,
SteamID64). Gdy TPM niedostępny/niesprawny → DPAPI (CryptProtectData, konto
Windows). Poza Windowsem → tekst jawny. Wartości na dysku z prefiksem:
"tpm:<b64>" albo "dpapi:<b64>".
"""

from __future__ import annotations

import base64
import os

KEY_EMUSTART = "EmuStartSecretsKey"
KEY_PYLINKS = "PyLinksSecretsKey"
_TPM_OK = None            # None=nietestowany, True/False=wynik selftestu


# ── TPM (NCrypt / Platform Crypto Provider) ────────────────────────────────
def _tpm_handles(key_name: str):
    import ctypes
    from ctypes import wintypes
    ncrypt = ctypes.windll.ncrypt
    SILENT = 0x00000040
    hProv = wintypes.HANDLE()
    if ncrypt.NCryptOpenStorageProvider(
            ctypes.byref(hProv),
            ctypes.c_wchar_p("Microsoft Platform Crypto Provider"), 0) != 0:
        raise OSError("brak Platform Crypto Provider (TPM)")
    hKey = wintypes.HANDLE()
    st = ncrypt.NCryptOpenKey(hProv, ctypes.byref(hKey),
                              ctypes.c_wchar_p(key_name), 0, SILENT)
    if st != 0:
        st = ncrypt.NCryptCreatePersistedKey(
            hProv, ctypes.byref(hKey), ctypes.c_wchar_p("RSA"),
            ctypes.c_wchar_p(key_name), 0, 0)
        if st != 0:
            ncrypt.NCryptFreeObject(hProv)
            raise OSError(f"NCryptCreatePersistedKey: {st & 0xFFFFFFFF:#x}")
        length = ctypes.c_ulong(2048)
        ncrypt.NCryptSetProperty(hKey, ctypes.c_wchar_p("Length"),
                                 ctypes.byref(length), ctypes.sizeof(length), 0)
        if ncrypt.NCryptFinalizeKey(hKey, 0) != 0:
            ncrypt.NCryptFreeObject(hKey); ncrypt.NCryptFreeObject(hProv)
            raise OSError("NCryptFinalizeKey nieudane")
    return ncrypt, hProv, hKey


def _oaep_pad():
    import ctypes

    class OAEP(ctypes.Structure):
        _fields_ = [("pszAlgId", ctypes.c_wchar_p),
                    ("pbLabel", ctypes.c_void_p), ("cbLabel", ctypes.c_ulong)]
    return OAEP("SHA256", None, 0)


def _tpm_wrap(raw: bytes, key_name: str = KEY_EMUSTART):
    try:
        import ctypes
        OAEP_PAD = 0x00000004
        ncrypt, hProv, hKey = _tpm_handles(key_name)
        try:
            pad = _oaep_pad(); cb = ctypes.c_ulong(0)
            if ncrypt.NCryptEncrypt(hKey, raw, len(raw), ctypes.byref(pad),
                                    None, 0, ctypes.byref(cb), OAEP_PAD) != 0:
                return None
            buf = ctypes.create_string_buffer(cb.value)
            if ncrypt.NCryptEncrypt(hKey, raw, len(raw), ctypes.byref(pad),
                                    buf, cb.value, ctypes.byref(cb), OAEP_PAD) != 0:
                return None
            return buf.raw[:cb.value]
        finally:
            ncrypt.NCryptFreeObject(hKey); ncrypt.NCryptFreeObject(hProv)
    except Exception:
        return None


def _tpm_unwrap(blob: bytes, key_name: str = KEY_EMUSTART):
    try:
        import ctypes
        OAEP_PAD = 0x00000004
        ncrypt, hProv, hKey = _tpm_handles(key_name)
        try:
            pad = _oaep_pad(); cb = ctypes.c_ulong(0)
            if ncrypt.NCryptDecrypt(hKey, blob, len(blob), ctypes.byref(pad),
                                    None, 0, ctypes.byref(cb), OAEP_PAD) != 0:
                return None
            buf = ctypes.create_string_buffer(cb.value)
            if ncrypt.NCryptDecrypt(hKey, blob, len(blob), ctypes.byref(pad),
                                    buf, cb.value, ctypes.byref(cb), OAEP_PAD) != 0:
                return None
            return buf.raw[:cb.value]
        finally:
            ncrypt.NCryptFreeObject(hKey); ncrypt.NCryptFreeObject(hProv)
    except Exception:
        return None


def _tpm_ok() -> bool:
    """Round-trip losowego tokenu przez TPM zanim na nim polegniemy."""
    global _TPM_OK
    if _TPM_OK is not None:
        return _TPM_OK
    _TPM_OK = False
    if os.name == "nt":
        try:
            tok = b"emustart-selftest-" + os.urandom(8)
            w = _tpm_wrap(tok)
            _TPM_OK = bool(w) and _tpm_unwrap(w) == tok
        except Exception:
            _TPM_OK = False
    return _TPM_OK


# ── DPAPI (ctypes) ─────────────────────────────────────────────────────────
def _dpapi(func_name: str, data: bytes):
    try:
        import ctypes, ctypes.wintypes as wt

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        bi = BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)),
                                         ctypes.POINTER(ctypes.c_char)))
        bo = BLOB()
        fn = getattr(ctypes.windll.crypt32, func_name)
        if not fn(ctypes.byref(bi), None, None, None, None, 0, ctypes.byref(bo)):
            return None
        try:
            return ctypes.string_at(bo.pbData, bo.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(bo.pbData)
    except Exception:
        return None


def _dpapi_ok() -> bool:
    if os.name != "nt":
        return False
    tok = b"x" + os.urandom(6)
    enc = _dpapi("CryptProtectData", tok)
    return bool(enc) and _dpapi("CryptUnprotectData", enc) == tok


# ── API modułu ─────────────────────────────────────────────────────────────
def available() -> bool:
    return os.name == "nt" and (_tpm_ok() or _dpapi_ok())


def backend() -> str:
    """Realnie użyty backend: 'tpm' | 'dpapi' | 'none'."""
    if os.name != "nt":
        return "none"
    if _tpm_ok():
        return "tpm"
    if _dpapi_ok():
        return "dpapi"
    return "none"


def protect(text: str) -> str:
    if not text or os.name != "nt":
        return text or ""
    if text.startswith(("tpm:", "dpapi:")):
        return text
    raw = text.encode("utf-8")
    if len(raw) <= 180 and _tpm_ok():
        blob = _tpm_wrap(raw)
        if blob is not None:
            return "tpm:" + base64.b64encode(blob).decode("ascii")
    enc = _dpapi("CryptProtectData", raw)
    if enc is not None:
        return "dpapi:" + base64.b64encode(enc).decode("ascii")
    return text


def unprotect(value: str, key_name: str = KEY_EMUSTART) -> str:
    if not value:
        return ""
    if value.startswith("tpm:"):
        out = _tpm_unwrap(base64.b64decode(value[4:]), key_name)
        return out.decode("utf-8") if out else ""
    if value.startswith("dpapi:"):
        out = _dpapi("CryptUnprotectData", base64.b64decode(value[6:]))
        return out.decode("utf-8") if out else ""
    return value
