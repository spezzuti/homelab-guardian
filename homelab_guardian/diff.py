from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from homelab_guardian.models import HealthCheck

# Severity for change direction: moving up this scale is a regression,
# moving down is an improvement. "unknown" sits between ok and warning
# because losing signal is worse than ok but not yet a confirmed problem.
SEVERITY_RANK = {"ok": 0, "unknown": 1, "warning": 2, "critical": 3}


@dataclass(slots=True)
class ScanDiff:
    previous_scan_id: int | None = None
    previous_created_at: str | None = None
    regressions: list[dict[str, Any]] = field(default_factory=list)
    improvements: list[dict[str, Any]] = field(default_factory=list)
    new_checks: list[dict[str, Any]] = field(default_factory=list)
    removed_checks: list[dict[str, Any]] = field(default_factory=list)
    unchanged_count: int = 0

    @property
    def has_previous(self) -> bool:
        return self.previous_scan_id is not None

    @property
    def has_changes(self) -> bool:
        return bool(self.regressions or self.improvements or self.new_checks or self.removed_checks)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _checks_by_id(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for check in checks:
        check_id = check.get("id")
        if isinstance(check_id, str) and check_id:
            indexed[check_id] = check
    return indexed


def diff_scans(
    previous_snapshot: dict[str, Any] | None,
    current_checks: list[HealthCheck],
    previous_scan_id: int | None = None,
    previous_created_at: str | None = None,
) -> ScanDiff:
    """Compare the current checks against the previous scan snapshot.

    A check is matched by its stable ``id``. Checks present now but not before
    are "new"; checks present before but not now are "removed". Status changes
    are classified as regressions or improvements by severity rank.
    """
    if previous_snapshot is None:
        return ScanDiff()

    diff = ScanDiff(previous_scan_id=previous_scan_id, previous_created_at=previous_created_at)
    previous = _checks_by_id(previous_snapshot.get("checks", []))
    seen_ids: set[str] = set()

    for check in current_checks:
        seen_ids.add(check.id)
        old = previous.get(check.id)
        if old is None:
            diff.new_checks.append(
                {"id": check.id, "name": check.name, "status": check.status, "summary": check.summary}
            )
            continue
        old_status = old.get("status", "unknown")
        if old_status == check.status:
            diff.unchanged_count += 1
            continue
        change = {
            "id": check.id,
            "name": check.name,
            "previous_status": old_status,
            "current_status": check.status,
            "summary": check.summary,
        }
        if SEVERITY_RANK.get(check.status, 1) > SEVERITY_RANK.get(old_status, 1):
            diff.regressions.append(change)
        else:
            diff.improvements.append(change)

    for check_id, old in previous.items():
        if check_id not in seen_ids:
            diff.removed_checks.append(
                {
                    "id": check_id,
                    "name": old.get("name", check_id),
                    "previous_status": old.get("status", "unknown"),
                }
            )

    return diff
