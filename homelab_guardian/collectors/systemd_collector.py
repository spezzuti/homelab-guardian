from __future__ import annotations

import json
import subprocess
from typing import Any, Callable

from homelab_guardian.models import HealthCheck

# Read-only systemd inspection via systemctl. Two layers:
#   1. A sweep per bus (system, optionally user) for failed units and
#      restart-looping units (activating/auto-restart). A unit stuck in a
#      restart loop never reaches "failed", so checking failed alone misses
#      exactly the pathology that matters most.
#   2. Explicitly watched units with per-unit state and restart counts.
# Guardian never starts, stops, or reloads anything.

_LIST_COLUMNS = ("unit", "load", "active", "sub", "description")


def _run_systemctl(
    args: list[str],
    user_bus: bool,
    runner: Callable[..., subprocess.CompletedProcess],
) -> subprocess.CompletedProcess:
    command = ["systemctl"] + (["--user"] if user_bus else []) + args + ["--no-pager"]
    return runner(command, capture_output=True, text=True, timeout=20)


def _parse_list_output(result: subprocess.CompletedProcess) -> list[dict[str, str]] | None:
    """Parse `systemctl list-units` output: JSON on systemd >= 246, plain
    column text as fallback. None means the output was unusable."""
    stdout = result.stdout or ""
    try:
        parsed = json.loads(stdout)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except ValueError:
        pass

    rows: list[dict[str, str]] = []
    for line in stdout.splitlines():
        line = line.strip().lstrip("●○×* ")
        if not line or not line.split(None, 1)[0].endswith(".service"):
            continue
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        rows.append(dict(zip(_LIST_COLUMNS, parts + [""] * (5 - len(parts)))))
    return rows if rows else None


