from homelab_guardian import db


def test_load_scan_and_before(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        first = db.save_scan(conn, {"checks": [], "marker": 1})
        second = db.save_scan(conn, {"checks": [], "marker": 2})
        assert db.load_scan(conn, first)[2]["marker"] == 1
        assert db.load_scan(conn, 999) is None
        before = db.load_scan_before(conn, second)
        assert before[0] == first
        assert db.load_scan_before(conn, first) is None
    finally:
        conn.close()


def test_list_scans_newest_first_with_limit(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        ids = [db.save_scan(conn, {"checks": [], "n": i}) for i in range(5)]
        scans = db.list_scans(conn, limit=3)
        assert [s[0] for s in scans] == sorted(ids, reverse=True)[:3]
    finally:
        conn.close()


def test_list_scans_skips_corrupt_rows(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        good = db.save_scan(conn, {"checks": []})
        conn.execute(
            "INSERT INTO scans (created_at, snapshot_json) VALUES (?, ?)",
            ("2026-06-12T00:00:00+00:00", "{broken"),
        )
        conn.commit()
        scans = db.list_scans(conn)
        assert [s[0] for s in scans] == [good]
    finally:
        conn.close()
