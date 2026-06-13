from datetime import datetime, timezone

from app.tls import parse_der_validity


def _tlv(tag: int, value: bytes) -> bytes:
    length = len(value)
    if length < 0x80:
        return bytes([tag, length]) + value
    length_bytes = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([tag, 0x80 | len(length_bytes)]) + length_bytes + value


def _utc_time(text: str) -> bytes:
    return _tlv(0x17, text.encode())


def _generalized_time(text: str) -> bytes:
    return _tlv(0x18, text.encode())


def _fake_cert(not_before: bytes, not_after: bytes, with_version: bool = True) -> bytes:
    """Build a minimal DER Certificate: just enough structure for the parser."""
    fields = b""
    if with_version:
        fields += _tlv(0xA0, _tlv(0x02, b"\x02"))  # [0] version
    fields += _tlv(0x02, b"\x01")  # serialNumber
    fields += _tlv(0x30, b"")  # signature AlgorithmIdentifier
    fields += _tlv(0x30, _tlv(0x31, b"issuer"))  # issuer Name
    fields += _tlv(0x30, not_before + not_after)  # validity
    tbs = _tlv(0x30, fields)
    return _tlv(0x30, tbs)


def test_parse_utc_time_validity():
    der = _fake_cert(_utc_time("240101000000Z"), _utc_time("261231235959Z"))
    not_before, not_after = parse_der_validity(der)
    assert not_before == datetime(2024, 1, 1, tzinfo=timezone.utc)
    assert not_after == datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def test_parse_generalized_time_and_no_version():
    der = _fake_cert(
        _generalized_time("20500101000000Z"), _generalized_time("20600101000000Z"), with_version=False
    )
    not_before, not_after = parse_der_validity(der)
    assert not_before.year == 2050
    assert not_after.year == 2060


def test_utc_time_century_window():
    # YY < 50 means 20YY; YY >= 50 means 19YY per RFC 5280
    der = _fake_cert(_utc_time("990101000000Z"), _utc_time("491231235959Z"))
    not_before, not_after = parse_der_validity(der)
    assert not_before.year == 1999
    assert not_after.year == 2049
