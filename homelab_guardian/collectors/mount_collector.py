from __future__ import annotations

import os
from typing import Any

from homelab_guardian.models import HealthCheck

# Verifies that configured mountpoints are actually mounted. A dropped NAS/NFS/
# CIFS mount is a common silent homelab failure: the mountpoint directory still
# exists, so a plain disk-usage check sees the underlying root filesystem and
# stays green while the share is gone. This checks the real thing with
# os.path.ismount — read-only, nothing is mounted or written.

GROUP = "Storage"


def _slug(path: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in path).strip("_")
    return cleaned or "root"


def collect(config: dict[str, Any], secrets: Any = None) -> list[HealthCheck]:
    checks: list[HealthCheck] = []
    for item in config.get("mounts", []) or []:
        if isinstance(item, str):
            item = {"path": item}
        path = str(item.get("path", "")).strip()
        name = item.get("name") or f"Mount: {path}"
        required = bool(item.get("required", True))
        check_id = item.get("id") or f"mount_{_slug(path)}"
        if not path:
            checks.append(HealthCheck(check_id, name, "unknown", "Mount check is missing a path.",
                                      dict(item), "Add a path or remove this mount check.", group=GROUP))
            continue
        try:
            exists = os.path.exists(path)
            mounted = bool(exists and os.path.ismount(path))
        except OSError as exc:
            checks.append(HealthCheck(check_id, name, "unknown", f"Could not check {path}.",
                                      {"path": path, "error": str(exc)},
                                      "Check permissions and that the path is reachable.", group=GROUP))
            continue

        evidence = {"path": path, "exists": exists, "mounted": mounted, "required": required}
        if mounted:
            checks.append(HealthCheck(check_id, name, "ok", f"{path} is mounted.", evidence,
                                      "No action required.", group=GROUP))
        else:
            status = "critical" if required else "warning"
            reason = "is not mounted" if exists else "mountpoint directory is missing"
            checks.append(HealthCheck(
                check_id, name, status, f"{path} {reason}.", evidence,
                f"Confirm the NAS/network is reachable, then remount (e.g. `mount {path}`) or fix the fstab entry.",
                group=GROUP,
            ))
    return checks
