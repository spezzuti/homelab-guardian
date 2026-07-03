"""Minimal dependency-free DNS client for querying a SPECIFIC resolver.

The system-resolver path keeps using socket.getaddrinfo; this exists only for
split-horizon validation, where the point is to ask one chosen server (the
Pi-hole, the router) what IT answers for a name and assert the answer. Scope
is deliberately tiny: A records, UDP with a TCP retry on truncation.
"""
from __future__ import annotations

import os
import socket
import struct

_RCODE_NAMES = {1: "FORMERR", 2: "SERVFAIL", 3: "NXDOMAIN", 4: "NOTIMP", 5: "REFUSED"}


class DnsError(Exception):
    """The server answered, but not with a usable A response."""


def build_query(hostname: str) -> bytes:
    """A standard recursive query for the A records of hostname."""
    try:
        labels = [part.encode("ascii") for part in hostname.rstrip(".").split(".") if part]
    except UnicodeEncodeError as exc:
        raise DnsError(f"hostname is not plain ASCII: {hostname!r}") from exc
    if not labels or any(len(label) > 63 for label in labels):
        raise DnsError(f"invalid hostname: {hostname!r}")
    header = os.urandom(2) + struct.pack("!HHHHH", 0x0100, 1, 0, 0, 0)  # RD set, one question
    question = b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"
    return header + question + struct.pack("!HH", 1, 1)  # QTYPE=A, QCLASS=IN


def _skip_name(data: bytes, offset: int) -> int:
    """Advance past a (possibly compression-pointer) encoded name."""
    while True:
        if offset >= len(data):
            raise DnsError("malformed response: name runs past the packet")
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:  # compression pointer ends the name
            return offset + 2
        offset += 1 + length


def parse_a_records(data: bytes, query_id: bytes) -> list[str]:
    """A record addresses from a response, following CNAME chains implicitly
    (the answer section carries the chain; we collect every A in it)."""
    if len(data) < 12:
        raise DnsError("malformed response: shorter than a DNS header")
    if data[:2] != query_id:
        raise DnsError("response id does not match the query")
    flags = struct.unpack("!H", data[2:4])[0]
    rcode = flags & 0xF
    if rcode:
        name = _RCODE_NAMES.get(rcode, f"rcode {rcode}")
        raise DnsError(f"server answered {name}")
    qdcount, ancount = struct.unpack("!HH", data[4:8])
    offset = 12
    for _ in range(qdcount):
        offset = _skip_name(data, offset) + 4  # + QTYPE/QCLASS
    addresses: list[str] = []
    for _ in range(ancount):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            raise DnsError("malformed response: truncated answer record")
        rtype, _rclass, _ttl, rdlength = struct.unpack("!HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlength]
        offset += rdlength
        if rtype == 1 and rdlength == 4:
            addresses.append(socket.inet_ntoa(rdata))
    return addresses


def _is_truncated(data: bytes) -> bool:
    return len(data) >= 4 and bool(data[2] & 0x02)


def query_a(hostname: str, server: str, timeout: float = 3.0, port: int = 53) -> list[str]:
    """Ask one specific DNS server for hostname's A records.

    Raises OSError when the server is unreachable and DnsError when it answers
    with an error or garbage — callers treat both as "did not resolve" but the
    message tells the user which it was.
    """
    query = build_query(hostname)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(query, (server, port))
        data, _ = sock.recvfrom(4096)
    if _is_truncated(data):
        with socket.create_connection((server, port), timeout=timeout) as tcp:
            tcp.settimeout(timeout)
            tcp.sendall(struct.pack("!H", len(query)) + query)
            raw = b""
            while len(raw) < 2:
                chunk = tcp.recv(4096)
                if not chunk:
                    raise DnsError("server closed the TCP retry without a length")
                raw += chunk
            (length,) = struct.unpack("!H", raw[:2])
            while len(raw) < 2 + length:
                chunk = tcp.recv(4096)
                if not chunk:
                    break
                raw += chunk
            data = raw[2:2 + length]
    return parse_a_records(data, query[:2])
