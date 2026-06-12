from app.diff import ScanDiff
from app.models import HealthCheck
from app.notifications.telegram_notifier import TELEGRAM_MESSAGE_LIMIT, build_message, should_notify


def _check(check_id: str, status: str = "ok", summary: str = "fine") -> HealthCheck:
    return HealthCheck(check_id, check_id, status, summary)


def _diff_with_regression() -> ScanDiff:
    diff = ScanDiff(previous_scan_id=1, previous_created_at="2026-06-12T00:00:00+00:00")
    diff.regressions.append(
        {"id": "a", "name": "Service A", "previous_status": "ok", "current_status": "critical", "summary": "down"}
    )
    return diff


def test_send_on_always():
    assert should_notify("always", "ok", None)


def test_send_on_problems_requires_problem():
    assert not should_notify("problems", "ok", _diff_with_regression())
    assert should_notify("problems", "warning", None)
    assert should_notify("problems", "critical", None)


def test_send_on_changes_requires_changes():
    assert should_notify("changes", "ok", _diff_with_regression())
    quiet = ScanDiff(previous_scan_id=1, unchanged_count=5)
    assert not should_notify("changes", "ok", quiet)


def test_send_on_changes_first_scan_only_if_unhealthy():
    first = ScanDiff()
    assert not should_notify("changes", "ok", first)
    assert should_notify("changes", "critical", first)


def test_message_contains_problems_and_changes():
    checks = [_check("a", "critical", "Service A is down"), _check("b", "ok")]
    message = build_message(checks, _diff_with_regression(), scan_id=9)
    assert "CRITICAL" in message
    assert "Scan #9" in message
    assert "Service A is down" in message
    assert "What changed:" in message
    assert "ok → <b>critical</b>" in message


def test_message_escapes_html():
    checks = [_check("a", "warning", "bad <tag> & ampersand")]
    message = build_message(checks, None, scan_id=1)
    assert "<tag>" not in message
    assert "&lt;tag&gt;" in message


def test_message_truncated_to_telegram_limit():
    checks = [_check(f"c{i}", "warning", "x" * 400) for i in range(30)]
    message = build_message(checks, None, scan_id=1)
    assert len(message) <= TELEGRAM_MESSAGE_LIMIT
    assert message.endswith("…truncated, see report.")
