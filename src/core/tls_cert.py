"""Local Root CA & LAN Leaf TLS Certificate generator + Dual HTTP/HTTPS socket wrapper.

iOS Safari/WebKit requires an `https://` secure context on LAN IPs (`192.168.x.x`) for:
1. `navigator.geolocation` (live hardware GPS tracking)
2. `navigator.serviceWorker` (offline PWA page & asset caching)

This module generates a persistent local Root CA certificate (`~/.aegishiker/tls/aegishiker_ca.pem`)
and a leaf certificate covering the machine's current LAN IPv4 address, `127.0.0.1`, and `localhost`.
It also provides `DualToplevelSocket`, which peeks the first byte of each incoming TCP connection
(`0x16` = TLS ClientHello vs ASCII HTTP verb) so `TrailServer` can serve BOTH `https://` and `http://`
on a single port.
"""
from __future__ import annotations

import datetime
import ipaddress
import logging
import socket
import ssl
from pathlib import Path
from typing import Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import paths

logger = logging.getLogger(__name__)


def get_tls_dir() -> Path:
    d = paths.get_app_dir() / "tls"
    d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_tls_certificates(host_ip: str = "127.0.0.1") -> Tuple[Path, Path, Path]:
    """Ensure a local Root CA and a leaf certificate containing `host_ip` in its SAN exist.

    Returns `(ca_cert_path, leaf_cert_path, leaf_key_path)`.
    """
    tls_dir = get_tls_dir()
    ca_cert_path = tls_dir / "aegishiker_ca.pem"
    ca_key_path = tls_dir / "aegishiker_ca_key.pem"
    leaf_cert_path = tls_dir / "aegishiker_server.pem"
    leaf_key_path = tls_dir / "aegishiker_server_key.pem"

    now = datetime.datetime.now(datetime.timezone.utc)

    # 1. Load or generate persistent Root CA
    ca_cert: Optional[x509.Certificate] = None
    ca_key: Optional[rsa.RSAPrivateKey] = None
    if ca_cert_path.exists() and ca_key_path.exists():
        try:
            ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
            ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)  # type: ignore[assignment]
        except Exception as e:
            logger.warning(f"Regenerating local CA certificate: {e}")
            ca_cert, ca_key = None, None

    if ca_cert is None or ca_key is None:
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        ca_subject = x509.Name(
            [
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AegisHiker Local Companion CA"),
                x509.NameAttribute(NameOID.COMMON_NAME, "AegisHiker Local Root CA"),
            ]
        )
        ca_cert = (
            x509.CertificateBuilder()
            .subject_name(ca_subject)
            .issuer_name(ca_subject)
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(private_key=ca_key, algorithm=hashes.SHA256())
        )
        ca_key_path.write_bytes(
            ca_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    # 2. Check whether existing leaf cert already covers `host_ip`
    need_leaf = not (leaf_cert_path.exists() and leaf_key_path.exists())
    if not need_leaf:
        try:
            existing_leaf = x509.load_pem_x509_certificate(leaf_cert_path.read_bytes())
            san = existing_leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            ips = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
            if host_ip not in ips or existing_leaf.not_valid_after_utc <= now + datetime.timedelta(days=7):
                need_leaf = True
        except Exception:
            need_leaf = True

    if need_leaf:
        leaf_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        san_entries: list[x509.GeneralName] = [
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]
        try:
            ip_obj = ipaddress.ip_address(host_ip)
            if ip_obj != ipaddress.ip_address("127.0.0.1"):
                san_entries.append(x509.IPAddress(ip_obj))
        except ValueError:
            san_entries.append(x509.DNSName(host_ip))

        leaf_subject = x509.Name(
            [
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AegisHiker Field Companion"),
                x509.NameAttribute(NameOID.COMMON_NAME, host_ip),
            ]
        )
        leaf_cert = (
            x509.CertificateBuilder()
            .subject_name(leaf_subject)
            .issuer_name(ca_cert.subject)
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=820))  # iOS max leaf validity is 825 days
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(private_key=ca_key, algorithm=hashes.SHA256())
        )
        leaf_key_path.write_bytes(
            leaf_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
        # Bundle leaf + CA cert chain
        leaf_cert_path.write_bytes(
            leaf_cert.public_bytes(serialization.Encoding.PEM)
            + ca_cert.public_bytes(serialization.Encoding.PEM)
        )

    return ca_cert_path, leaf_cert_path, leaf_key_path


def create_server_ssl_context(host_ip: str = "127.0.0.1") -> Tuple[ssl.SSLContext, Path]:
    """Create a server `ssl.SSLContext` for `host_ip` and return `(ctx, ca_cert_path)`."""
    ca_cert_path, leaf_cert_path, leaf_key_path = ensure_tls_certificates(host_ip)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=str(leaf_cert_path), keyfile=str(leaf_key_path))
    return ctx, ca_cert_path


def wrap_client_socket_auto_tls(
    conn: socket.socket,
    ssl_context: Optional[ssl.SSLContext],
) -> socket.socket:
    """Peek at the first byte of an accepted client connection:
    - If `0x16` (TLS Handshake ClientHello) and `ssl_context` is available, wrap with TLS.
    - Otherwise return the raw socket for plain HTTP requests.
    """
    if ssl_context is None:
        return conn
    try:
        conn.settimeout(5.0)
        first = conn.recv(1, socket.MSG_PEEK)
        if first == b"\x16":
            return ssl_context.wrap_socket(conn, server_side=True)
    except (ssl.SSLError, OSError):
        raise
    return conn
