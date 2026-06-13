from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

HealthStatus = Literal["ok", "warning", "critical", "unknown"]


@dataclass(slots=True)
class HealthCheck:
    id: str
    name: str
    status: HealthStatus
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recommended_action: str = "No action required."
    # Coarse grouping label used to roll many checks up under one expandable
    # heading in the dashboard (e.g. "Core services", "Security", "Backups").
    # Empty means "derive a group from the check id" so older snapshots and
    # collectors that don't set one still group sensibly. Keep groups few and
    # human — they are the headings a newcomer scans, not a taxonomy.
    group: str = ""
    # Acknowledged checks keep their real status but are muted: excluded from
    # overall status, change detection, and notifications, and rendered in
    # their own collapsed section.
    acknowledged: bool = False
    ack_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
