from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def connect(database_path: str | Path) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    # In serve --interval mode the background scan thread writes while web
    # request threads read the same file. Without these a reader hitting a
    # concurrent write raises "database is locked" and 500s. busy_timeout makes
    # it wait for the lock (per connection); WAL lets readers and a writer work
    # concurrently (persists on the db file once set).
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # Some filesystems (e.g. certain network mounts) reject WAL; the default
        # rollback journal still works, just with less read/write concurrency.
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS acks (
            check_id TEXT PRIMARY KEY,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            expires_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS check_states (
            check_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            consecutive INTEGER NOT NULL,
            notified_status TEXT NOT NULL DEFAULT 'ok',
            updated_at TEXT NOT NULL
        )
        """
    )
    # Criticals pushed to an attached agent that are awaiting the agent's
    # confirmation it relayed them. If the deadline passes unacknowledged, the
    # scan loop fires the Telegram fallback so a critical is never silently lost.
    # An agent ack DEFERS the deadline (recorded in agent_acked_at) rather than
    # deleting the row — only recovery, a human ack, or the fired fallback clears
    # it, so a misbehaving agent cannot silently swallow a critical.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_alerts (
            check_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            pushed_at TEXT NOT NULL,
            deadline TEXT NOT NULL,
            agent_acked_at TEXT
        )
        """
    )
    try:  # additive migration for databases created before agent_acked_at
        conn.execute("ALTER TABLE pending_alerts ADD COLUMN agent_acked_at TEXT")
    except sqlite3.OperationalError:
        pass
    # Append-only audit + state machine for approval-gated repairs. A proposal is
    # created (proposed) → approved/denied by a human → executed → verified. See
    # docs/repair.md. Nothing here executes anything; that is homelab_guardian.repair.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repair_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_id TEXT NOT NULL,
            action TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}',
            argv TEXT NOT NULL DEFAULT '[]',
            plan_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'proposed',
            proposed_by TEXT NOT NULL DEFAULT '',
            proposed_at TEXT NOT NULL,
            approved_by TEXT,
            approved_at TEXT,
            executed_at TEXT,
            result_json TEXT,
            verify_json TEXT
        )
        """
    )
    conn.commit()
    return conn


_REPAIR_COLUMNS = (
    "id, check_id, action, params, argv, plan_json, status, proposed_by, "
    "proposed_at, approved_by, approved_at, executed_at, result_json, verify_json"
)


def _repair_dict(row) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = [c.strip() for c in _REPAIR_COLUMNS.split(",")]
    d = dict(zip(keys, row))
    for jcol in ("params", "argv", "plan_json", "result_json", "verify_json"):
        if d.get(jcol):
            try:
                d[jcol] = json.loads(d[jcol])
            except ValueError:
                pass
    return d


def create_repair_proposal(conn, check_id, action, plan, proposed_by="") -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO repair_proposals (check_id, action, params, argv, plan_json, status, proposed_by, proposed_at) "
        "VALUES (?, ?, ?, ?, ?, 'proposed', ?, ?)",
        (check_id, action, json.dumps(plan.get("params", {})), json.dumps(plan.get("argv", [])),
         json.dumps(plan), proposed_by, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_repair_proposal(conn, proposal_id) -> dict[str, Any] | None:
    row = conn.execute(f"SELECT {_REPAIR_COLUMNS} FROM repair_proposals WHERE id = ?", (proposal_id,)).fetchone()
    return _repair_dict(row)


def set_repair_decision(conn, proposal_id, status, decided_by) -> bool:
    """Approve or deny a proposal — only from the 'proposed' state. Returns True
    if the transition happened (i.e. it was still pending)."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE repair_proposals SET status = ?, approved_by = ?, approved_at = ? "
        "WHERE id = ? AND status = 'proposed'",
        (status, decided_by, now, proposal_id),
    )
    conn.commit()
    return cur.rowcount > 0


