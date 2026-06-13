from __future__ import annotations

import subprocess
from typing import Any, Callable

from homelab_guardian.models import HealthCheck

# Surfaces services that listen on a non-loopback address (0.0.0.0, ::, or a
# LAN IP) and therefore answer to other machines on the network. Some of those
# are meant to be shared; some — a database port, an SMB share of a home
# directory, a VNC server — almost never should be. This reads `ss` only; it
# changes nothing. Pairs with the firewall collector: a firewall blocks these,
# this names them.

GROUP = "Security"

# port -> (label, why it is sensitive)
_SENSITIVE: dict[int, str] = {
    21: "FTP", 23: "Telnet", 111: "rpcbind", 135: "MS-RPC",
    137: "NetBIOS", 138: "NetBIOS", 139: "SMB/NetBIOS", 445: "SMB",
    2049: "NFS", 1433: "MS SQL", 3306: "MySQL/MariaDB", 3389: "RDP",
    5432: "PostgreSQL", 5900: "VNC", 5901: "VNC", 5902: "VNC",
    6379: "Redis", 9200: "Elasticsearch", 11211: "memcached",
    27017: "MongoDB",
}


def _is_loopback(addr: str) -> bool:
    addr = addr.strip()
    if "%lo" in addr:
        return True
    addr = addr.strip("[]")
    return addr.startswith("127.") or addr == "::1"


def _split_host_port(endpoint: str) -> tuple[str, int] | None:
    """Split an `ss` local-address field like 0.0.0.0:22, [::]:445, or
    127.0.0.53%lo:53 into (address, port)."""
    if ":" not in endpoint:
        return None
    host, _, port = endpoint.rpartition(":")
    try:
        return host, int(port)
    except ValueError:
        return None


def _parse_ss(text: str) -> list[dict[str, Any]]:
    """Parse `ss -tulnH` rows into listener dicts. Column 0 is the protocol,
    column 4 is the local address:port."""
    listeners: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0]
        hp = _split_host_port(parts[4])
        if hp is None:
            continue
        addr, port = hp
        listeners.append({"proto": proto, "address": addr, "port": port})
    return listeners


def collect(
    config: dict[str, Any],
    secrets: Any = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[HealthCheck]:
    check_id = "exposed_services"
    name = "Network-exposed services"

    sensitive = dict(_SENSITIVE)
    for port, label in (config.get("sensitive_ports", {}) or {}).items():
        sensitive[int(port)] = str(label)
    allow = {int(p) for p in (config.get("allow_ports", []) or [])}

    try:
        result = runner(["ss", "-tulnH"], capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return [HealthCheck(
            check_id, name, "unknown",
            "`ss` is not available, so listening sockets could not be inspected.",
            {}, "Install iproute2, or disable this collector on hosts without it.",
            group=GROUP,
        )]
    except (subprocess.SubprocessError, OSError) as exc:
        return [HealthCheck(
            check_id, name, "unknown",
            "Could not list listening sockets.",
            {"error": str(exc)}, "Check that `ss -tulnH` runs for Guardian's user.",
            group=GROUP,
        )]

    listeners = _parse_ss(result.stdout or "")
    exposed = [l for l in listeners if not _is_loopback(l["address"])]
    flagged = [
        {**l, "service": sensitive[l["port"]]}
        for l in exposed
        if l["port"] in sensitive and l["port"] not in allow
    ]
    # de-duplicate flagged ports for a readable summary
    flagged_ports = sorted({(f["service"], f["port"]) for f in flagged}, key=lambda x: x[1])

    evidence = {
        "exposed_count": len(exposed),
        "exposed": sorted(({"proto": l["proto"], "address": l["address"], "port": l["port"]} for l in exposed), key=lambda x: x["port"]),
        "sensitive_exposed": [{"service": s, "port": p} for s, p in flagged_ports],
        "allowed_ports": sorted(allow),
    }

    if flagged_ports:
        listed = ", ".join(f"{svc} ({port})" for svc, port in flagged_ports)
        return [HealthCheck(
            check_id, name, "warning",
            f"Sensitive service(s) reachable from the network: {listed}.",
            evidence,
            "Bind these to localhost, put them behind a firewall rule, or confirm they should be LAN-reachable and add their ports to allow_ports.",
            group=GROUP,
        )]

    return [HealthCheck(
        check_id, name, "ok",
        f"{len(exposed)} service(s) listen on the network; none are on a sensitive port.",
        evidence,
        "No action required.",
        group=GROUP,
    )]
