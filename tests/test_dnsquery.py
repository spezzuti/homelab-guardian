"""Wire-format tests for the dependency-free DNS client (canned packets)."""
import struct

import pytest

from homelab_guardian import dnsquery


def _question(hostname: str) -> bytes:
    labels = b"".join(bytes([len(p)]) + p.encode() for p in hostname.split("."))
    return labels + b"\x00" + struct.pack("!HH", 1, 1)


def _response(query_id: bytes, hostname: str, answers: list[tuple[int, bytes]], flags: int = 0x8180) -> bytes:
    out = query_id + struct.pack("!HHHHH", flags, 1, len(answers), 0, 0)
    out += _question(hostname)
    for rtype, rdata in answers:
        out += b"\xc0\x0c" + struct.pack("!HHIH", rtype, 1, 60, len(rdata)) + rdata
    return out


def test_build_query_encodes_the_question():
    q = dnsquery.build_query("port.example.lan")
    assert q[4:6] == b"\x00\x01"  # one question
    assert b"\x04port\x07example\x03lan\x00" in q
    assert q.endswith(struct.pack("!HH", 1, 1))


def test_build_query_rejects_bad_hostnames():
    with pytest.raises(dnsquery.DnsError):
        dnsquery.build_query("")
    with pytest.raises(dnsquery.DnsError):
        dnsquery.build_query("a" * 64 + ".lan")


def test_parse_collects_a_records_and_skips_cname():
    q = dnsquery.build_query("port.example.lan")
    cname_rdata = b"\x05other\x07example\x03lan\x00"
    data = _response(q[:2], "port.example.lan", [
        (5, cname_rdata),                       # CNAME — skipped
        (1, bytes([192, 168, 0, 87])),          # A
        (1, bytes([192, 168, 0, 88])),          # A
    ])
    assert dnsquery.parse_a_records(data, q[:2]) == ["192.168.50.20", "192.168.50.21"]


def test_parse_rejects_mismatched_id():
    q = dnsquery.build_query("x.lan")
    data = _response(b"\xde\xad", "x.lan", [(1, bytes([10, 0, 0, 1]))])
    with pytest.raises(dnsquery.DnsError, match="id"):
        dnsquery.parse_a_records(data, q[:2])


def test_parse_surfaces_nxdomain_by_name():
    q = dnsquery.build_query("gone.lan")
    data = _response(q[:2], "gone.lan", [], flags=0x8183)
    with pytest.raises(dnsquery.DnsError, match="NXDOMAIN"):
        dnsquery.parse_a_records(data, q[:2])


def test_parse_rejects_short_packet():
    with pytest.raises(dnsquery.DnsError, match="header"):
        dnsquery.parse_a_records(b"\x00\x01\x02", b"\x00\x01")


def test_parse_no_answers_returns_empty_list():
    q = dnsquery.build_query("empty.lan")
    data = _response(q[:2], "empty.lan", [])
    assert dnsquery.parse_a_records(data, q[:2]) == []
