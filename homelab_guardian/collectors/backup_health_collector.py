from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable

from homelab_guardian.models import HealthCheck

# The backup you never check is the backup you don't have. The existing
# `backups` collector watches a directory's freshness; this one watches the
# backup *job* itself. Two modes:
#   restic  — query the repo for its newest snapshot age (authoritative, needs
#             the repo password).
#   systemd — read a backup unit's last-run result and finish time
#             (credential-free; works when the repo password is root-only).
# Read-only in both modes: it never runs a backup or touches the repo.

GROUP = "Backups"

DEFAULT_MAX_AGE_HOURS = 36.0


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in text).strip("_")
    return cleaned or "repo"


def _parse_iso(value: str) -> datetime | None:
    value = value.strip().replace("Z", "+00:00")
    # restic emits nanosecond precision; datetime handles at most microseconds.
    if "." in value:
        head, _, tail = value.partition(".")
        frac = "".join(c for c in tail if c.isdigit())
        rest = tail[len(frac):]
        value = f"{head}.{frac[:6]}{rest}"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _age_status(age_hours: float, warn: float, crit: float) -> str:
    if age_hours >= crit:
        return "critical"
    if age_hours >= warn:
        return "warning"
    return "ok"


def _check_restic(
    item: dict[str, Any], check_id: str, name: str, warn: float, crit: float,
    runner: Callable[..., subprocess.CompletedProcess], secrets: Any,
) -> HealthCheck:
    repository = item.get("repository")
    if not repository:
        return HealthCheck(check_id, name, "unknown", "Backup repo is missing a repository path.", dict(item), "Add a repository.", group=GROUP)

    import os
    env = dict(os.environ)
    env["RESTIC_REPOSITORY"] = str(repository)
    if item.get("password_file"):
        env["RESTIC_PASSWORD_FILE"] = str(item["password_file"])
    pw_env = item.get("password_env")
    if pw_env:
        value = None
        if secrets is not None and hasattr(secrets, "get"):
            value = secrets.get(pw_env)
        env["RESTIC_PASSWORD"] = value if value is not None else env.get(pw_env, "")

    try:
        result = runner(
            ["restic", "snapshots", "--json", "--latest", "1", "--no-lock"],
            capture_output=True, text=True, timeout=60, env=env,
        )
    except FileNotFoundError:
        return HealthCheck(check_id, name, "unknown", "restic is not installed.", {"repository": repository}, "Install restic, or use tool: systemd for this repo.", group=GROUP)
    except (subprocess.SubprocessError, OSError) as exc:
        return HealthCheck(check_id, name, "unknown", f"Could not query restic repo {repository}.", {"repository": repository, "error": str(exc)}, "Check the repo path and credentials.", group=GROUP)

    if result.returncode != 0:
        return HealthCheck(
            check_id, name, "critical",
            f"restic could not read repo {repository} (exit {result.returncode}).",
            {"repository": repository, "stderr": (result.stderr or "").strip()[:300]},
            "Check the repo is reachable and the password is correct — a backup that can't be read is not a backup.",
            group=GROUP,
        )

    try:
        snapshots = json.loads(result.stdout or "[]")
    except ValueError:
        return HealthCheck(check_id, name, "unknown", "Could not parse restic output.", {"repository": repository}, "Run restic snapshots --json manually.", group=GROUP)

    if not snapshots:
        return HealthCheck(check_id, name, "critical", f"Repo {repository} has no snapshots.", {"repository": repository}, "The backup has never produced a snapshot — check the backup job is running.", group=GROUP)

    newest = max(snapshots, key=lambda s: s.get("time", ""))
    taken = _parse_iso(str(newest.get("time", "")))
    if taken is None:
        return HealthCheck(check_id, name, "unknown", f"Could not read snapshot time for {repository}.", {"repository": repository}, "Inspect the repo with restic snapshots.", group=GROUP)

    age_hours = (datetime.now(timezone.utc) - taken).total_seconds() / 3600
    evidence = {
        "repository": repository, "snapshots": len(snapshots),
        "latest_snapshot": taken.isoformat(), "age_hours": round(age_hours, 1),
        "warn_age_hours": warn, "critical_age_hours": crit,
    }
    status = _age_status(age_hours, warn, crit)
    summary = f"Newest snapshot in {name} is {age_hours:.1f}h old ({len(snapshots)} snapshot(s))."
    action = "No action required." if status == "ok" else "The backup job has not produced a fresh snapshot in time — check the schedule, mount, and last run."
    return HealthCheck(check_id, name, status, summary, evidence, action, group=GROUP)


