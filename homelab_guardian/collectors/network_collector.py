from __future__ import annotations

import socket
import warnings
from datetime import datetime, timezone
from typing import Any

import requests
import urllib3

from homelab_guardian.models import HealthCheck
from homelab_guardian.tls import fetch_cert_expiry


def _expected_status(item: dict[str, Any]) -> set[int]:
    expected = item.get("expected_status", item.get("expected_statuses", [200]))
    if isinstance(expected, int):
        return {expected}
    return {int(code) for code in expected}


def collect(config: dict[str, Any], secrets: Any = None) -> list[HealthCheck]:
    checks: list[HealthCheck] = []

    # Each check carries a `group` so related services roll up together in the
    # dashboard. A target may set `group:` (e.g. "Core services") to file its
    # reachability and certificate checks under one heading; otherwise it falls
    # back to a sensible per-type default.

    for item in config.get("dns_checks", []) or []:
        group = item.get("group") or "Network"
        check_id = item.get("id") or f"dns_{item.get('hostname', 'unknown')}"
        name = item.get("name") or f"DNS check: {item.get('hostname', 'unknown')}"
        hostname = item.get("hostname")
        record_type = item.get("record_type", "A/AAAA")
        if not hostname:
            checks.append(HealthCheck(check_id, name, "unknown", "DNS check is missing a hostname.", item, "Add a hostname or remove this check.", group=group))
            continue
        try:
            addresses = sorted({result[4][0] for result in socket.getaddrinfo(hostname, None)})
            checks.append(
                HealthCheck(
                    check_id, name, "ok",
                    f"{hostname} resolves to {len(addresses)} address(es).",
                    {"hostname": hostname, "record_type": record_type, "addresses": addresses},
                    "No action required.", group=group,
                )
            )
        except Exception as exc:
            checks.append(
                HealthCheck(
                    check_id, name, "warning",
                    f"{hostname} did not resolve.",
                    {"hostname": hostname, "record_type": record_type, "error": str(exc)},
                    "Check DNS service, upstream resolver, or local network path.", group=group,
                )
            )

    for item in config.get("tcp_checks", []) or []:
        group = item.get("group") or "Network"
        check_id = item.get("id") or f"tcp_{item.get('host', 'unknown')}_{item.get('port', 'unknown')}"
        name = item.get("name") or f"TCP check: {item.get('host', 'unknown')}:{item.get('port', 'unknown')}"
        host = item.get("host")
        port = item.get("port")
        timeout = float(item.get("timeout", 3))
        if not host or not port:
            checks.append(HealthCheck(check_id, name, "unknown", "TCP check is missing host or port.", item, "Add host and port or remove this check.", group=group))
            continue
        try:
            with socket.create_connection((host, int(port)), timeout=timeout):
                checks.append(
                    HealthCheck(
                        check_id, name, "ok",
                        f"{host}:{port} accepted a TCP connection.",
                        {"host": host, "port": int(port), "timeout_seconds": timeout},
                        "No action required.", group=group,
                    )
                )
        except Exception as exc:
            checks.append(
                HealthCheck(
                    check_id, name, "warning",
                    f"{host}:{port} is not reachable.",
                    {"host": host, "port": port, "timeout_seconds": timeout, "error": str(exc)},
                    "Check whether the service, host, route, or firewall changed.", group=group,
                )
            )

    for item in config.get("http_checks", []) or []:
        group = item.get("group") or "Web services"
        check_id = item.get("id") or f"http_{item.get('url', 'unknown')}"
        name = item.get("name") or f"HTTP check: {item.get('url', 'unknown')}"
        url = item.get("url")
        timeout = float(item.get("timeout", 5))
        method = str(item.get("method", "GET")).upper()
        expected = _expected_status(item)
        # Homelab services commonly use self-signed certificates. verify_tls: false
        # keeps the reachability check useful without forcing users to install CAs.
        verify_tls = bool(item.get("verify_tls", True))
        if not url:
            checks.append(HealthCheck(check_id, name, "unknown", "HTTP check is missing a URL.", item, "Add a URL or remove this check.", group=group))
            continue
        try:
            with warnings.catch_warnings():
                if not verify_tls:
                    warnings.simplefilter("ignore", urllib3.exceptions.InsecureRequestWarning)
                response = requests.request(method, url, timeout=timeout, allow_redirects=True, verify=verify_tls)
            evidence = {
                "url": url,
                "method": method,
                "status_code": response.status_code,
                "expected_status": sorted(expected),
                "final_url": response.url,
                "elapsed_seconds": round(response.elapsed.total_seconds(), 3),
                "verify_tls": verify_tls,
            }
            if response.status_code in expected:
                checks.append(HealthCheck(check_id, name, "ok", f"{url} returned HTTP {response.status_code}.", evidence, "No action required.", group=group))
            else:
                checks.append(HealthCheck(check_id, name, "warning", f"{url} returned HTTP {response.status_code}; expected {sorted(expected)}.", evidence, "Check the service, reverse proxy, certificate, or expected status configuration.", group=group))
        except Exception as exc:
            checks.append(
                HealthCheck(
                    check_id, name, "warning",
                    f"{url} did not return an HTTP response.",
                    {"url": url, "method": method, "timeout_seconds": timeout, "expected_status": sorted(expected), "error": str(exc)},
                    "Check network path, DNS, TLS, reverse proxy, or service health.", group=group,
                )
            )

    for item in config.get("tls_checks", []) or []:
        group = item.get("group") or "Certificates"
        host = item.get("host")
        port = int(item.get("port", 443))
        check_id = item.get("id") or f"tls_{host}_{port}"
        name = item.get("name") or f"TLS certificate: {host}"
        warn_days = float(item.get("warn_days", 14))
        critical_days = float(item.get("critical_days", 3))
        timeout = float(item.get("timeout", 5))
        if not host:
            checks.append(HealthCheck(check_id, name, "unknown", "TLS check is missing a host.", item, "Add a host or remove this check.", group=group))
            continue
        try:
            not_before, not_after, verified = fetch_cert_expiry(host, port, timeout=timeout)
        except Exception as exc:
            checks.append(
                HealthCheck(
                    check_id, name, "warning",
                    f"Could not read the TLS certificate from {host}:{port}.",
                    {"host": host, "port": port, "error": str(exc)},
                    "Check that the service is up and actually speaks TLS on this port.", group=group,
                )
            )
            continue
        days_left = (not_after - datetime.now(timezone.utc)).total_seconds() / 86400
        evidence = {
            "host": host,
            "port": port,
            "not_before": not_before.isoformat(),
            "not_after": not_after.isoformat(),
            "days_left": round(days_left, 1),
            "chain_verified": verified,
            "warn_days": warn_days,
        }
        if days_left <= 0:
            checks.append(
                HealthCheck(check_id, name, "critical", f"Certificate for {host}:{port} EXPIRED {abs(days_left):.1f} days ago.", evidence, "Renew the certificate now; clients are already failing or bypassing warnings.", group=group)
            )
        elif days_left <= critical_days:
            checks.append(
                HealthCheck(check_id, name, "critical", f"Certificate for {host}:{port} expires in {days_left:.1f} days.", evidence, "Renew immediately; check why auto-renewal (certbot/ACME) did not run.", group=group)
            )
        elif days_left <= warn_days:
            checks.append(
                HealthCheck(check_id, name, "warning", f"Certificate for {host}:{port} expires in {days_left:.1f} days.", evidence, "Renew soon, or verify the auto-renewal job is healthy.", group=group)
            )
        else:
            checks.append(
                HealthCheck(check_id, name, "ok", f"Certificate for {host}:{port} is valid for {days_left:.0f} more days.", evidence, "No action required.", group=group)
            )

    if not checks:
        checks.append(HealthCheck("network_not_configured", "Network checks", "unknown", "No network checks are configured.", {}, "Add dns_checks, tcp_checks, http_checks, or tls_checks to config.yaml if desired.", group="Network"))

    return checks
