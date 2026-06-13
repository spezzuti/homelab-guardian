from homelab_guardian.diff import diff_scans
from homelab_guardian.models import HealthCheck


def _check(check_id: str, status: str = "ok", name: str | None = None) -> HealthCheck:
    return HealthCheck(check_id, name or check_id, status, f"{check_id} is {status}")


def _snapshot(*checks: HealthCheck) -> dict:
    return {"checks": [c.to_dict() for c in checks]}


def test_first_scan_has_no_previous():
    diff = diff_scans(None, [_check("a")])
    assert not diff.has_previous
    assert not diff.has_changes


def test_no_changes():
    previous = _snapshot(_check("a"), _check("b", "warning"))
    diff = diff_scans(previous, [_check("a"), _check("b", "warning")], previous_scan_id=7)
    assert diff.has_previous
    assert not diff.has_changes
    assert diff.unchanged_count == 2


def test_regression_and_improvement():
    previous = _snapshot(_check("a", "ok"), _check("b", "critical"))
    diff = diff_scans(previous, [_check("a", "critical"), _check("b", "ok")], previous_scan_id=7)
    assert [c["id"] for c in diff.regressions] == ["a"]
    assert diff.regressions[0]["previous_status"] == "ok"
    assert diff.regressions[0]["current_status"] == "critical"
    assert [c["id"] for c in diff.improvements] == ["b"]


def test_ok_to_unknown_is_regression():
    previous = _snapshot(_check("a", "ok"))
    diff = diff_scans(previous, [_check("a", "unknown")], previous_scan_id=7)
    assert len(diff.regressions) == 1
    assert not diff.improvements


def test_new_and_removed_checks():
    previous = _snapshot(_check("old", "warning"))
    diff = diff_scans(previous, [_check("new", "ok")], previous_scan_id=7)
    assert [c["id"] for c in diff.new_checks] == ["new"]
    assert [c["id"] for c in diff.removed_checks] == ["old"]
    assert diff.removed_checks[0]["previous_status"] == "warning"


def test_malformed_previous_checks_are_ignored():
    previous = {"checks": [{"name": "no id"}, {"id": "", "status": "ok"}, _check("a").to_dict()]}
    diff = diff_scans(previous, [_check("a")], previous_scan_id=7)
    assert not diff.has_changes
    assert diff.unchanged_count == 1


def test_missing_checks_key_treated_as_empty():
    diff = diff_scans({}, [_check("a")], previous_scan_id=7)
    assert [c["id"] for c in diff.new_checks] == ["a"]
