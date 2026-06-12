from app import db


def test_save_and_load_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "test.sqlite")
    try:
        assert db.load_latest_scan(conn) is None
        scan_id = db.save_scan(conn, {"checks": [{"id": "a", "status": "ok"}]})
        loaded = db.load_latest_scan(conn)
        assert loaded is not None
        assert loaded[0] == scan_id
        assert loaded[2]["checks"][0]["id"] == "a"
    finally:
        conn.close()


def test_latest_scan_is_most_recent(tmp_path):
    conn = db.connect(tmp_path / "test.sqlite")
    try:
        db.save_scan(conn, {"checks": [], "marker": "first"})
        db.save_scan(conn, {"checks": [], "marker": "second"})
        loaded = db.load_latest_scan(conn)
        assert loaded is not None
        assert loaded[2]["marker"] == "second"
    finally:
        conn.close()


def test_corrupt_snapshot_returns_none(tmp_path):
    conn = db.connect(tmp_path / "test.sqlite")
    try:
        conn.execute(
            "INSERT INTO scans (created_at, snapshot_json) VALUES (?, ?)",
            ("2026-06-12T00:00:00+00:00", "{not json"),
        )
        conn.commit()
        assert db.load_latest_scan(conn) is None
    finally:
        conn.close()
