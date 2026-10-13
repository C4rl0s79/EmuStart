"""tls — szyfrowany port serwera EmuStart (HTTPS z własnym certyfikatem).

Serwer tworzy przy pierwszym starcie własny certyfikat (EC P-256, 20 lat) w katalogu
serwera. Klienci nie sprawdzają go w urzędzie certyfikacji, tylko porównują jego odcisk
(SHA-256 całego certyfikatu) z odciskiem z kodu QR / zalogowania („przypięcie”):
podszycie się pod serwer wymagałoby jego klucza prywatnego. Dzięki temu HTTPS działa
także pod gołym adresem IP (port publiczny przez UPnP — etap 0.35).

Bez biblioteki `cryptography` (uruchomienie ze źródeł bez niej) port szyfrowany się nie
otwiera, reszta serwera działa jak dotąd.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import socket
import ssl
from pathlib import Path

log = logging.getLogger("emustart.tls")

CERT = "tls-cert.pem"
KEY = "tls-key.pem"


def fingerprint_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def fingerprint(cert_path: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding="ascii"))
    return fingerprint_der(der)


def ensure(folder: Path) -> tuple[Path, Path, str] | None:
    """(certyfikat, klucz, odcisk) — tworzy przy pierwszym wywołaniu. None bez `cryptography`."""
    cert, key = folder / CERT, folder / KEY
    if not (cert.is_file() and key.is_file()):
        try:
            _generate(cert, key)
        except ImportError:
            log.warning("brak biblioteki cryptography — port szyfrowany wyłączony")
            return None
        log.info("utworzono certyfikat serwera: %s", cert)
    return cert, key, fingerprint(cert)


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
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))


def server_context(cert: Path, key: Path) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(str(cert), str(key))
    return ctx


def client_context() -> ssl.SSLContext:
    """Bez sprawdzania urzędu certyfikacji — o zaufaniu decyduje przypięty odcisk."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
