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


def save_scan(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(snapshot, indent=2, sort_keys=True)
    cursor = conn.execute(
        "INSERT INTO scans (created_at, snapshot_json) VALUES (?, ?)",
        (created_at, payload),
    )
    conn.commit()
    return int(cursor.lastrowid)
