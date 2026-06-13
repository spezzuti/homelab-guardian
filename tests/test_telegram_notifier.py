from homelab_guardian.alerting import AlertEvents
from homelab_guardian.models import HealthCheck
from homelab_guardian.notifications.telegram_notifier import TELEGRAM_MESSAGE_LIMIT, build_message, should_notify


def _check(check_id: str, status: str = "ok", summary: str = "fine") -> HealthCheck:
    return HealthCheck(check_id, check_id, status, summary)


def _failure_event(name: str = "Service A", to_status: str = "critical") -> dict:
    return {
        "id": name.lower().replace(" ", "_"),
        "name": name,
        "previous_status": "ok",
        "current_status": to_status,
        "summary": f"{name} is down",
        "scans_observed": 2,
    }


def test_send_on_always():
    assert should_notify("always", AlertEvents())


def test_send_on_problems_requires_confirmed_failure():
    assert not should_notify("problems", AlertEvents())
    assert not should_notify("problems", AlertEvents(recovered=[_failure_event()]))
    assert should_notify("problems", AlertEvents(confirmed=[_failure_event()]))


def test_send_on_changes_fires_on_failures_and_recoveries():
    assert not should_notify("changes", AlertEvents())
    assert should_notify("changes", AlertEvents(confirmed=[_failure_event()]))
    assert should_notify("changes", AlertEvents(recovered=[_failure_event()]))


def test_message_contains_confirmed_failures():
    checks = [_check("a", "critical", "Service A is down"), _check("b", "ok")]
    events = AlertEvents(confirmed=[_failure_event()])
    message = build_message(checks, events, scan_id=9)
    assert "CRITICAL" in message
    assert "Scan #9" in message
    assert "Now failing (confirmed):" in message
    assert "Service A is down" in message


def test_message_contains_recoveries():
    events = AlertEvents(recovered=[{**_failure_event(), "previous_status": "critical", "current_status": "ok"}])
    message = build_message([_check("a", "ok")], events, scan_id=1)
    assert "Recovered:" in message
    assert "critical → <b>ok</b>" in message


def test_message_escapes_html():
    events = AlertEvents(confirmed=[{**_failure_event(), "summary": "bad <tag> & ampersand"}])
    message = build_message([_check("a", "warning")], events, scan_id=1)
    assert "<tag>" not in message
    assert "&lt;tag&gt;" in message


def test_message_without_events_lists_ongoing_problems():
    checks = [_check("a", "warning", "still degraded")]
    message = build_message(checks, AlertEvents(), scan_id=1)
    assert "still degraded" in message


def test_message_truncated_to_telegram_limit():
    events = AlertEvents(confirmed=[{**_failure_event(f"svc {i}"), "summary": "x" * 500} for i in range(30)])
    message = build_message([_check("a", "critical")], events, scan_id=1)
    assert len(message) <= TELEGRAM_MESSAGE_LIMIT
    assert message.endswith("…truncated, see report.")
