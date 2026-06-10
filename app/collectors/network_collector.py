from __future__ import annotations

import socket
from typing import Any

from app.models import HealthCheck


def collect(config: dict[str, Any]) -> list[HealthCheck]:
    checks: list[HealthCheck] = []

    for item in config.get("dns_checks", []) or []:
        check_id = item.get("id") or f"dns_{item.get('hostname', 'unknown')}"
        name = item.get("name") or f"DNS check: {item.get('hostname', 'unknown')}"
        hostname = item.get("hostname")
        if not hostname:
            checks.append(HealthCheck(check_id, name, "unknown", "DNS check is missing a hostname.", item, "Add a hostname or remove this check."))
            continue
        try:
            addresses = sorted({result[4][0] for result in socket.getaddrinfo(hostname, None)})
            checks.append(HealthCheck(check_id, name, "ok", f"{hostname} resolves.", {"hostname": hostname, "addresses": addresses}, "No action required."))
        except Exception as exc:
            checks.append(HealthCheck(check_id, name, "warning", f"{hostname} did not resolve.", {"hostname": hostname, "error": str(exc)}, "Check DNS service, upstream resolver, or local network path."))

    for item in config.get("tcp_checks", []) or []:
        check_id = item.get("id") or f"tcp_{item.get('host', 'unknown')}_{item.get('port', 'unknown')}"
        name = item.get("name") or f"TCP check: {item.get('host', 'unknown')}:{item.get('port', 'unknown')}"
        host = item.get("host")
        port = item.get("port")
        timeout = float(item.get("timeout", 3))
        if not host or not port:
            checks.append(HealthCheck(check_id, name, "unknown", "TCP check is missing host or port.", item, "Add host and port or remove this check."))
            continue
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                checks.append(HealthCheck(check_id, name, "ok", f"{host}:{port} is reachable.", {"host": host, "port": port}, "No action required."))
        except Exception as exc:
            checks.append(HealthCheck(check_id, name, "warning", f"{host}:{port} is not reachable.", {"host": host, "port": port, "error": str(exc)}, "Check whether the service, host, or firewall changed."))

    if not checks:
        checks.append(HealthCheck("network_not_configured", "Network checks", "unknown", "No network checks are configured.", {}, "Add dns_checks or tcp_checks to config.yaml if desired."))

    return checks