def claim_repair_execution(conn, proposal_id, result, verify) -> bool:
    """Atomically move a proposal from 'approved' to 'running' — the same
    conditional-UPDATE pattern as set_repair_decision, so two concurrent
    execute() calls cannot both claim (and run) the same proposal. Returns True
    if this caller won the claim."""
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "UPDATE repair_proposals SET status = 'running', executed_at = ?, result_json = ?, verify_json = ? "
        "WHERE id = ? AND status = 'approved'",
        (now, json.dumps(result), json.dumps(verify), proposal_id),
    )
    conn.commit()
    return cur.rowcount > 0


def record_repair_execution(conn, proposal_id, status, result, verify) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE repair_proposals SET status = ?, executed_at = ?, result_json = ?, verify_json = ? WHERE id = ?",
        (status, now, json.dumps(result), json.dumps(verify), proposal_id),
    )
    conn.commit()


def list_repair_proposals(conn, limit=20) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_REPAIR_COLUMNS} FROM repair_proposals ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [d for r in rows if (d := _repair_dict(r)) is not None]


def count_recent_repair_executions(conn, action, check_id, since_iso) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM repair_proposals WHERE action = ? AND check_id = ? "
        "AND executed_at IS NOT NULL AND executed_at >= ?",
        (action, check_id, since_iso),
    ).fetchone()
    return int(row[0]) if row else 0


