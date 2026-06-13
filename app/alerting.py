from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.diff import SEVERITY_RANK
from app.models import HealthCheck

# Flap damping. A single blip — wifi hiccup, service restart, slow DNS —
# should not page anyone. Each check keeps an alert state: its current
# status, how many consecutive scans it has held that status, and the last
# status the user was notified about. A transition is only announced once
# the new status has held for `confirm_scans` consecutive scans, in both
# directions (failures and recoveries), so a flapping check stays quiet.


@dataclass(slots=True)
class AlertEvents:
    confirmed: list[dict[str, Any]] = field(default_factory=list)
    recovered: list[dict[str, Any]] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.confirmed or self.recovered)


def update_alert_states(
    conn: sqlite3.Connection,
    checks: list[HealthCheck],
    confirm_scans: int = 1,
) -> AlertEvents:
    """Advance every check's alert state and return the transitions that are
    now confirmed and worth announcing. Acknowledged checks update silently:
    their notified status tracks reality so unacking never replays history."""
    confirm_scans = max(1, int(confirm_scans))
    events = AlertEvents()
    now = datetime.now(timezone.utc).isoformat()

    for check in checks:
        row = conn.execute(
            "SELECT status, consecutive, notified_status FROM check_states WHERE check_id = ?",
            (check.id,),
        ).fetchone()
        previous_status = row[0] if row else None
        consecutive = (row[1] + 1) if row and previous_status == check.status else 1
        notified = row[2] if row else "ok"

        new_notified = notified
        if check.acknowledged:
            new_notified = check.status
        elif check.status != notified and consecutive >= confirm_scans:
            event = {
                "id": check.id,
                "name": check.name,
                "previous_status": notified,
                "current_status": check.status,
                "summary": check.summary,
                "scans_observed": consecutive,
            }
            if SEVERITY_RANK.get(check.status, 1) > SEVERITY_RANK.get(notified, 0):
                events.confirmed.append(event)
            else:
                events.recovered.append(event)
            new_notified = check.status

        conn.execute(
            "INSERT INTO check_states (check_id, status, consecutive, notified_status, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(check_id) DO UPDATE SET status = excluded.status, "
            "consecutive = excluded.consecutive, notified_status = excluded.notified_status, "
            "updated_at = excluded.updated_at",
            (check.id, check.status, consecutive, new_notified, now),
        )

    conn.commit()
    return events