def _bus_sweep(
    bus_name: str,
    user_bus: bool,
    runner: Callable[..., subprocess.CompletedProcess],
) -> HealthCheck:
    check_id = f"systemd_{bus_name}_units"
    name = f"systemd services ({bus_name})"
    list_args = ["list-units", "--type=service", "--all", "--plain", "--no-legend", "--output=json"]

    try:
        result = _run_systemctl(list_args, user_bus, runner)
        if result.returncode != 0:
            # older systemd rejects --output=json entirely; retry plain
            result = _run_systemctl(
                ["list-units", "--type=service", "--all", "--plain", "--no-legend"], user_bus, runner
            )
    except FileNotFoundError:
        return HealthCheck(
            check_id,
            name,
            "unknown",
            "systemctl is not available on this host.",
            {"bus": bus_name},
            "Disable the systemd collector on hosts without systemd, or run Guardian directly on the host instead of in a container.",
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return HealthCheck(
            check_id,
            name,
            "unknown",
            f"systemctl failed on the {bus_name} bus.",
            {"bus": bus_name, "error": str(exc)},
            "Check that the systemd bus is reachable from Guardian's user (user bus needs a login session or lingering).",
        )

    if result.returncode != 0:
        return HealthCheck(
            check_id,
            name,
            "unknown",
            f"systemctl exited with {result.returncode} on the {bus_name} bus.",
            {"bus": bus_name, "stderr": (result.stderr or "").strip()[:300]},
            "Check that the systemd bus is reachable from Guardian's user (user bus needs a login session or lingering).",
        )

    units = _parse_list_output(result)
    if units is None:
        return HealthCheck(
            check_id,
            name,
            "unknown",
            f"Could not parse systemctl output on the {bus_name} bus.",
            {"bus": bus_name},
            "Run `systemctl list-units --type=service` manually to inspect the output format.",
        )

    failed = [u for u in units if u.get("active") == "failed"]
    looping = [u for u in units if u.get("active") == "activating" and u.get("sub") == "auto-restart"]
    evidence = {
        "bus": bus_name,
        "total_services": len(units),
        "failed": [{"unit": u.get("unit"), "description": u.get("description", "")} for u in failed],
        "restart_looping": [{"unit": u.get("unit"), "description": u.get("description", "")} for u in looping],
    }

    if failed or looping:
        problems = []
        if failed:
            problems.append(f"{len(failed)} failed")
        if looping:
            problems.append(f"{len(looping)} stuck in a restart loop")
        worst = ", ".join(u.get("unit", "?") for u in (failed + looping)[:5])
        return HealthCheck(
            check_id,
            name,
            "critical",
            f"{' and '.join(problems)} service(s) on the {bus_name} bus: {worst}",
            evidence,
            "Read the unit's log: journalctl -u <unit> (add --user for user units). Guardian did not restart or modify anything.",
        )

    return HealthCheck(
        check_id,
        name,
        "ok",
        f"All {len(units)} {bus_name} services are free of failures and restart loops.",
        evidence,
        "No action required.",
    )


def _watched_unit(
    item: dict[str, Any] | str,
    runner: Callable[..., subprocess.CompletedProcess],
) -> HealthCheck:
    if isinstance(item, str):
        item = {"unit": item}
    unit = str(item.get("unit", "")).strip()
    user_bus = bool(item.get("user", False))
    required = bool(item.get("required", True))
    bus_name = "user" if user_bus else "system"
    check_id = item.get("id") or f"systemd_unit_{unit.replace('.', '_').replace('@', '_')}"
    name = item.get("name") or f"Service: {unit}"

    if not unit:
        return HealthCheck(check_id, name, "unknown", "Watched systemd unit is missing a unit name.", dict(item), "Add a unit name or remove this entry.")

    properties = "ActiveState,SubState,NRestarts,UnitFileState,ExecMainStatus"
    try:
        result = _run_systemctl(["show", unit, f"--property={properties}"], user_bus, runner)
    except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:
        return HealthCheck(
            check_id,
            name,
            "unknown",
            f"Could not read state for {unit}.",
            {"unit": unit, "bus": bus_name, "error": str(exc)},
            "Check that systemctl works for Guardian's user on this bus.",
        )

    state = dict(
        line.split("=", 1) for line in (result.stdout or "").splitlines() if "=" in line
    )
    active = state.get("ActiveState", "unknown")
    sub = state.get("SubState", "")
    evidence = {
        "unit": unit,
        "bus": bus_name,
        "active_state": active,
        "sub_state": sub,
        "restarts": state.get("NRestarts"),
        "unit_file_state": state.get("UnitFileState"),
        "last_exit_status": state.get("ExecMainStatus"),
        "required": required,
    }

    if active == "active":
        return HealthCheck(check_id, name, "ok", f"{unit} is active ({sub}).", evidence, "No action required.")
    if active == "failed" or (active == "activating" and sub == "auto-restart"):
        problem = "has failed" if active == "failed" else "is stuck in a restart loop"
        return HealthCheck(
            check_id,
            name,
            "critical",
            f"{unit} {problem} (state: {active}/{sub}).",
            evidence,
            f"Read the log: journalctl {'--user ' if user_bus else ''}-u {unit} -n 50. Guardian did not restart or modify anything.",
        )
    if active in {"activating", "reloading", "deactivating"}:
        return HealthCheck(
            check_id, name, "warning", f"{unit} is transitioning (state: {active}/{sub}).", evidence,
            "Re-check shortly; persistent transitioning states usually mean a slow or wedged start.",
        )
    status = "critical" if required else "warning"
    return HealthCheck(
        check_id,
        name,
        status,
        f"{unit} is not running (state: {active}/{sub}).",
        evidence,
        "Confirm whether this service should be running here; if not, remove it from watched units or set required: false.",
    )


def collect(
    config: dict[str, Any],
    secrets: Any = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[HealthCheck]:
    checks: list[HealthCheck] = [_bus_sweep("system", False, runner)]
    if config.get("include_user", False):
        checks.append(_bus_sweep("user", True, runner))
    for item in config.get("units", []) or []:
        checks.append(_watched_unit(item, runner))
    return checks
