from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from homelab_guardian.models import HealthCheck


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


def collect(config: dict[str, Any], secrets: Any = None) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    paths = config.get("paths", []) or []

    if not paths:
        return [
            HealthCheck(
                "backups_not_configured",
                "Backup checks",
                "unknown",
                "Backup checks are enabled, but no backup paths are configured yet.",
                {"configured_paths": 0, "configuration_complete": False},
                "This is configuration incomplete, not a detected backup failure. Add backup paths to config.yaml, or disable backup checks until ready.",
            )
        ]

    now = datetime.now(timezone.utc)
    for item in paths:
        if isinstance(item, dict):
            path_value = item.get("path")
            name = item.get("name") or path_value or "unknown backup path"
            max_age_days = _max_age_days(item)
            required = bool(item.get("required", True))
            check_id = item.get("id") or f"backup_{str(name).lower().replace(' ', '_')}"
        else:
            path_value = str(item)
            name = path_value
            max_age_days = 1.0
            required = True
            check_id = f"backup_{name}"

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
            if not path.exists():
                status = "critical" if required else "warning"
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

            newest_mtime, newest_path, file_count = _latest_file_mtime(path)
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
            status = "ok" if age_hours <= max_age_hours else "warning"
            action = "No action required." if status == "ok" else "Check whether the backup job has stopped or the destination is stale."
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
