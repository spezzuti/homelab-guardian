from app import db
from app.alerting import update_alert_states
from app.models import HealthCheck


def _check(check_id: str, status: str = "ok", acked: bool = False) -> HealthCheck:
    return HealthCheck(check_id, check_id, status, f"{check_id} is {status}", acknowledged=acked)


def test_immediate_mode_alerts_on_first_bad_scan(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        events = update_alert_states(conn, [_check("a", "critical")], confirm_scans=1)
        assert len(events.confirmed) == 1
        assert events.confirmed[0]["previous_status"] == "ok"
        # second scan, same status: no repeat alert
        events = update_alert_states(conn, [_check("a", "critical")], confirm_scans=1)
        assert not events.any
    finally:
        conn.close()


def test_flap_damping_requires_consecutive_scans(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        # scan 1: goes bad — not yet confirmed
        events = update_alert_states(conn, [_check("a", "critical")], confirm_scans=2)
        assert not events.any
        # scan 2: still bad — now confirmed
        events = update_alert_states(conn, [_check("a", "critical")], confirm_scans=2)
        assert len(events.confirmed) == 1
        assert events.confirmed[0]["scans_observed"] == 2
    finally:
        conn.close()


def test_flap_does_not_alert(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        update_alert_states(conn, [_check("a", "critical")], confirm_scans=2)  # blip
        events = update_alert_states(conn, [_check("a", "ok")], confirm_scans=2)  # back to ok
        assert not events.any
        # ok again: counter for ok resets the bad streak; still quiet
        events = update_alert_states(conn, [_check("a", "ok")], confirm_scans=2)
        assert not events.any
    finally:
        conn.close()


def test_recovery_is_damped_symmetrically(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        update_alert_states(conn, [_check("a", "critical")], confirm_scans=2)
        update_alert_states(conn, [_check("a", "critical")], confirm_scans=2)  # confirmed bad
        events = update_alert_states(conn, [_check("a", "ok")], confirm_scans=2)  # first ok scan
        assert not events.any
        events = update_alert_states(conn, [_check("a", "ok")], confirm_scans=2)  # second ok scan
        assert len(events.recovered) == 1
        assert events.recovered[0]["previous_status"] == "critical"
    finally:
        conn.close()


def test_worsening_confirmed_status_alerts_again(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        update_alert_states(conn, [_check("a", "warning")])  # alerts warning
        events = update_alert_states(conn, [_check("a", "critical")])
        assert len(events.confirmed) == 1
        assert events.confirmed[0]["previous_status"] == "warning"
        assert events.confirmed[0]["current_status"] == "critical"
    finally:
        conn.close()


def test_acked_checks_never_alert_and_track_silently(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    try:
        events = update_alert_states(conn, [_check("a", "critical", acked=True)])
        assert not events.any
        # after unack, status has not changed — no replayed alert
        events = update_alert_states(conn, [_check("a", "critical")])
        assert not events.any
    finally:
        conn.close()
