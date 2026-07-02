from __future__ import annotations

import glob
import subprocess
from typing import Any, Callable

from homelab_guardian.models import HealthCheck

# SSH is the front door. Two cheap, high-value questions: can someone brute
# force a password, and can root log in directly? This reads the sshd config
# the way sshd does — first matching value wins, drop-ins under
# sshd_config.d/ are included near the top of the main file — so it needs no
# root and no running sshd. fail2ban presence is reported as mitigating
# context, not a hard requirement.

GROUP = "Security"

# sshd's own defaults when a directive is absent from every config file.
_DEFAULTS = {
    "passwordauthentication": "yes",
    "permitrootlogin": "prohibit-password",
    "kbdinteractiveauthentication": "yes",
}


def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def _default_dropins() -> list[str]:
    return sorted(glob.glob("/etc/ssh/sshd_config.d/*.conf"))


def _ordered_lines(main_text: str, dropins: list[tuple[str, str]]) -> list[str]:
    """Reproduce sshd's read order: drop-ins are pulled in at the position of
    the `Include .../sshd_config.d/*.conf` line in the main config (near the
    top on Debian/Ubuntu). With no Include line, drop-ins are appended."""
    main_lines = main_text.splitlines()
    dropin_lines = [line for _, text in dropins for line in text.splitlines()]
    for i, line in enumerate(main_lines):
        stripped = line.strip().lower()
        if stripped.startswith("include") and "sshd_config.d" in stripped:
            return main_lines[:i] + dropin_lines + main_lines[i + 1:]
    return main_lines + dropin_lines


def effective_sshd_settings(main_text: str, dropins: list[tuple[str, str]]) -> dict[str, str]:
    """First-match-wins resolution of the global directives we care about.

    Resolution STOPS at the first `Match` block: directives inside a Match apply
    only conditionally (by user/address/etc.), so treating them as global would
    both raise false hardening warnings and mask real global weaknesses. We read
    only the unconditional (pre-Match) section, the same values that apply to a
    default connection — a `Match` that loosens auth for a specific source is out
    of scope for this check."""
    wanted = set(_DEFAULTS)
    found: dict[str, str] = {}
    for line in _ordered_lines(main_text, dropins):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key = parts[0].lower()
        if key == "match":
            break  # end of the global section — conditional blocks aren't global
        if key in wanted and key not in found:
            found[key] = parts[1].strip().strip('"').lower()
    return {key: found.get(key, default) for key, default in _DEFAULTS.items()}


def _is_active(service: str, runner: Callable[..., subprocess.CompletedProcess]) -> bool | None:
    try:
        result = runner(["systemctl", "is-active", service], capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    return (result.stdout or "").strip() == "active"


def collect(
    config: dict[str, Any],
    secrets: Any = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    reader: Callable[[str], str | None] = _read_text,
    dropin_lister: Callable[[], list[str]] = _default_dropins,
) -> list[HealthCheck]:
    check_id = "ssh_hardening"
    name = "SSH server hardening"

    main_text = reader("/etc/ssh/sshd_config")
    if main_text is None:
        return [HealthCheck(
            check_id, name, "unknown",
            "No SSH server config found at /etc/ssh/sshd_config.",
            {}, "Disable this collector on hosts that do not run an SSH server.",
            group=GROUP,
        )]

    dropins = [(path, reader(path) or "") for path in dropin_lister()]
    settings = effective_sshd_settings(main_text, dropins)
    fail2ban = _is_active("fail2ban", runner)

    password_auth = settings["passwordauthentication"] == "yes"
    root_login = settings["permitrootlogin"]
    root_login_weak = root_login == "yes"

    evidence = {
        "password_authentication": settings["passwordauthentication"],
        "permit_root_login": root_login,
        "kbd_interactive_authentication": settings["kbdinteractiveauthentication"],
        "fail2ban_active": fail2ban,
        "drop_in_files": [path for path, _ in dropins],
    }

    problems: list[str] = []
    if password_auth:
        problems.append("password authentication is enabled (brute-forceable)")
    if root_login_weak:
        problems.append("direct root login is permitted")

    if problems:
        action = "Set `PasswordAuthentication no` once key auth works"
        if root_login_weak:
            action += " and `PermitRootLogin no`"
        action += " (use a drop-in that sorts before 50-cloud-init.conf — sshd takes the first match)."
        if password_auth and not fail2ban:
            action += " Consider fail2ban until password auth is off."
        return [HealthCheck(
            check_id, name, "warning",
            "SSH hardening gap: " + "; ".join(problems) + ".",
            evidence, action, group=GROUP,
        )]

    return [HealthCheck(
        check_id, name, "ok",
        "SSH denies password auth and direct root login.",
        evidence, "No action required.", group=GROUP,
    )]
