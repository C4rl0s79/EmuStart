"""tls — szyfrowany port serwera EmuStart (HTTPS z własnym certyfikatem).

Serwer tworzy przy pierwszym starcie własny certyfikat (EC P-256, 20 lat) w katalogu
serwera. Klienci nie sprawdzają go w urzędzie certyfikacji, tylko porównują jego odcisk
(SHA-256 całego certyfikatu) z odciskiem z kodu QR / zalogowania („przypięcie”):
podszycie się pod serwer wymagałoby jego klucza prywatnego. Dzięki temu HTTPS działa
także pod gołym adresem IP (port publiczny przez UPnP — etap 0.35).

Bez biblioteki `cryptography` (uruchomienie ze źródeł bez niej) port szyfrowany się nie
otwiera, reszta serwera działa jak dotąd.

Klucz prywatny nie leży na dysku otwartym tekstem: `tls-key.pem` jest zaszyfrowany (AES-256)
losowym hasłem, a hasło — DPAPI w zakresie komputera (`tls-key.pass`). Kopia folderu serwera
(udział sieciowy, kopia zapasowa, skradziony dysk) na innym komputerze jest bezużyteczna.
Przeniesienie serwera: panel → „Przygotuj przeniesienie” (plik `przeniesienie.emustart`
zaszyfrowany hasłem przeniesienia: klucz certyfikatu i klucze źródeł grafik), na nowym
komputerze panel prosi o to hasło (`import_move`) — certyfikat ten sam, urządzenia działają
dalej bez ponownego logowania.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import os
import socket
import ssl
from pathlib import Path

log = logging.getLogger("emustart.tls")

CERT = "tls-cert.pem"
KEY = "tls-key.pem"
PASS = "tls-key.pass"
MOVE = "przeniesienie.emustart"

# stan klucza (panel serwera): ok | locked (folder przeniesiony z innego komputera — potrzebne
# hasło przeniesienia) | plain (bez DPAPI, np. poza Windows) | none (bez certyfikatu)
STATE: dict = {"state": "none", "reason": ""}


def fingerprint_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def fingerprint(cert_path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="ascii"))
    return fingerprint_der(der)


def ensure(folder: Path) -> tuple[Path, Path, str] | None:
    """(certyfikat, klucz, odcisk) — tworzy przy pierwszym wywołaniu; klucz zapisany jako
    zaszyfrowany. None bez `cryptography` albo gdy klucza nie da się odszyfrować na tym
    komputerze (folder przeniesiony — STATE „locked”, panel prosi o hasło przeniesienia)."""
    cert, key = folder / CERT, folder / KEY
    try:
        if not (cert.is_file() and key.is_file()):
            _generate(cert, key)
            log.info("utworzono certyfikat serwera: %s", cert)
        _protect_existing(folder)
    except ImportError:
        log.warning("brak biblioteki cryptography — port szyfrowany wyłączony")
        STATE.update(state="none", reason="brak biblioteki cryptography")
        return None
    if _encrypted(key) and key_password(folder) is None:
        STATE.update(state="locked", reason="klucz certyfikatu z innego komputera — podaj hasło przeniesienia")
        log.warning("klucz certyfikatu nie do odszyfrowania na tym komputerze (folder serwera przeniesiony?) — "
                    "port szyfrowany wyłączony; panel serwera → Przeniesienie serwera")
        return None
    STATE.update(state="ok" if _encrypted(key) else "plain", reason="")
    return cert, key, fingerprint(cert)


def _encrypted(key: Path) -> bool:
    try:
        return b"ENCRYPTED" in key.read_bytes()[:64]
    except OSError:
        return False


def key_password(folder: Path) -> bytes | None:
    """Hasło klucza (DPAPI komputera) albo None."""
    from emustart import secure
    try:
        blob = (folder / PASS).read_bytes()
    except OSError:
        return None
    return secure.unprotect_machine(blob)


def _write_key(folder: Path, private_key) -> bool:
    """Klucz zaszyfrowany nowym, losowym hasłem chronionym DPAPI komputera. False — DPAPI
    niedostępne (klucz zostaje bez zmian)."""
    import secrets as _secrets
    from cryptography.hazmat.primitives import serialization
    from emustart import secure
    pw = _secrets.token_urlsafe(32).encode("ascii")
    blob = secure.protect_machine(pw)
    if not blob or secure.unprotect_machine(blob) != pw:
        return False
    data = private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.BestAvailableEncryption(pw))
    key, pas = folder / KEY, folder / PASS
    key_tmp, pas_tmp = folder / (KEY + ".tmp"), folder / (PASS + ".tmp")
    pas_tmp.write_bytes(blob)
    key_tmp.write_bytes(data)
    # kontrola: zaszyfrowany klucz wczytuje się hasłem z DPAPI, zanim cokolwiek podmienimy
    serialization.load_pem_private_key(key_tmp.read_bytes(), password=secure.unprotect_machine(pas_tmp.read_bytes()))
    os.replace(pas_tmp, pas)
    os.replace(key_tmp, key)                       # stary (otwarty) klucz nadpisany
    return True


def _protect_existing(folder: Path) -> None:
    """Klucz zapisany otwartym tekstem (serwer sprzed 0.45) → zaszyfrowany."""
    from cryptography.hazmat.primitives import serialization
    key = folder / KEY
    if _encrypted(key):
        return
    k = serialization.load_pem_private_key(key.read_bytes(), password=None)
    if _write_key(folder, k):
        log.info("klucz certyfikatu serwera zaszyfrowany (DPAPI komputera)")
    else:
        log.warning("DPAPI niedostępne — klucz certyfikatu zostaje otwartym tekstem")


def _move_cipher(password: str, salt: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    k = Scrypt(salt=salt, length=32, n=2 ** 15, r=8, p=1).derive(password.encode("utf-8"))
    return AESGCM(k)


def export_move(folder: Path, password: str, extra: dict | None = None) -> Path:
    """Plik przeniesienia serwera: klucz certyfikatu (i `extra`, np. klucze źródeł grafik)
    zaszyfrowane hasłem przeniesienia (scrypt + AES-256-GCM)."""
    import base64
    import json
    from cryptography.hazmat.primitives import serialization
    key = folder / KEY
    pw = key_password(folder) if _encrypted(key) else None
    if _encrypted(key) and pw is None:
        raise ValueError("Klucza certyfikatu nie da się odszyfrować na tym komputerze.")
    k = serialization.load_pem_private_key(key.read_bytes(), password=pw)
    plain = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode("ascii")
    body = json.dumps({"key": plain, "fp": fingerprint(folder / CERT), "extra": extra or {}}).encode("utf-8")
    salt, nonce = os.urandom(16), os.urandom(12)
    data = _move_cipher(password, salt).encrypt(nonce, body, b"emustart-move-1")
    out = folder / MOVE
    out.write_text(json.dumps({"v": 1, "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
                               "data": base64.b64encode(data).decode()}), encoding="ascii")
    return out


def import_move(folder: Path, password: str) -> dict:
    """Hasło przeniesienia → klucz certyfikatu zaszyfrowany na tym komputerze (DPAPI). Zwraca
    `extra` z pliku przeniesienia. ValueError — złe hasło / inny certyfikat / brak pliku."""
    import base64
    import json
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives import serialization
    try:
        d = json.loads((folder / MOVE).read_text(encoding="ascii"))
    except (OSError, ValueError):
        raise ValueError("Nie ma pliku przeniesienia (przeniesienie.emustart) w folderze serwera — "
                         "przygotuj przeniesienie na poprzednim komputerze.")
    try:
        body = _move_cipher(password, base64.b64decode(d["salt"])).decrypt(
            base64.b64decode(d["nonce"]), base64.b64decode(d["data"]), b"emustart-move-1")
    except (InvalidTag, KeyError, ValueError):
        raise ValueError("Złe hasło przeniesienia.")
    content = json.loads(body)
    if content.get("fp") != fingerprint(folder / CERT):
        raise ValueError("Plik przeniesienia jest od innego certyfikatu niż ten w folderze serwera.")
    k = serialization.load_pem_private_key(content["key"].encode("ascii"), password=None)
    if not _write_key(folder, k):
        raise ValueError("DPAPI niedostępne na tym komputerze.")
    (folder / MOVE).unlink(missing_ok=True)
    STATE.update(state="ok", reason="")
    log.info("klucz certyfikatu przeniesiony na ten komputer (DPAPI)")
    return content.get("extra") or {}


def _generate(cert: Path, key: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"EmuStart {socket.gethostname()}"[:64])])
    now = datetime.datetime.now(datetime.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(now - datetime.timedelta(days=1))
         .not_valid_after(now + datetime.timedelta(days=365 * 20))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(socket.gethostname())]), critical=False)
         .sign(k, hashes.SHA256()))
    cert.parent.mkdir(parents=True, exist_ok=True)
    if not _write_key(cert.parent, k):              # bez DPAPI (poza Windows) — otwartym tekstem
        key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))


def server_context(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    pw = key_password(key.parent) if _encrypted(key) else None
    ctx.load_cert_chain(str(cert), str(key), password=pw)
    return ctx


def client_context() -> ssl.SSLContext:
    """Bez sprawdzania urzędu certyfikacji — o zaufaniu decyduje przypięty odcisk."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
