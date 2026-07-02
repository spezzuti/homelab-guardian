import os
import time

from homelab_guardian.collectors import backup_collector


def _config(path, **overrides):
    item = {"id": "b1", "name": "test backup", "path": str(path), "max_age_days": 1, "required": True}
    item.update(overrides)
    return {"paths": [item]}


def test_no_paths_reports_nothing():
    # Enabled-but-unconfigured is calm by default — the guidance lives in
    # `guardian doctor` (preflight), not as noise on the live dashboard.
    assert backup_collector.collect({"paths": []}) == []


def test_fresh_backup_is_ok(tmp_path):
    (tmp_path / "backup.tar").write_bytes(b"data")
    checks = backup_collector.collect(_config(tmp_path))
    assert checks[0].status == "ok"
    assert checks[0].evidence["file_count"] == 1


def test_stale_backup_is_warning(tmp_path):
    marker = tmp_path / "backup.tar"
    marker.write_bytes(b"data")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(marker, (two_days_ago, two_days_ago))
    checks = backup_collector.collect(_config(tmp_path))
    assert checks[0].status == "warning"
    assert checks[0].evidence["age_days"] >= 1.9


def test_very_stale_required_backup_is_critical(tmp_path):
    # A required backup past its critical age (default 3x the freshness window)
    # is a data-loss risk, not just a warning.
    marker = tmp_path / "backup.tar"
    marker.write_bytes(b"data")
    ten_days_ago = time.time() - 10 * 24 * 3600
    os.utime(marker, (ten_days_ago, ten_days_ago))
    checks = backup_collector.collect(_config(tmp_path))  # max_age_days=1 -> crit at 3d
    assert checks[0].status == "critical"


def test_very_stale_optional_backup_stays_warning(tmp_path):
    marker = tmp_path / "backup.tar"
    marker.write_bytes(b"data")
    ten_days_ago = time.time() - 10 * 24 * 3600
    os.utime(marker, (ten_days_ago, ten_days_ago))
    checks = backup_collector.collect(_config(tmp_path, required=False))
    assert checks[0].status == "warning"


def test_missing_required_path_is_critical(tmp_path):
    checks = backup_collector.collect(_config(tmp_path / "missing"))
    assert checks[0].status == "critical"


def test_missing_optional_path_is_warning(tmp_path):
    checks = backup_collector.collect(_config(tmp_path / "missing", required=False))
    assert checks[0].status == "warning"


def test_empty_required_dir_is_critical(tmp_path):
    checks = backup_collector.collect(_config(tmp_path))
    assert checks[0].status == "critical"
    assert "no files" in checks[0].summary


def test_hung_backup_path_times_out(tmp_path, monkeypatch):
    # A dropped mount that makes rglob/stat block must be bounded, not hang the
    # scan; it degrades to unknown with a clear reason.
    (tmp_path / "backup.tar").write_bytes(b"data")
    monkeypatch.setattr(
        backup_collector, "_latest_file_mtime", lambda path: time.sleep(30)
    )
    checks = backup_collector.collect(_config(tmp_path, probe_timeout_seconds=0.1))
    assert checks[0].status == "unknown"
    assert checks[0].evidence["probe_timed_out"] is True


def test_newest_file_wins_in_nested_dirs(tmp_path):
    old = tmp_path / "old.tar"
    old.write_bytes(b"old")
    stale_time = time.time() - 5 * 24 * 3600
    os.utime(old, (stale_time, stale_time))
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "new.tar").write_bytes(b"new")
    checks = backup_collector.collect(_config(tmp_path))
    assert checks[0].status == "ok"
    assert checks[0].evidence["latest_item"].endswith("new.tar")
    assert checks[0].evidence["file_count"] == 2
