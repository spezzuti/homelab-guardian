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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            snapshot_json TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


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
    return int(cursor.lastrowid)
