from app.diff import ScanDiff, diff_scans
from app.models import HealthCheck
from app.reports.markdown_report import overall_status, render


def _check(check_id: str, status: str = "ok", summary: str = "fine") -> HealthCheck:
    return HealthCheck(check_id, check_id.replace("_", " ").title(), status, summary)


def test_overall_status_severity_order():
    assert overall_status([_check("a", "ok")]) == "ok"
    assert overall_status([_check("a", "ok"), _check("b", "unknown")]) == "unknown"
    assert overall_status([_check("a", "warning"), _check("b", "unknown")]) == "warning"
    assert overall_status([_check("a", "critical"), _check("b", "warning")]) == "critical"


def test_report_structure_and_ordering():
    checks = [
        _check("ok_one", "ok"),
        _check("crit_one", "critical", "it broke"),
        _check("warn_one", "warning"),
    ]
    report = render(checks, scan_id=3)
    assert report.startswith("# Homelab Guardian Health Report")
    assert "Scan ID: `3`" in report
    assert "**🚨 CRITICAL**" in report
    crit_pos = report.index("Crit One")
    warn_pos = report.index("Warn One")
    ok_pos = report.index("Ok One")
    assert crit_pos < warn_pos < ok_pos


def test_many_ok_checks_collapse_to_names():
    checks = [_check(f"ok_{i}", "ok") for i in range(7)]
    report = render(checks)
    assert "7 checks are OK. Showing names only" in report
    # collapsed entries do not include the evidence block
    assert report.count("```json") == 0


def test_first_scan_diff_message():
    report = render([_check("a")], diff=ScanDiff())
    assert "First recorded scan" in report


def test_no_change_diff_message():
    checks = [_check("a")]
    previous = {"checks": [c.to_dict() for c in checks]}
    diff = diff_scans(previous, checks, previous_scan_id=1, previous_created_at="t")
    report = render(checks, diff=diff)
    assert "No changes. All 1 checks have the same status as before." in report


def test_regression_appears_in_diff_section():
    previous = {"checks": [_check("svc", "ok").to_dict()]}
    current = [_check("svc", "critical", "svc fell over")]
    diff = diff_scans(previous, current, previous_scan_id=1, previous_created_at="t")
    report = render(current, diff=diff)
    assert "## What changed since last scan" in report
    assert "ok → **critical**" in report
