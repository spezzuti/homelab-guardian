import os
import time

from homelab_guardian.collectors import backup_collector


def _config(path, **overrides):
    item = {"id": "b1", "name": "test backup", "path": str(path), "max_age_days": 1, "required": True}
    item.update(overrides)
    return {"paths": [item]}


def test_no_paths_is_unknown():
    checks = backup_collector.collect({"paths": []})
    assert checks[0].id == "backups_not_configured"
    assert checks[0].status == "unknown"


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