def prune_scans(conn: sqlite3.Connection, retention_days: float) -> int:
    """Delete scan snapshots older than retention_days. Returns rows removed."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    cursor = conn.execute("DELETE FROM scans WHERE created_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount


def set_ack(conn: sqlite3.Connection, check_id: str, note: str = "", expires_at: str | None = None) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO acks (check_id, note, created_at, expires_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(check_id) DO UPDATE SET note = excluded.note, "
        "created_at = excluded.created_at, expires_at = excluded.expires_at",
        (check_id, note, created_at, expires_at),
    )
    conn.commit()


def remove_ack(conn: sqlite3.Connection, check_id: str) -> bool:
    cursor = conn.execute("DELETE FROM acks WHERE check_id = ?", (check_id,))
    conn.commit()
    return cursor.rowcount > 0


def list_acks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT check_id, note, created_at, expires_at FROM acks ORDER BY check_id").fetchall()
    return [{"check_id": r[0], "note": r[1], "created_at": r[2], "expires_at": r[3]} for r in rows]


def record_pending_alert(
    conn: sqlite3.Connection, check_id: str, status: str, summary: str, deadline: str
) -> None:
    """Mark a critical as pushed-to-agent and awaiting its acknowledgement. A
    re-push (fresh confirmed transition) resets any earlier agent ack — the
    agent must confirm relaying THIS occurrence."""
    conn.execute(
        "INSERT INTO pending_alerts (check_id, status, summary, pushed_at, deadline) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(check_id) DO UPDATE SET status = excluded.status, "
        "summary = excluded.summary, pushed_at = excluded.pushed_at, "
        "deadline = excluded.deadline, agent_acked_at = NULL",
        (check_id, status, summary, datetime.now(timezone.utc).isoformat(), deadline),
    )
    conn.commit()


def clear_pending_alerts(conn: sqlite3.Connection, check_ids: list[str]) -> int:
    """Remove pending alerts for the given check ids (the agent acknowledged, or
    the fallback fired). Returns rows removed."""
    if not check_ids:
        return 0
    placeholders = ",".join("?" for _ in check_ids)
    cursor = conn.execute(f"DELETE FROM pending_alerts WHERE check_id IN ({placeholders})", check_ids)
    conn.commit()
    return cursor.rowcount


def defer_pending_alerts(conn: sqlite3.Connection, check_ids: list[str], new_deadline: str) -> int:
    """Record the agent's relayed-it ack by DEFERRING the fallback deadline —
    once per push. Rows already deferred (agent_acked_at set) are untouched, so
    repeated acks cannot postpone the fallback indefinitely: it fires at most
    one deferral after the original deadline unless the check recovers or a
    human acknowledges it. Returns rows newly deferred."""
    if not check_ids:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    placeholders = ",".join("?" for _ in check_ids)
    cursor = conn.execute(
        f"UPDATE pending_alerts SET deadline = ?, agent_acked_at = ? "
        f"WHERE check_id IN ({placeholders}) AND agent_acked_at IS NULL",
        [new_deadline, now, *check_ids],
    )
    conn.commit()
    return cursor.rowcount


_PENDING_COLUMNS = "check_id, status, summary, pushed_at, deadline, agent_acked_at"


def _pending_dict(r) -> dict[str, Any]:
    return {"check_id": r[0], "status": r[1], "summary": r[2], "pushed_at": r[3],
            "deadline": r[4], "agent_acked_at": r[5]}


def list_pending_alerts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_PENDING_COLUMNS} FROM pending_alerts ORDER BY deadline"
    ).fetchall()
    return [_pending_dict(r) for r in rows]


def overdue_pending_alerts(conn: sqlite3.Connection, now: datetime | None = None) -> list[dict[str, Any]]:
    """Pending alerts whose acknowledgement deadline has passed."""
    current = (now or datetime.now(timezone.utc)).isoformat()
    rows = conn.execute(
        f"SELECT {_PENDING_COLUMNS} FROM pending_alerts WHERE deadline <= ? ORDER BY deadline",
        (current,),
    ).fetchall()
    return [_pending_dict(r) for r in rows]


def load_active_acks(conn: sqlite3.Connection, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """Active (non-expired) acknowledgments keyed by check id."""
    current = (now or datetime.now(timezone.utc)).isoformat()
    rows = conn.execute(
        "SELECT check_id, note, created_at, expires_at FROM acks "
        "WHERE expires_at IS NULL OR expires_at > ?",
        (current,),
    ).fetchall()
    return {r[0]: {"check_id": r[0], "note": r[1], "created_at": r[2], "expires_at": r[3]} for r in rows}


def load_latest_scan(conn: sqlite3.Connection) -> tuple[int, str, dict[str, Any]] | None:
    """Return (scan_id, created_at, snapshot) for the most recent scan, or None.

    A snapshot row that fails to parse is treated as absent rather than fatal:
    a corrupt historical row should never block a new scan.
    """
    row = conn.execute(
        "SELECT id, created_at, snapshot_json FROM scans ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _parse_row(row)


def _parse_row(row: tuple[Any, ...] | None) -> tuple[int, str, dict[str, Any]] | None:
    if row is None:
        return None
    try:
        snapshot = json.loads(row[2])
    except (TypeError, ValueError):
        return None
    if not isinstance(snapshot, dict):
        return None
    return int(row[0]), str(row[1]), snapshot


def load_scan(conn: sqlite3.Connection, scan_id: int) -> tuple[int, str, dict[str, Any]] | None:
    row = conn.execute(
        "SELECT id, created_at, snapshot_json FROM scans WHERE id = ?", (scan_id,)
    ).fetchone()
    return _parse_row(row)


def load_scan_before(conn: sqlite3.Connection, scan_id: int) -> tuple[int, str, dict[str, Any]] | None:
    row = conn.execute(
        "SELECT id, created_at, snapshot_json FROM scans WHERE id < ? ORDER BY id DESC LIMIT 1",
        (scan_id,),
    ).fetchone()
    return _parse_row(row)


def list_scans(conn: sqlite3.Connection, limit: int = 50) -> list[tuple[int, str, dict[str, Any]]]:
    """Newest-first scan summaries. Corrupt rows are skipped."""
    rows = conn.execute(
        "SELECT id, created_at, snapshot_json FROM scans ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()
    parsed = (_parse_row(row) for row in rows)
    return [item for item in parsed if item is not None]


def save_scan(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(snapshot, indent=2, sort_keys=True)
    cursor = conn.execute(
        "INSERT INTO scans (created_at, snapshot_json) VALUES (?, ?)",
        (created_at, payload),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)
