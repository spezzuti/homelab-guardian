from __future__ import annotations

import os
from typing import Any

from homelab_guardian.collectors._util import ProbeTimeout, probe_with_timeout
from homelab_guardian.models import HealthCheck

# Verifies that configured mountpoints are actually mounted. A dropped NAS/NFS/
# CIFS mount is a common silent homelab failure: the mountpoint directory still
# exists, so a plain disk-usage check sees the underlying root filesystem and
# stays green while the share is gone. This checks the real thing with
# os.path.ismount — read-only, nothing is mounted or written.

GROUP = "Storage"

# A stat() against a dropped NFS/CIFS mount can block in the kernel for minutes;
# bound it so one hung share can't stall the whole scan. Overridable per-mount
# with `probe_timeout_seconds`.
DEFAULT_PROBE_TIMEOUT = 5.0


def _slug(path: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in path).strip("_")
    return cleaned or "root"


def _probe(path: str) -> tuple[bool, bool]:
    """(exists, mounted) for a path. Runs the actual stat()s; the caller bounds
    it with a timeout so a hung mount can't block the scan."""
    exists = os.path.exists(path)
    return exists, bool(exists and os.path.ismount(path))


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
        probe_timeout = float(item.get("probe_timeout_seconds", DEFAULT_PROBE_TIMEOUT))
        try:
            exists, mounted = probe_with_timeout(lambda: _probe(path), probe_timeout)
        except ProbeTimeout:
            # A stat that never returns is itself the failure this collector looks
            # for — a hung share reads as "not mounted", just with a clearer why.
            status = "critical" if required else "warning"
            checks.append(HealthCheck(
                check_id, name, status,
                f"{path} did not respond within {probe_timeout:g}s (stale or hung mount?).",
                {"path": path, "exists": None, "mounted": False, "required": required, "probe_timed_out": True},
                "The mount is likely stale/hung — check the NAS/network, then unmount and remount it.",
                group=GROUP,
            ))
            continue
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
