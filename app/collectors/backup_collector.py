from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from app.models import HealthCheck


def collect(config: dict[str, Any]) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    paths = config.get("paths", []) or []

    for item in paths:
        path_value = item.get("path") if isinstance(item, dict) else str(item)
        name = item.get("name", path_value) if isinstance(item, dict) else path_value
        max_age_hours = float(item.get("max_age_hours", 24)) if isinstance(item, dict) else 24.0
        check_id = item.get("id", f"backup_{name}" ) if isinstance(item, dict) else f"backup_{name}"
        path = Path(path_value).expanduser()

        try:
            if not path.exists():
                checks.append(HealthCheck(check_id, f"Backup freshness: {name}", "critical", f"Backup path does not exist: {path}", {"path": str(path)}, "Check the backup job, mount, or configured path."))
                continue
            newest = max((p.stat().st_mtime for p in path.rglob("*") if p.is_file()), default=path.stat().st_mtime)
            age_hours = (time.time() - newest) / 3600
            status = "ok" if age_hours <= max_age_hours else "warning"
            summary = f"Newest backup content is {age_hours:.1f} hours old."
            action = "No action required." if status == "ok" else "Check whether the backup job has stopped or the destination is unavailable."
            checks.append(HealthCheck(check_id, f"Backup freshness: {name}", status, summary, {"path": str(path), "age_hours": round(age_hours, 2), "max_age_hours": max_age_hours}, action))
        except Exception as exc:
            checks.append(HealthCheck(check_id, f"Backup freshness: {name}", "unknown", "Could not inspect backup path.", {"path": str(path), "error": str(exc)}, "Check local permissions and mount health."))

    if not checks:
        checks.append(HealthCheck("backups_not_configured", "Backup checks", "unknown", "No backup paths are configured.", {}, "Add backup paths to config.yaml if desired."))

    return checks
