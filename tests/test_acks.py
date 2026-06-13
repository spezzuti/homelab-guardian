from datetime import datetime, timedelta, timezone

from homelab_guardian import db
from homelab_guardian.models import HealthCheck
from homelab_guardian.notifications.telegram_notifier import build_message
from homelab_guardian.reports.markdown_report import overall_status, render
from homelab_guardian.web import overall_of, render_scan_page
from homelab_guardian.diff import ScanDiff


def _check(check_id: str, status: str = "ok", acked: bool = False, note: str = "") -> HealthCheck:
    return HealthCheck(
        check_id, check_id.replace("_", " ").title(), status, f"{check_id} is {status}",
        acknowledged=acked, ack_note=note,
    )


def test_ack_crud_and_expiry(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        now = datetime.now(timezone.utc)
        db.set_ack(conn, "noisy_check", note="known issue")
        db.set_ack(conn, "expired_check", expires_at=(now - timedelta(days=1)).isoformat())
        db.set_ack(conn, "future_check", expires_at=(now + timedelta(days=1)).isoformat())

        active = db.load_active_acks(conn)
        assert set(active) == {"noisy_check", "future_check"}
        assert active["noisy_check"]["note"] == "known issue"

        assert len(db.list_acks(conn)) == 3  # expired acks remain listed
        assert db.remove_ack(conn, "noisy_check")
        assert not db.remove_ack(conn, "noisy_check")
        assert "noisy_check" not in db.load_active_acks(conn)
    finally:
        conn.close()


def test_set_ack_upserts(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        db.set_ack(conn, "x", note="first")
        db.set_ack(conn, "x", note="second")
        acks = db.list_acks(conn)
        assert len(acks) == 1
        assert acks[0]["note"] == "second"
    finally:
        conn.close()


def test_overall_status_ignores_acknowledged():
    checks = [_check("a", "critical", acked=True), _check("b", "ok")]
    assert overall_status(checks) == "ok"
    assert overall_of(checks) == "ok"
    checks.append(_check("c", "warning"))
    assert overall_status(checks) == "warning"


def test_report_moves_acked_to_muted_section():
    checks = [_check("noisy", "critical", acked=True, note="waiting on part"), _check("fine", "ok")]
    report = render(checks)
    assert "**✅ OK**" in report  # overall ignores the acked critical
    assert "Acknowledged — muted known issues (1)" in report
    assert "waiting on part" in report
    assert "Critical issues" not in report
    assert "- Acknowledged (muted): 1" in report


def test_telegram_message_excludes_acked():
    from homelab_guardian.alerting import AlertEvents

    checks = [_check("noisy", "critical", acked=True), _check("fine", "ok")]
    message = build_message(checks, AlertEvents(), scan_id=1)
    assert "OK" in message
    assert "Noisy" not in message


def test_web_page_shows_acked_section_and_pill():
    checks = [_check("noisy", "critical", acked=True, note="known"), _check("fine", "ok")]
    snapshot = {"app": "G", "checks": [c.to_dict() for c in checks]}
    page = render_scan_page((1, "2026-06-13T00:00:00+00:00", snapshot), ScanDiff(), [], refresh_seconds=0)
    assert "OK" in page
    assert "Acknowledged — muted known issues (1)" in page
    assert "acknowledged</span>" in page
    assert "guardian unack" in page
