from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone

# Certificate expiry without a crypto dependency. The catch: with
# verify_mode=CERT_NONE (needed for self-signed homelab certs),
# getpeercert() returns an empty dict — only the DER bytes are available.
# X.509 mandates the field order inside tbsCertificate, so a ~40-line strict
# DER walk to the Validity sequence is deterministic and safe for this one
# read-only purpose. It is not a general-purpose ASN.1 parser.


def _read_tlv(data: bytes, offset: int) -> tuple[int, int, int]:
    """Return (tag, value_offset, value_length) for the TLV at offset."""
    tag = data[offset]
    length_byte = data[offset + 1]
    if length_byte < 0x80:
        return tag, offset + 2, length_byte
    num_bytes = length_byte & 0x7F
    value_length = int.from_bytes(data[offset + 2 : offset + 2 + num_bytes], "big")
    return tag, offset + 2 + num_bytes, value_length


def _parse_time(tag: int, raw: bytes) -> datetime:
    text = raw.decode("ascii")
    if tag == 0x17:  # UTCTime: YYMMDDHHMMSSZ
        year = int(text[:2])
        year += 2000 if year < 50 else 1900
        text = f"{year}{text[2:]}"
    # GeneralizedTime (0x18) is already YYYYMMDDHHMMSSZ
    return datetime.strptime(text, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)


def parse_der_validity(der: bytes) -> tuple[datetime, datetime]:
    """Extract (not_before, not_after) from a DER-encoded X.509 certificate.

    Walks: Certificate -> tbsCertificate -> [version] serialNumber
    signatureAlgorithm issuer -> validity. Field order is fixed by RFC 5280.
    """
    _, offset, _ = _read_tlv(der, 0)  # Certificate SEQUENCE
    _, offset, _ = _read_tlv(der, offset)  # tbsCertificate SEQUENCE

    tag, value_offset, length = _read_tlv(der, offset)
    if tag == 0xA0:  # optional [0] EXPLICIT version
        offset = value_offset + length
        tag, value_offset, length = _read_tlv(der, offset)
    # serialNumber INTEGER
    offset = value_offset + length
    # signature AlgorithmIdentifier SEQUENCE
    _, value_offset, length = _read_tlv(der, offset)
    offset = value_offset + length
    # issuer Name SEQUENCE
    _, value_offset, length = _read_tlv(der, offset)
    offset = value_offset + length
    # validity SEQUENCE { notBefore, notAfter }
    _, offset, _ = _read_tlv(der, offset)
    tag, value_offset, length = _read_tlv(der, offset)
    not_before = _parse_time(tag, der[value_offset : value_offset + length])
    offset = value_offset + length
    tag, value_offset, length = _read_tlv(der, offset)
    not_after = _parse_time(tag, der[value_offset : value_offset + length])
    return not_before, not_after


def fetch_cert_expiry(host: str, port: int = 443, timeout: float = 5.0) -> tuple[datetime, datetime, bool]:
    """Connect, read the peer certificate, return (not_before, not_after,
    chain_verified). Tries full verification first; falls back to an
    unverified handshake (self-signed) and parses the DER directly."""
    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls_sock:
                der = tls_sock.getpeercert(binary_form=True)
        not_before, not_after = parse_der_validity(der)
        return not_before, not_after, True
    except ssl.SSLError:
        pass  # fall through to the unverified read below

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls_sock:
            der = tls_sock.getpeercert(binary_form=True)
    not_before, not_after = parse_der_validity(der)
    return not_before, not_after, False
