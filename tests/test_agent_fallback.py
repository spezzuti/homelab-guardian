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

def test_acknowledge_alerts_clears(tmp_path):
    path = str(tmp_path / "g.sqlite")
    conn = db.connect(path)
    db.record_pending_alert(conn, "a", "critical", "x", _future())
    conn.close()
    assert acknowledge_alerts_payload(path, ["a"])["acknowledged_receipt"] == 1
    assert pending_alerts_payload(path) == []


def test_acknowledge_alerts_requires_ids(tmp_path):
    path = str(tmp_path / "g.sqlite")
    db.connect(path).close()
    assert acknowledge_alerts_payload(path, [])["acknowledged_receipt"] == 0


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
