from __future__ import annotations

import subprocess
from typing import Any, Callable

from homelab_guardian.models import HealthCheck, HealthStatus

# Two questions a homelab owner forgets between logins: are there security
# updates waiting, and is the box running an old kernel because nobody
# rebooted after a patch? Both are read-only to answer. apt-check gives the
# counts without root; /var/run/reboot-required is a flag the package tooling
# drops. Apt only for now; dnf/pacman hosts report "unknown" cleanly.

GROUP = "Host"

_APT_CHECK = "/usr/lib/update-notifier/apt-check"


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _parse_apt_check(stderr: str) -> tuple[int, int] | None:
    """apt-check writes `total;security` to stderr."""
    text = (stderr or "").strip()
    if ";" not in text:
        return None
    total, _, security = text.partition(";")
    try:
        return int(total), int(security)
    except ValueError:
        return None


def collect(
    config: dict[str, Any],
    secrets: Any = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    reader: Callable[[str], str | None] = _read_text,
) -> list[HealthCheck]:
    check_id = "system_updates"
    name = "System updates"

    reboot_pkgs_text = reader("/var/run/reboot-required.pkgs")
    reboot_required = reader("/var/run/reboot-required") is not None or reboot_pkgs_text is not None
    reboot_pkgs = sorted({p.strip() for p in (reboot_pkgs_text or "").splitlines() if p.strip()})

    try:
        result = runner([_APT_CHECK], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        status: HealthStatus = "warning" if reboot_required else "unknown"
        summary = (
            "A reboot is required to finish applying updates."
            if reboot_required
            else "No supported package manager found to count updates (apt-check missing)."
        )
        return [HealthCheck(
            check_id, name, status, summary,
            {"reboot_required": reboot_required, "reboot_required_packages": reboot_pkgs},
            "This collector currently supports apt-based systems; disable it elsewhere." if not reboot_required
            else "Reboot at a convenient time to load the updated kernel/libraries.",
            group=GROUP,
        )]
    except (subprocess.SubprocessError, OSError) as exc:
        return [HealthCheck(
            check_id, name, "unknown", "Could not count pending updates.",
            {"error": str(exc), "reboot_required": reboot_required},
            "Check that apt-check runs for Guardian's user.", group=GROUP,
        )]

    counts = _parse_apt_check(result.stderr or "")
    if counts is None:
        return [HealthCheck(
            check_id, name, "unknown", "Could not parse apt-check output.",
            {"stderr": (result.stderr or "").strip()[:200], "reboot_required": reboot_required},
            "Run /usr/lib/update-notifier/apt-check manually to inspect the output.", group=GROUP,
        )]

    total, security = counts
    evidence = {
        "pending_updates": total,
        "security_updates": security,
        "reboot_required": reboot_required,
        "reboot_required_packages": reboot_pkgs,
    }

    problems = []
    if security:
        problems.append(f"{security} security update(s) pending")
    if reboot_required:
        why = f" ({', '.join(reboot_pkgs)})" if reboot_pkgs else ""
        problems.append(f"a reboot is required{why}")

    if problems:
        return [HealthCheck(
            check_id, name, "warning", "; ".join(problems).capitalize() + ".",
            evidence,
            "Apply security updates (`sudo apt-get update && sudo apt-get upgrade`) and reboot if required. "
            "Enable unattended-upgrades with an automatic-reboot window so this stops recurring.",
            group=GROUP,
        )]

    if total:
        return [HealthCheck(
            check_id, name, "ok",
            f"{total} non-security update(s) available; none are security and no reboot is pending.",
            evidence, "No action required; routine updates can wait for your normal cadence.", group=GROUP,
        )]

    return [HealthCheck(
        check_id, name, "ok", "System is fully up to date.", evidence, "No action required.", group=GROUP,
    )]
