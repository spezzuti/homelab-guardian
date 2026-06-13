from __future__ import annotations

import os
import shutil
from typing import Any

from homelab_guardian.models import HealthCheck

# Disk-full is the most common silent homelab failure: backups stop, databases
# corrupt, containers crash-loop, all without a single alert from the service
# itself. This collector reads usage on configured mounts; nothing is written.

DEFAULT_WARN_PERCENT = 85
DEFAULT_CRITICAL_PERCENT = 95


def _default_path() -> str:
    if os.name == "nt":
        return os.path.splitdrive(os.getcwd())[0] + "\\"
    return "/"


def _slug(path: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in path).strip("_")
    return cleaned or "root"


def _check_path(item: dict[str, Any] | str) -> HealthCheck:
    if isinstance(item, str):
        item = {"path": item}
    path = str(item.get("path", "")).strip()
    name = item.get("name") or f"Disk space: {path or 'unknown'}"
    check_id = item.get("id") or f"disk_{_slug(path)}"
    warn_percent = float(item.get("warn_percent", DEFAULT_WARN_PERCENT))
    critical_percent = float(item.get("critical_percent", DEFAULT_CRITICAL_PERCENT))

    if not path:
        return HealthCheck(check_id, name, "unknown", "Disk check is missing a path.", dict(item), "Add a path or remove this disk check.")

    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return HealthCheck(
            check_id,
            name,
            "unknown",
            f"Could not read disk usage for {path}.",
            {"path": path, "error": str(exc)},
            "Check that the path exists and is mounted; remove the check if the mount is gone on purpose.",
        )

    percent_used = (usage.used / usage.total * 100) if usage.total else 0.0
    free_gb = usage.free / 1024**3
    evidence = {
        "path": path,
        "total_gb": round(usage.total / 1024**3, 1),
        "used_gb": round(usage.used / 1024**3, 1),
        "free_gb": round(free_gb, 1),
        "percent_used": round(percent_used, 1),
        "warn_percent": warn_percent,
        "critical_percent": critical_percent,
    }
    summary = f"{path} is {percent_used:.1f}% full ({free_gb:.1f} GiB free)."

    if percent_used >= critical_percent:
        return HealthCheck(
            check_id,
            name,
            "critical",
            summary,
            evidence,
            "Free space soon: old backups, container images (docker system df), logs, and downloads are the usual suspects. Services start failing unpredictably on a full disk.",
        )
    if percent_used >= warn_percent:
        return HealthCheck(
            check_id,
            name,
            "warning",
            summary,
            evidence,
            "Plan a cleanup before this becomes urgent: check large directories with du, prune unused container images, rotate logs.",
        )
    return HealthCheck(check_id, name, "ok", summary, evidence, "No action required.")


def collect(config: dict[str, Any], secrets: Any = None) -> list[HealthCheck]:
    paths = config.get("paths", []) or []
    if not paths:
        paths = [{"path": _default_path(), "name": "Disk space: system drive"}]
    return [_check_path(item) for item in paths]
