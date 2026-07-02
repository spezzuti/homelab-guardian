from datetime import datetime, timedelta, timezone

import yaml

from homelab_guardian import db, main
from homelab_guardian.alerting import AlertEvents
from homelab_guardian.mcp_server import acknowledge_alerts_payload, pending_alerts_payload


def _past():
    return (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()


def _future():
    return (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()


# --- db layer --------------------------------------------------------------

def test_record_list_and_clear_pending(tmp_path):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.record_pending_alert(conn, "disk", "critical", "full", _future())
    assert [p["check_id"] for p in db.list_pending_alerts(conn)] == ["disk"]
    assert db.clear_pending_alerts(conn, ["disk"]) == 1
    assert db.list_pending_alerts(conn) == []
    assert db.clear_pending_alerts(conn, []) == 0  # empty is a no-op


def test_overdue_filters_by_deadline(tmp_path):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.record_pending_alert(conn, "a", "critical", "x", _past())
    db.record_pending_alert(conn, "b", "critical", "y", _future())
    assert [o["check_id"] for o in db.overdue_pending_alerts(conn)] == ["a"]


def test_record_is_idempotent_per_check(tmp_path):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.record_pending_alert(conn, "a", "critical", "first", _future())
    db.record_pending_alert(conn, "a", "critical", "second", _future())
    rows = db.list_pending_alerts(conn)
    assert len(rows) == 1 and rows[0]["summary"] == "second"


# --- mcp callback ----------------------------------------------------------

def test_acknowledge_alerts_defers_instead_of_clearing(tmp_path):
    path = str(tmp_path / "g.sqlite")
    conn = db.connect(path)
    db.record_pending_alert(conn, "a", "critical", "x", _past())
    conn.close()
    res = acknowledge_alerts_payload(path, ["a"], defer_minutes=60)
    assert res["acknowledged_receipt"] == 1
    # Still tracked: the ack deferred the fallback, it did not cancel it.
    pending = pending_alerts_payload(path)
    assert [p["check_id"] for p in pending] == ["a"]
    assert pending[0]["agent_acked_at"] is not None
    assert pending[0]["deadline"] == res["fallback_deferred_until"]
    assert db.overdue_pending_alerts(db.connect(path)) == []  # deadline moved out


def test_acknowledge_alerts_defers_only_once(tmp_path):
    path = str(tmp_path / "g.sqlite")
    conn = db.connect(path)
    db.record_pending_alert(conn, "a", "critical", "x", _future())
    conn.close()
    assert acknowledge_alerts_payload(path, ["a"], defer_minutes=-5)["acknowledged_receipt"] == 1
    # A repeat ack cannot push the (now overdue) deadline out again.
    assert acknowledge_alerts_payload(path, ["a"], defer_minutes=60)["acknowledged_receipt"] == 0
    assert [o["check_id"] for o in db.overdue_pending_alerts(db.connect(path))] == ["a"]


def test_acknowledge_alerts_requires_ids(tmp_path):
    path = str(tmp_path / "g.sqlite")
    db.connect(path).close()
    assert acknowledge_alerts_payload(path, [])["acknowledged_receipt"] == 0


def test_repush_resets_agent_ack(tmp_path):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.record_pending_alert(conn, "a", "critical", "first", _future())
    db.defer_pending_alerts(conn, ["a"], _future())
    # A fresh confirmed critical re-pushes the alert: the old relay claim no
    # longer covers this occurrence.
    db.record_pending_alert(conn, "a", "critical", "second", _future())
    assert db.list_pending_alerts(conn)[0]["agent_acked_at"] is None


# --- main: record + overdue fallback ---------------------------------------

def test_record_pending_criticals_records_only_criticals(tmp_path):
    config = {"app": {"database_path": str(tmp_path / "g.sqlite")},
              "notifications": {"agent": {"ack_timeout_minutes": 10}}}
    events = AlertEvents(confirmed=[
        {"id": "c1", "current_status": "critical", "summary": "down"},
        {"id": "w1", "current_status": "warning", "summary": "warn"},
    ])
    main._record_pending_criticals(config, events)
    conn = db.connect(config["app"]["database_path"])
    assert [p["check_id"] for p in db.list_pending_alerts(conn)] == ["c1"]


def _agent_config(tmp_path, **agent):
    dbp = str(tmp_path / "g.sqlite")
    cfg = {"app": {"database_path": dbp},
           "notifications": {"mode": "agent",
                             "agent": {"critical_fallback": True, **agent},
                             "telegram": {"enabled": True}}}
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(p), dbp


def test_check_overdue_fires_fallback_and_clears(tmp_path, monkeypatch):
    cfg_path, dbp = _agent_config(tmp_path)
    conn = db.connect(dbp)
    db.record_pending_alert(conn, "c1", "critical", "disk full", _past())
    conn.close()
    sent = []
    monkeypatch.setattr(main.telegram_notifier, "send_text",
                        lambda config, text, secrets=None: sent.append(text) or True)
    main.check_overdue_alerts(cfg_path)
    assert sent and "did not acknowledge" in sent[0] and "disk full" in sent[0]
    assert db.list_pending_alerts(db.connect(dbp)) == []  # cleared after send


def test_check_overdue_keeps_pending_if_send_fails(tmp_path, monkeypatch):
    cfg_path, dbp = _agent_config(tmp_path)
    conn = db.connect(dbp)
    db.record_pending_alert(conn, "c1", "critical", "x", _past())
    conn.close()
    monkeypatch.setattr(main.telegram_notifier, "send_text",
                        lambda config, text, secrets=None: False)  # telegram down
    main.check_overdue_alerts(cfg_path)
    # not cleared → will retry next recheck rather than drop the critical
    assert [p["check_id"] for p in db.list_pending_alerts(db.connect(dbp))] == ["c1"]


def test_deferred_overdue_still_fires_fallback(tmp_path, monkeypatch):
    """The soft spot from the review: an agent's ack must not let a critical die
    silently. Once the deferred deadline passes, the fallback fires anyway."""
    cfg_path, dbp = _agent_config(tmp_path)
    conn = db.connect(dbp)
    db.record_pending_alert(conn, "c1", "critical", "disk full", _future())
    db.defer_pending_alerts(conn, ["c1"], _past())  # deferral window has lapsed
    conn.close()
    sent = []
    monkeypatch.setattr(main.telegram_notifier, "send_text",
                        lambda config, text, secrets=None: sent.append(text) or True)
    main.check_overdue_alerts(cfg_path)
    assert sent and "still critical after agent relay" in sent[0] and "disk full" in sent[0]
    assert db.list_pending_alerts(db.connect(dbp)) == []


def test_reconcile_clears_recovered_deferred_but_keeps_unacked(tmp_path):
    from homelab_guardian.models import HealthCheck

    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.record_pending_alert(conn, "recovered_acked", "critical", "x", _future())
    db.defer_pending_alerts(conn, ["recovered_acked"], _future())
    db.record_pending_alert(conn, "recovered_unacked", "critical", "y", _past())
    db.record_pending_alert(conn, "still_down", "critical", "z", _future())
    db.defer_pending_alerts(conn, ["still_down"], _future())
    checks = [
        HealthCheck(id="recovered_acked", name="A", status="ok", summary=""),
        HealthCheck(id="recovered_unacked", name="B", status="ok", summary=""),
        HealthCheck(id="still_down", name="C", status="critical", summary=""),
    ]
    main._reconcile_pending_alerts(conn, checks)
    remaining = {p["check_id"] for p in db.list_pending_alerts(conn)}
    # Relayed + recovered → done. Still critical → keep tracking. Never acked →
    # keep even though recovered: its fallback doubles as the agent-down signal.
    assert remaining == {"recovered_unacked", "still_down"}


def test_human_ack_clears_pending_alert(tmp_path):
    cfg_path, dbp = _agent_config(tmp_path)
    conn = db.connect(dbp)
    db.record_pending_alert(conn, "c1", "critical", "x", _past())
    conn.close()
    assert main.run_ack(cfg_path, "ack", "c1", note="on it", days=0, until="") == 0
    assert db.list_pending_alerts(db.connect(dbp)) == []


def test_check_overdue_noop_in_direct_mode(tmp_path, monkeypatch):
    dbp = str(tmp_path / "g.sqlite")
    cfg = {"app": {"database_path": dbp}, "notifications": {"mode": "direct"}}
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    conn = db.connect(dbp)
    db.record_pending_alert(conn, "c1", "critical", "x", _past())
    conn.close()
    sent = []
    monkeypatch.setattr(main.telegram_notifier, "send_text",
                        lambda config, text, secrets=None: sent.append(text) or True)
    main.check_overdue_alerts(str(p))
    assert sent == []  # not agent mode → no fallback fired