def _check_systemd(
    item: dict[str, Any], check_id: str, name: str, warn: float, crit: float,
    runner: Callable[..., subprocess.CompletedProcess],
) -> HealthCheck:
    unit = item.get("unit")
    if not unit:
        return HealthCheck(check_id, name, "unknown", "Backup repo (systemd mode) is missing a unit name.", dict(item), "Add a unit, e.g. marcus-backup.service.", group=GROUP)
    props = "Result,ExecMainStatus,ExecMainExitTimestamp,ActiveState"
    user = ["--user"] if item.get("user") else []
    try:
        result = runner(["systemctl", *user, "show", unit, f"--property={props}"], capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        return HealthCheck(check_id, name, "unknown", f"Could not read state for {unit}.", {"unit": unit, "error": str(exc)}, "Check that systemctl works for Guardian's user.", group=GROUP)

    state = dict(line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line)
    last_result = state.get("Result", "")
    active = state.get("ActiveState", "")
    finished_raw = state.get("ExecMainExitTimestamp", "").strip()
    evidence: dict[str, Any] = {"unit": unit, "last_result": last_result or None, "active_state": active or None, "last_finished": finished_raw or None}

    if active == "active":
        return HealthCheck(check_id, name, "ok", f"Backup unit {unit} is currently running.", evidence, "No action required.", group=GROUP)
    if last_result and last_result != "success":
        return HealthCheck(check_id, name, "critical", f"Last run of {unit} did not succeed (result: {last_result}).", evidence, f"Inspect the failure: journalctl -u {unit} -n 50. A failing backup job is a silent data-loss risk.", group=GROUP)
    if not finished_raw or finished_raw in {"n/a", "0"}:
        if last_result == "success":
            # A oneshot unit's ExecMainExitTimestamp is reset on reboot, so a
            # successful past run looks timestamp-less until it runs again.
            return HealthCheck(
                check_id, name, "warning",
                f"{unit} reports its last result as success, but no completed-run time is recorded (e.g. since a reboot) — recency can't be confirmed.",
                evidence,
                "Wait for the next scheduled run, or use restic mode for an authoritative snapshot age that survives reboots.",
                group=GROUP,
            )
        return HealthCheck(check_id, name, "warning", f"Backup unit {unit} has no record of a completed run.", evidence, "Trigger it once and confirm it completes; check the timer is enabled.", group=GROUP)

    finished = None
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S"):
        try:
            finished = datetime.strptime(finished_raw, fmt)
            break
        except ValueError:
            continue
    if finished is None:
        return HealthCheck(check_id, name, "unknown", f"Could not parse last-run time for {unit}.", evidence, "Check ExecMainExitTimestamp formatting.", group=GROUP)
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=timezone.utc)

    age_hours = (datetime.now(timezone.utc) - finished).total_seconds() / 3600
    evidence.update({"age_hours": round(age_hours, 1), "warn_age_hours": warn, "critical_age_hours": crit})
    status = _age_status(age_hours, warn, crit)
    summary = f"{unit} last completed successfully {age_hours:.1f}h ago."
    action = "No action required." if status == "ok" else "The backup job has not completed within its window — check the timer and the last run."
    return HealthCheck(check_id, name, status, summary, evidence, action, group=GROUP)


def _check_repo(item: dict[str, Any], runner: Callable[..., subprocess.CompletedProcess], secrets: Any) -> HealthCheck:
    name = item.get("name") or f"Backup: {item.get('id') or item.get('repository') or item.get('unit') or 'repo'}"
    check_id = item.get("id") or f"backup_health_{_slug(str(item.get('repository') or item.get('unit') or 'repo'))}"
    warn = float(item.get("max_age_hours", DEFAULT_MAX_AGE_HOURS))
    crit = float(item.get("critical_age_hours", warn * 3))
    tool = str(item.get("tool", "restic")).lower()
    if tool == "systemd":
        return _check_systemd(item, check_id, name, warn, crit, runner)
    return _check_restic(item, check_id, name, warn, crit, runner, secrets)


def collect(
    config: dict[str, Any],
    secrets: Any = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[HealthCheck]:
    repos = config.get("repos", []) or []
    if not repos:
        return [HealthCheck(
            "backup_health_not_configured", "Backup health", "unknown",
            "No backup repos are configured to monitor.",
            {}, "Add repos: with a restic repository or a systemd backup unit to watch.", group=GROUP,
        )]
    return [_check_repo(item, runner, secrets) for item in repos]
