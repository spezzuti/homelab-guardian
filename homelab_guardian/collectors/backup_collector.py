from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from homelab_guardian.collectors._util import ProbeTimeout, probe_with_timeout
from homelab_guardian.models import HealthCheck, HealthStatus

# A backup path on a dropped NFS/CIFS mount can make stat()/rglob() block in the
# kernel indefinitely. Bound the filesystem walk so one hung destination can't
# stall the whole scan. Generous by default (large backup trees are legitimately
# slow to walk); override per-path with `probe_timeout_seconds`.
DEFAULT_PROBE_TIMEOUT = 30.0


def _latest_file_mtime(path: Path) -> tuple[float | None, Path | None, int]:
    if path.is_file():
        return path.stat().st_mtime, path, 1

    newest_mtime: float | None = None
    newest_path: Path | None = None
    file_count = 0

    for child in path.rglob("*"):
        try:
            if not child.is_file():
                continue
            child_mtime = child.stat().st_mtime
        except OSError:
            continue

        file_count += 1
        if newest_mtime is None or child_mtime > newest_mtime:
            newest_mtime = child_mtime
            newest_path = child

    return newest_mtime, newest_path, file_count


def _backup_evidence(
    path: Path,
    *,
    exists: bool,
    required: bool,
    latest_item: Path | None,
    latest_item_mtime: datetime | None,
    age_hours: float | None,
    age_days: float | None,
    max_age_days: float,
    file_count: int,
) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": exists,
        "required": required,
        "latest_item": str(latest_item) if latest_item else None,
        "latest_item_mtime": latest_item_mtime.isoformat() if latest_item_mtime else None,
        "age_hours": round(age_hours, 2) if age_hours is not None else None,
        "age_days": round(age_days, 2) if age_days is not None else None,
        "max_age_days": max_age_days,
        "file_count": file_count,
    }


def _max_age_days(item: dict[str, Any]) -> float:
    if "max_age_days" in item:
        return float(item["max_age_days"])
    if "max_age_hours" in item:
        return float(item["max_age_hours"]) / 24
    return 1.0


def _critical_age_days(item: Any, max_age_days: float) -> float:
    """When a *required* backup is this stale it is critical, not just a warning
    — a backup job that silently died days ago is a data-loss risk, matching
    backup_health's escalation. Defaults to 3× the freshness window."""
    if isinstance(item, dict):
        if "critical_age_days" in item:
            return float(item["critical_age_days"])
        if "critical_age_hours" in item:
            return float(item["critical_age_hours"]) / 24
    return max_age_days * 3


def collect(config: dict[str, Any], secrets: Any = None) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    paths = config.get("paths", []) or []

    if not paths:
        # Enabled-but-unconfigured is not a finding — it's calm by default.
        # The "you turned this on but added no paths" guidance lives in
        # `guardian doctor` (preflight), so an empty backups section reports
        # nothing here rather than dragging the Backups group to "unknown".
        return []

    now = datetime.now(timezone.utc)
    for item in paths:
        if isinstance(item, dict):
            path_value = item.get("path")
            name = item.get("name") or path_value or "unknown backup path"
            max_age_days = _max_age_days(item)
            required = bool(item.get("required", True))
            check_id = item.get("id") or f"backup_{str(name).lower().replace(' ', '_')}"
            probe_timeout = float(item.get("probe_timeout_seconds", DEFAULT_PROBE_TIMEOUT))
        else:
            path_value = str(item)
            name = path_value
            max_age_days = 1.0
            required = True
            check_id = f"backup_{name}"
            probe_timeout = DEFAULT_PROBE_TIMEOUT

        if not path_value:
            checks.append(
                HealthCheck(
                    check_id,
                    f"Backup freshness: {name}",
                    "unknown",
                    "Backup check is missing a path.",
                    {"configured_item": item},
                    "Add a path or remove this backup check.",
                )
            )
            continue

        path = Path(path_value).expanduser()
        try:
            if not probe_with_timeout(path.exists, probe_timeout):
                status: HealthStatus = "critical" if required else "warning"
                checks.append(
                    HealthCheck(
                        check_id,
                        f"Backup freshness: {name}",
                        status,
                        f"Backup path is missing: {path}",
                        _backup_evidence(
                            path,
                            exists=False,
                            required=required,
                            latest_item=None,
                            latest_item_mtime=None,
                            age_hours=None,
                            age_days=None,
                            max_age_days=max_age_days,
                            file_count=0,
                        ),
                        "Check whether the backup destination, mount, or configured path changed.",
                    )
                )
                continue

            newest_mtime, newest_path, file_count = probe_with_timeout(
                lambda: _latest_file_mtime(path), probe_timeout
            )
            if newest_mtime is None or newest_path is None:
                status = "critical" if required else "unknown"
                checks.append(
                    HealthCheck(
                        check_id,
                        f"Backup freshness: {name}",
                        status,
                        "Backup path exists, but no files were found inside it.",
                        _backup_evidence(
                            path,
                            exists=True,
                            required=required,
                            latest_item=None,
                            latest_item_mtime=None,
                            age_hours=None,
                            age_days=None,
                            max_age_days=max_age_days,
                            file_count=file_count,
                        ),
                        "Confirm the backup job is writing files to this path, or point Guardian at the correct backup output directory.",
                    )
                )
                continue

            latest = datetime.fromtimestamp(newest_mtime, tz=timezone.utc)
            age_hours = (now - latest).total_seconds() / 3600
            age_days = age_hours / 24
            max_age_hours = max_age_days * 24
            critical_age_hours = _critical_age_days(item, max_age_days) * 24
            if age_hours <= max_age_hours:
                status = "ok"
                action = "No action required."
            elif required and age_hours >= critical_age_hours:
                status = "critical"
                action = "A required backup has been stale for too long — the job likely died. Fix it before the last good copy ages out."
            else:
                status = "warning"
                action = "Check whether the backup job has stopped or the destination is stale."
            summary = f"Newest backup file is {round(age_days, 2)} days / {round(age_hours, 2)} hours old."
            checks.append(
                HealthCheck(
                    check_id,
                    f"Backup freshness: {name}",
                    status,
                    summary,
                    _backup_evidence(
                        path,
                        exists=True,
                        required=required,
                        latest_item=newest_path,
                        latest_item_mtime=latest,
                        age_hours=age_hours,
                        age_days=age_days,
                        max_age_days=max_age_days,
                        file_count=file_count,
                    ),
                    action,
                )
            )
        except ProbeTimeout:
            checks.append(
                HealthCheck(
                    check_id,
                    f"Backup freshness: {name}",
                    "unknown",
                    f"Backup path did not respond within {probe_timeout:g}s (stale/hung mount, or a very large tree).",
                    {"path": str(path), "required": required, "probe_timed_out": True},
                    "Check whether the backup mount is stale/hung; if the tree is just large, raise probe_timeout_seconds for this path.",
                )
            )
        except Exception as exc:
            checks.append(
                HealthCheck(
                    check_id,
                    f"Backup freshness: {name}",
                    "unknown",
                    "Could not inspect backup path.",
                    {"path": str(path), "error": str(exc)},
                    "Check local permissions and mount health.",
                )
            )

    return checks
