import os
from pathlib import Path

from homelab_guardian import db
from homelab_guardian.mcp_server import (
    changes_payload,
    check_payload,
    checks_payload,
    history_payload,
    problems_payload,
    resolve_database_path,
    summary_payload,
)
from homelab_guardian.models import HealthCheck


def _db(tmp_path, *snapshots) -> str:
    path = str(tmp_path / "g.sqlite")
    conn = db.connect(path)
    try:
        for snap in snapshots:
            db.save_scan(conn, snap)
    finally:
        conn.close()
    return path


def _snap(*checks: HealthCheck) -> dict:
    return {"app": "HG", "checks": [c.to_dict() for c in checks]}


def test_summary_overall_counts_and_group_rollup(tmp_path):
    path = _db(tmp_path, _snap(
        HealthCheck("firewall_host", "Host firewall", "ok", "ok", group="Security"),
        HealthCheck("exposed_services", "Exposed services", "warning", "SMB exposed", group="Security"),
        HealthCheck("disk_root", "Root disk", "ok", "26% full", group="Host"),
    ))
    s = summary_payload(path)
    assert s["overall"] == "warning"
    assert s["counts"]["warning"] == 1 and s["counts"]["ok"] == 2
    groups = {g["group"]: g for g in s["groups"]}
    assert groups["Security"]["status"] == "warning"
    assert groups["Host"]["status"] == "ok"
    # the problem group sorts ahead of the healthy one
    assert s["groups"][0]["group"] == "Security"


def test_problems_excludes_ok_and_acknowledged(tmp_path):
    crit = HealthCheck("a", "A", "critical", "broke", group="Host")
    acked = HealthCheck("b", "B", "critical", "known", group="Host")
    acked.acknowledged = True
    okc = HealthCheck("c", "C", "ok", "fine", group="Host")
    path = _db(tmp_path, _snap(crit, acked, okc))
    problems = problems_payload(path)
    assert [p["id"] for p in problems] == ["a"]
    assert problems[0]["recommended_action"]


def test_checks_filter_by_group_and_status(tmp_path):
    path = _db(tmp_path, _snap(
        HealthCheck("a", "A", "ok", "x", group="Host"),
        HealthCheck("b", "B", "warning", "y", group="Security"),
    ))
    assert {c["id"] for c in checks_payload(path)} == {"a", "b"}
    assert [c["id"] for c in checks_payload(path, group="Security")] == ["b"]
    assert [c["id"] for c in checks_payload(path, status="ok")] == ["a"]


def test_check_detail_includes_evidence(tmp_path):
    path = _db(tmp_path, _snap(
        HealthCheck("x", "X", "warning", "sum", {"port": 445}, "do thing", group="Security"),
    ))
    detail = check_payload(path, "x")
    assert detail["evidence"] == {"port": 445}
    assert detail["group"] == "Security"
    assert detail["recommended_action"] == "do thing"
    assert check_payload(path, "missing") is None


def test_changes_reports_regression(tmp_path):
    path = _db(
        tmp_path,
        _snap(HealthCheck("svc", "Service", "ok", "fine")),
        _snap(HealthCheck("svc", "Service", "critical", "down")),
    )
    changes = changes_payload(path)
    assert changes["has_previous"] and changes["has_changes"]
    assert any(r["current_status"] == "critical" for r in changes["regressions"])


def test_history_newest_first(tmp_path):
    path = _db(
        tmp_path,
        _snap(HealthCheck("a", "A", "ok", "x")),
        _snap(HealthCheck("a", "A", "warning", "y")),
    )
    history = history_payload(path, limit=5)
    assert len(history) == 2
    assert history[0]["overall"] == "warning"  # newest first


def test_summary_with_no_scans(tmp_path):
    path = str(tmp_path / "empty.sqlite")
    db.connect(path).close()
    assert summary_payload(path)["overall"] == "unknown"
    assert problems_payload(path) == []


def test_resolve_database_path_relative_to_config_dir(tmp_path):
    # An MCP client launches the server from its own cwd, so a relative db path
    # must resolve against the config file's directory, not the process cwd.
    config_path = str(tmp_path / "sub" / "config.yaml")
    config = {"app": {"database_path": "data/guardian.sqlite"}}
    resolved = resolve_database_path(config, config_path)
    assert Path(resolved) == tmp_path / "sub" / "data" / "guardian.sqlite"
    assert Path(resolved).is_absolute()


def test_resolve_database_path_absolute_is_unchanged(tmp_path):
    absolute = str(tmp_path / "elsewhere" / "g.sqlite")
    config = {"app": {"database_path": absolute}}
    resolved = resolve_database_path(config, str(tmp_path / "config.yaml"))
    assert Path(resolved) == Path(absolute)


def test_resolve_database_path_independent_of_cwd(tmp_path, monkeypatch):
    config_path = str(tmp_path / "config.yaml")
    config = {"app": {"database_path": "data/g.sqlite"}}
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # launch from a foreign cwd
    resolved = resolve_database_path(config, config_path)
    assert Path(resolved) == tmp_path / "data" / "g.sqlite"
