from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models import HealthCheck


def _latest_mtime(path: Path) -> tuple[float, Path]:
    newest_mtime = path.stat().st_mtime
    newest_path = path
    if path.is_dir():
        for child in path.rglob("*"):
            try:
                child_mtime = child.stat().st_mtime
            except OSError:
                continue
            if child_mtime > newest_mtime:
                newest_mtime = child_mtime
                newest_path = child
    return newest_mtime, newest_path


def _age_parts(age_hours: float) -> dict[str, float]:
    return {"hours": round(age_hours, 2), "days": round(age_hours / 24, 2)}


def _max_age_days(item: dict[str, Any]) -> float:
    if "max_age_days" in item:
        return float(item["max_age_days"])
    if "max_age_hours" in item:
        return float(item["max_age_hours"]) / 24
    return 1.0


def collect(config: dict[str, Any]) -> list[HealthCheck]:
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
                        {"path": str(path), "exists": False, "required": required, "max_age_days": max_age_days},
                        "Check whether the backup destination, mount, or configured path changed.",
                    )
                )
                continue

            newest_mtime, newest_path = _latest_mtime(path)
            latest = datetime.fromtimestamp(newest_mtime, tz=timezone.utc)
            age_hours = (now - latest).total_seconds() / 3600
            max_age_hours = max_age_days * 24
            age = _age_parts(age_hours)
            status = "ok" if age_hours <= max_age_hours else "warning"
            action = "No action required." if status == "ok" else "Check whether the backup job has stopped or the destination is stale."
            summary = f"Latest backup content is {age['days']} days / {age['hours']} hours old."
            checks.append(
                HealthCheck(
                    check_id,
                    f"Backup freshness: {name}",
                    status,
                    summary,
                    {
                        "path": str(path),
                        "exists": True,
                        "required": required,
                        "latest_path": str(newest_path),
                        "latest_modified_utc": latest.isoformat(),
                        "age": age,
                        "max_age_days": max_age_days,
                    },
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
