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


def _unreachable_status(item: dict[str, Any]) -> str:
    """Severity for a target Guardian was told to watch but cannot reach AT ALL
    (connection refused, no response, name won't resolve, no TLS handshake).

    That is the canonical "this thing is gone" condition — a main server going
    dark — so it is `critical` by default, which is what trips Guardian's
    deterministic alert path (the agent-mode Telegram fallback is gated on a
    confirmed critical). Noisy or genuinely optional targets can opt back down
    to a softer `warning` with `critical_on_unreachable: false`.

    Note this is *unreachable*, not *degraded*: a service that answers with an
    unexpected HTTP status, or a certificate merely expiring soon, is reachable
    and stays a warning — only handled by the callers, not here."""
    return "warning" if item.get("critical_on_unreachable") is False else "critical"


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
                    check_id, name, _unreachable_status(item),
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
        if not host or not port:
            checks.append(HealthCheck(check_id, name, "unknown", "TCP check is missing host or port.", item, "Add host and port or remove this check.", group=group))
            continue
        # Coerce per-item numerics inside the item's own guard: a single bad
        # value must degrade only THIS check to unknown, never raise out of
        # collect() and wipe every other target's result.
        try:
            port_num = int(port)
            timeout = float(item.get("timeout", 3))
        except (TypeError, ValueError):
            checks.append(HealthCheck(check_id, name, "unknown", "TCP check has an invalid port or timeout.", {"host": host, "port": port, "timeout": item.get("timeout")}, "Set port to an integer and timeout to a number of seconds.", group=group))
            continue
        try:
            with socket.create_connection((host, port_num), timeout=timeout):
                checks.append(
                    HealthCheck(
                        check_id, name, "ok",
                        f"{host}:{port} accepted a TCP connection.",
                        {"host": host, "port": port_num, "timeout_seconds": timeout},
                        "No action required.", group=group,
                    )
                )
        except Exception as exc:
            checks.append(
                HealthCheck(
                    check_id, name, _unreachable_status(item),
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
        if not url:
            checks.append(HealthCheck(check_id, name, "unknown", "HTTP check is missing a URL.", item, "Add a URL or remove this check.", group=group))
            continue
        method = str(item.get("method", "GET")).upper()
        # Homelab services commonly use self-signed certificates. verify_tls: false
        # keeps the reachability check useful without forcing users to install CAs.
        verify_tls = bool(item.get("verify_tls", True))
        # Guard the numeric/expected-status parsing per item so one bad value
        # degrades only this check instead of aborting the whole collector.
        try:
            timeout = float(item.get("timeout", 5))
            expected = _expected_status(item)
        except (TypeError, ValueError):
            checks.append(HealthCheck(check_id, name, "unknown", "HTTP check has an invalid timeout or expected_status.", {"url": url, "timeout": item.get("timeout"), "expected_status": item.get("expected_status", item.get("expected_statuses"))}, "Set timeout to a number and expected_status to an integer or list of integers.", group=group))
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
                    check_id, name, _unreachable_status(item),
                    f"{url} did not return an HTTP response.",
                    {"url": url, "method": method, "timeout_seconds": timeout, "expected_status": sorted(expected), "error": str(exc)},
                    "Check network path, DNS, TLS, reverse proxy, or service health.", group=group,
                )
            )

    for item in config.get("tls_checks", []) or []:
        group = item.get("group") or "Certificates"
        host = item.get("host")
        if not host:
            check_id = item.get("id") or f"tls_{host}_{item.get('port', 443)}"
            name = item.get("name") or f"TLS certificate: {host}"
            checks.append(HealthCheck(check_id, name, "unknown", "TLS check is missing a host.", item, "Add a host or remove this check.", group=group))
            continue
        # Guard the numeric parsing per item so a bad port/day/timeout value
        # degrades only this check instead of aborting the whole collector.
        try:
            port = int(item.get("port", 443))
            warn_days = float(item.get("warn_days", 14))
            critical_days = float(item.get("critical_days", 3))
            timeout = float(item.get("timeout", 5))
        except (TypeError, ValueError):
            check_id = item.get("id") or f"tls_{host}"
            name = item.get("name") or f"TLS certificate: {host}"
            checks.append(HealthCheck(check_id, name, "unknown", "TLS check has an invalid port, warn_days, critical_days, or timeout.", {"host": host, "port": item.get("port"), "warn_days": item.get("warn_days"), "critical_days": item.get("critical_days"), "timeout": item.get("timeout")}, "Set port and the day/timeout thresholds to numbers.", group=group))
            continue
        check_id = item.get("id") or f"tls_{host}_{port}"
        name = item.get("name") or f"TLS certificate: {host}"
        try:
            not_before, not_after, verified = fetch_cert_expiry(host, port, timeout=timeout)
        except Exception as exc:
            checks.append(
                HealthCheck(
                    check_id, name, _unreachable_status(item),
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

    # Enabled-but-unconfigured is not a finding — it's calm by default. The
    # "you turned this on but added no checks" guidance lives in `guardian
    # doctor` (preflight), so an empty network section reports nothing here
    # rather than dragging the Network group to an alarming "unknown".
    return checks
