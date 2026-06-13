from __future__ import annotations

import subprocess
from typing import Any, Callable

from homelab_guardian.models import HealthCheck

# A host firewall is the difference between "one exposed service" and "every
# service on the box reachable by anything on the LAN". This collector answers
# one question without needing root: is a host firewall active, and does it
# default to denying inbound traffic? It reads service state and the two
# world-readable ufw config files; it never changes a rule.

GROUP = "Security"

_DENY_POLICIES = {"DROP", "REJECT"}


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _is_active(service: str, runner: Callable[..., subprocess.CompletedProcess]) -> bool | None:
    """systemctl is-active works for an unprivileged user. None means we
    could not tell (no systemctl, error)."""
    try:
        result = runner(["systemctl", "is-active", service], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    return (result.stdout or "").strip() == "active"


def _kv(text: str | None, key: str) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def collect(
    config: dict[str, Any],
    secrets: Any = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    reader: Callable[[str], str | None] = _read_text,
) -> list[HealthCheck]:
    check_id = "firewall_host"
    name = "Host firewall"

    ufw_enabled = (_kv(reader("/etc/ufw/ufw.conf"), "ENABLED") or "").lower() == "yes"
    ufw_active = _is_active("ufw", runner)
    input_policy = (_kv(reader("/etc/default/ufw"), "DEFAULT_INPUT_POLICY") or "").upper()
    nftables_active = _is_active("nftables", runner)
    firewalld_active = _is_active("firewalld", runner)

    evidence = {
        "ufw_enabled": ufw_enabled,
        "ufw_service_active": ufw_active,
        "ufw_default_input_policy": input_policy or None,
        "nftables_active": nftables_active,
        "firewalld_active": firewalld_active,
    }

    if ufw_enabled or ufw_active:
        if input_policy and input_policy not in _DENY_POLICIES:
            return [HealthCheck(
                check_id, name, "warning",
                f"ufw is active but its default incoming policy is {input_policy}, not deny.",
                evidence,
                "Set a default-deny inbound policy: `sudo ufw default deny incoming`, then allow only the ports you need.",
                group=GROUP,
            )]
        detail = f" (default incoming: {input_policy.lower()})" if input_policy else ""
        return [HealthCheck(
            check_id, name, "ok",
            f"ufw is active{detail}.",
            evidence,
            "No action required.",
            group=GROUP,
        )]

    if nftables_active or firewalld_active:
        backend = "firewalld" if firewalld_active else "nftables"
        return [HealthCheck(
            check_id, name, "ok",
            f"A host firewall ({backend}) is active.",
            evidence,
            "No action required. Guardian could not read the default policy without root; confirm it denies inbound by default.",
            group=GROUP,
        )]

    # systemctl present but nothing active -> genuinely no firewall.
    # systemctl absent (all None) -> we cannot tell.
    if ufw_active is None and nftables_active is None and firewalld_active is None:
        return [HealthCheck(
            check_id, name, "unknown",
            "Could not determine host firewall state.",
            evidence,
            "Run Guardian on the host (not a container) so it can read firewall service state, or disable this collector.",
            group=GROUP,
        )]

    return [HealthCheck(
        check_id, name, "warning",
        "No host firewall is active — every listening service is reachable by anything on the network.",
        evidence,
        "Enable one: `sudo ufw default deny incoming && sudo ufw allow ssh && sudo ufw enable`. Allow only the ports you actually use.",
        group=GROUP,
    )]
