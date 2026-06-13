from datetime import datetime, timedelta, timezone

from app import db


def test_prune_scans(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        conn.execute(
            "INSERT INTO scans (created_at, snapshot_json) VALUES (?, ?)", (old, "{}")
        )
        conn.commit()
        fresh = db.save_scan(conn, {"checks": []})
        assert db.prune_scans(conn, 60) == 1
        remaining = db.list_scans(conn)
        assert [s[0] for s in remaining] == [fresh]
        assert db.prune_scans(conn, 60) == 0
    finally:
        conn.close()
