from __future__ import annotations

import socket
import time
from typing import Any, Callable
from urllib.parse import urlsplit

# After a reboot, the Guardian service can start before NetworkManager/DNS is
# up. The scan that fires immediately on startup then sees every network/TLS/
# HTTP check fail at once and flips them ok -> warning — ~19 false warnings that
# linger until the next interval. That is an environmental artifact of boot
# ordering, not a real outage, so we gate ONLY the first scan on a quick
# readiness probe. Every later scan runs ungated: a network failure then is a
# genuine finding Guardian must report, not suppress.

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_INTERVAL_SECONDS = 3.0


def _host_from_url(url: str) -> str | None:
    try:
        host = urlsplit(url if "://" in url else f"//{url}").hostname
    except ValueError:
        return None
    return host or None


def derive_probe_hosts(config: dict[str, Any]) -> list[str]:
    """Reuse the host's own network checks as readiness probes: if any of the
    things we're about to check can be resolved, the resolver is up. De-duped,
    order-preserving."""
    net = (config.get("collectors", {}) or {}).get("network", {}) or {}
    hosts: list[str] = []
    for item in net.get("dns_checks", []) or []:
        if isinstance(item, dict) and item.get("hostname"):
            hosts.append(str(item["hostname"]))
    for key in ("tcp_checks", "tls_checks"):
        for item in net.get(key, []) or []:
            if isinstance(item, dict) and item.get("host"):
                hosts.append(str(item["host"]))
    for item in net.get("http_checks", []) or []:
        if isinstance(item, dict) and item.get("url"):
            host = _host_from_url(str(item["url"]))
            if host:
                hosts.append(host)

    seen: set[str] = set()
    out: list[str] = []
    for h in hosts:
        # A bare loopback host is always resolvable, so it proves nothing about
        # whether the network is actually up — skip it as a probe.
        if h in {"localhost", "127.0.0.1", "::1"} or h in seen:
            continue
        seen.add(h)
        out.append(h)
    return out


def wait_for_network_ready(
    config: dict[str, Any],
    *,
    resolver: Callable[[str], Any] = lambda host: socket.getaddrinfo(host, None),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = print,
) -> bool:
    """Block until one probe host resolves, or the timeout elapses. Returns
    True if the network looked ready, False if we gave up and are proceeding
    anyway (a scan is still better than no scan). Never raises."""
    cfg = (config.get("app", {}) or {}).get("network_ready", {}) or {}
    if cfg.get("enabled", True) is False:
        return True

    hosts = [str(h) for h in (cfg.get("hosts") or [])] or derive_probe_hosts(config)
    if not hosts:
        # Nothing network-dependent to protect — don't stall startup.
        return True

    timeout = float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
    interval = float(cfg.get("interval_seconds", DEFAULT_INTERVAL_SECONDS))
    deadline = monotonic() + timeout

    while True:
        for host in hosts:
            try:
                resolver(host)
                return True
            except OSError:
                continue
        if monotonic() >= deadline:
            log(
                f"Network not confirmed ready after {timeout:.0f}s "
                f"(tried {', '.join(hosts)}); scanning anyway."
            )
            return False
        log("Waiting for network to come up before the first scan...")
        sleep(interval)
