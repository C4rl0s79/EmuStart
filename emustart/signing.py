"""signing — podpis wydań EmuStart (Ed25519).

Skrót SHA-256 z API GitHuba chroni tylko przed uszkodzonym pobraniem; kto przejmie konto
albo repozytorium, podmieni i plik, i skrót. Podpis robi CI kluczem prywatnym z sekretu
repozytorium (`EMUSTART_SIGNING_KEY`), a program sprawdza go kluczem publicznym wbudowanym
w EmuStart.exe — podmienione wydanie bez klucza prywatnego nie przejdzie.

Podpisywana jest wiadomość z nazwą pliku i jego skrótem — podpis jednego pliku nie pasuje
do innego (np. starszej wersji podsuniętej jako nowa):

    emustart-release-v1\\n<nazwa pliku>\\n<sha256 hex>

Plik podpisu: `<nazwa pliku>.sig` (podpis base64) obok pliku w wydaniu.
Narzędzie: tools/release_sign.py (klucz, podpis).
"""

from __future__ import annotations

import base64
import hashlib

# klucz publiczny wydań (Ed25519, 32 bajty, base64); prywatny — tylko w sekrecie CI i kopii właściciela
RELEASE_PUBKEY = "jySJqARQgXYZI5p3ir/SxLoBkcAhBhAPZEJ2QusFvl8="


def message(name: str, sha256_hex: str) -> bytes:
    return f"emustart-release-v1\n{name}\n{sha256_hex.lower()}".encode("utf-8")


def file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sign(private_b64: str, name: str, sha256_hex: str) -> str:
    """Podpis (base64) — CI (tools/release_sign.py)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_b64))
    return base64.b64encode(key.sign(message(name, sha256_hex))).decode("ascii")


def verify(name: str, sha256_hex: str, sig_b64: str, public_b64: str = "") -> bool:
    """Czy podpis pliku `name` o skrócie `sha256_hex` pochodzi z klucza wydań."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    try:
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64 or RELEASE_PUBKEY))
        pub.verify(base64.b64decode(sig_b64.strip()), message(name, sha256_hex))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
