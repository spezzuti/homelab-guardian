from app.collectors import disk_collector


def test_default_path_when_unconfigured():
    checks = disk_collector.collect({})
    assert len(checks) == 1
    assert checks[0].id.startswith("disk_")
    assert checks[0].status in {"ok", "warning", "critical"}
    assert checks[0].evidence["total_gb"] > 0


def test_missing_path_is_unknown():
    checks = disk_collector.collect({"paths": [{"id": "d1", "name": "broken"}]})
    assert checks[0].status == "unknown"


def test_nonexistent_mount_is_unknown(tmp_path):
    checks = disk_collector.collect({"paths": [str(tmp_path / "not-mounted")]})
    assert checks[0].status == "unknown"
    assert "error" in checks[0].evidence


def test_thresholds(tmp_path):
    base = {"path": str(tmp_path), "id": "d1"}
    ok = disk_collector.collect({"paths": [{**base, "warn_percent": 99.9, "critical_percent": 100}]})
    assert ok[0].status == "ok"
    warn = disk_collector.collect({"paths": [{**base, "warn_percent": 0, "critical_percent": 100}]})
    assert warn[0].status == "warning"
    crit = disk_collector.collect({"paths": [{**base, "warn_percent": 0, "critical_percent": 0}]})
    assert crit[0].status == "critical"
    assert crit[0].evidence["percent_used"] >= 0
