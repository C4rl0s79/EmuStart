"""dstoken — token RetroAchievements w formacie DuckStation.

DuckStation nie trzyma tokenu RA otwartym tekstem: zapisuje go zaszyfrowanego
AES-128-CBC (dopełnienie zerami, wynik w base64). Klucz to SHA-256 z
identyfikatora komputera (MachineGuid; pomijany w trybie przenośnym) i nazwy
użytkownika, potem 100 razy SHA-256 z poprzedniego wyniku; pierwsze 16 bajtów
to klucz AES, kolejne 16 — wektor IV. Token zwykły (PCSX2, RetroArch,
logowanie w EmuStart) trzeba więc przed wpisaniem do DuckStation zaszyfrować,
inaczej DuckStation uzna go za nieważny i poprosi o zalogowanie.

AES przez Windows CNG (bcrypt.dll) — bez dodatkowych bibliotek.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import logging
import string

log = logging.getLogger("emustart.dstoken")

EXTRA_ROUNDS = 100
BLOCK = 16


def machine_key() -> bytes:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            v, _ = winreg.QueryValueEx(k, "MachineGuid")
        return str(v).encode("ascii", "replace")
    except OSError:
        return b""


def derive_key(username: str, portable: bool) -> bytes:
    h = hashlib.sha256()
    if not portable:
        h.update(machine_key())
    h.update(username.encode("utf-8"))
    key = h.digest()
    for _ in range(EXTRA_ROUNDS):
        key = hashlib.sha256(key).digest()
    return key


def _aes_cbc(key: bytes, iv: bytes, data: bytes, encrypt: bool) -> bytes:
    bc = ctypes.WinDLL("bcrypt")
    alg, hkey = ctypes.c_void_p(), ctypes.c_void_p()
    if bc.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ctypes.c_wchar_p("AES"), None, 0):
        raise OSError("BCryptOpenAlgorithmProvider")
    try:
        mode = ctypes.create_unicode_buffer("ChainingModeCBC")
        if bc.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"), mode, ctypes.sizeof(mode), 0):
            raise OSError("BCryptSetProperty")
        kb = ctypes.create_string_buffer(key, len(key))
        if bc.BCryptGenerateSymmetricKey(alg, ctypes.byref(hkey), None, 0, kb, len(key), 0):
            raise OSError("BCryptGenerateSymmetricKey")
        try:
            src = ctypes.create_string_buffer(data, len(data))
            ivb = ctypes.create_string_buffer(iv, len(iv))     # CNG zmienia IV w miejscu
            out = ctypes.create_string_buffer(len(data))
            n = ctypes.c_ulong()
            fn = bc.BCryptEncrypt if encrypt else bc.BCryptDecrypt
            if fn(hkey, src, len(data), None, ivb, len(iv), out, len(data), ctypes.byref(n), 0):
                raise OSError("BCryptEncrypt/Decrypt")
            return out.raw[:n.value]
        finally:
            bc.BCryptDestroyKey(hkey)
    finally:
        bc.BCryptCloseAlgorithmProvider(alg, 0)


def encrypt(token: str, username: str, portable: bool) -> str:
    if not token or not username:
        return ""
    key = derive_key(username, portable)
    data = token.encode("utf-8")
    data += b"\0" * (-len(data) % BLOCK)
    return base64.b64encode(_aes_cbc(key[:16], key[16:32], data, True)).decode("ascii")


def decrypt(value: str, username: str, portable: bool) -> str:
    """'' gdy się nie da (zły format, inny komputer, zwykły tekst)."""
    if not value or not username:
        return ""
    try:
        raw = base64.b64decode(value, validate=True)
    except ValueError:
        return ""
    if not raw or len(raw) % BLOCK:
        return ""
    key = derive_key(username, portable)
    try:
        plain = _aes_cbc(key[:16], key[16:32], raw, False).split(b"\0", 1)[0]
        text = plain.decode("ascii")
    except (OSError, UnicodeDecodeError):
        return ""
    ok = set(string.ascii_letters + string.digits)
    return text if text and set(text) <= ok else ""
