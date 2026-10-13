"""Podpis wydań EmuStart (emustart/signing.py).

    python tools/release_sign.py keygen <plik klucza prywatnego>
        nowa para kluczy: prywatny (base64) do pliku — do sekretu CI i kopii zapasowej,
        publiczny wypisany — do emustart/signing.py (RELEASE_PUBKEY)

    python tools/release_sign.py sign <plik>...
        podpis <plik>.sig obok pliku; klucz prywatny ze zmiennej EMUSTART_SIGNING_KEY
        (CI: sekret repozytorium). Bez klucza — błąd (wydanie bez podpisu nie wychodzi).

    python tools/release_sign.py verify <plik>
        sprawdzenie <plik>.sig kluczem publicznym z emustart/signing.py
"""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from emustart import signing  # noqa: E402


def keygen(out: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if out.exists():
        sys.exit(f"{out} już istnieje — nie nadpisuję klucza.")
    k = Ed25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                           serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(base64.b64encode(priv).decode("ascii") + "\n", encoding="ascii")
    print(base64.b64encode(pub).decode("ascii"))


def sign(files: list) -> None:
    key = (os.environ.get("EMUSTART_SIGNING_KEY") or "").strip()
    if not key:
        sys.exit("Brak EMUSTART_SIGNING_KEY — wydanie musi być podpisane (sekret repozytorium).")
    for f in map(Path, files):
        sig = signing.sign(key, f.name, signing.file_sha256(f))
        if not signing.verify(f.name, signing.file_sha256(f), sig):
            sys.exit("Klucz z EMUSTART_SIGNING_KEY nie pasuje do RELEASE_PUBKEY w emustart/signing.py.")
        f.with_name(f.name + ".sig").write_text(sig + "\n", encoding="ascii")
        print(f"podpisano {f.name}")


def verify(f: Path) -> None:
    sig = f.with_name(f.name + ".sig").read_text(encoding="ascii")
    ok = signing.verify(f.name, signing.file_sha256(f), sig)
    print("podpis poprawny" if ok else "PODPIS NIEPOPRAWNY")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "keygen" and len(args) == 1:
        keygen(Path(args[0]))
    elif cmd == "sign" and args:
        sign(args)
    elif cmd == "verify" and len(args) == 1:
        verify(Path(args[0]))
    else:
        sys.exit(__doc__)
