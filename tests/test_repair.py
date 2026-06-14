import subprocess

import pytest

from homelab_guardian import db, repair
from homelab_guardian.models import HealthCheck

UNIT = "marcus-backup.service"
CHECK_ID = "systemd_unit_marcus-backup_service"  # systemd_collector's id for that unit


def _failing_check():
    return HealthCheck(CHECK_ID, "Service: " + UNIT, "critical",
                       f"{UNIT} has failed (state: failed/failed).",
                       evidence={"unit": UNIT, "bus": "system", "active_state": "failed"},
                       recommended_action="Read the log.", group="Host")


def _config(tmp_path, **playbook):
    pb = {"enabled": True, "allowed_units": [UNIT], "max_attempts_per_hour": 3}
    pb.update(playbook)
    return {
        "app": {"database_path": str(tmp_path / "g.sqlite")},
        "collectors": {"systemd": {"enabled": True, "units": [{"unit": UNIT}]}},
        "repair": {"enabled": True, "playbooks": {"restart_systemd_unit": pb}},
    }


def _seed(tmp_path, check=None):
    """A db with a latest scan containing the failing check."""
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.save_scan(conn, {"app": "HG", "checks": [(check or _failing_check()).to_dict()]})
    return conn


def _CP(args, code=0, out="", err=""):
    return subprocess.CompletedProcess(args, code, out, err)


def _runner(restart_code=0, active="active"):
    """Fake systemctl: handles the restart argv and the verify re-scan."""
    def run(cmd, **kw):
        if "restart" in cmd:
            return _CP(cmd, restart_code)
        if "show" in cmd:
            return _CP(cmd, 0, f"ActiveState={active}\nSubState=running\nNRestarts=0\nUnitFileState=enabled\nExecMainStatus=0\n")
        if "list-units" in cmd:
            return _CP(cmd, 0, "[]")  # empty json → no failed units in the sweep
        return _CP(cmd, 0)
    return run


# --- gating + applicability ------------------------------------------------

def test_disabled_blocks_everything(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path)
    config["repair"]["enabled"] = False
    assert repair.applicable_actions(config, _failing_check()) == []
    with pytest.raises(repair.RepairError):
        repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")


def test_applies_only_to_failing_systemd_unit_checks(tmp_path):
    config = _config(tmp_path)
    assert repair.applicable_actions(config, _failing_check()) == ["restart_systemd_unit"]
    ok = HealthCheck(CHECK_ID, "x", "ok", "fine", evidence={"unit": UNIT, "bus": "system"})
    assert repair.applicable_actions(config, ok) == []  # not failing
    other = HealthCheck("disk_root", "Disk", "critical", "full", evidence={})
    assert repair.applicable_actions(config, other) == []  # wrong check type


def test_plan_rejects_unit_not_in_allowlist(tmp_path):
    config = _config(tmp_path, allowed_units=["something-else.service"])
    with pytest.raises(repair.RepairError, match="not in the allowlist"):
        repair.build_plan(config, _failing_check(), "restart_systemd_unit")


def test_plan_builds_argv_system_vs_user(tmp_path):
    config = _config(tmp_path)
    plan = repair.build_plan(config, _failing_check(), "restart_systemd_unit")
    assert plan["argv"] == ["sudo", "-n", "systemctl", "restart", UNIT]
    assert plan["needs_privilege"] is True

    user_check = HealthCheck("systemd_unit_x", "x", "critical", "down",
                             evidence={"unit": "hermes-gateway.service", "bus": "user"})
    cfg2 = _config(tmp_path, allowed_units=["hermes-gateway.service"])
    plan2 = repair.build_plan(cfg2, user_check, "restart_systemd_unit")
    assert plan2["argv"] == ["systemctl", "--user", "restart", "hermes-gateway.service"]
    assert plan2["needs_privilege"] is False


# --- propose / approve / deny ----------------------------------------------

def test_propose_creates_pending_proposal(tmp_path):
    conn = _seed(tmp_path)
    res = repair.propose(_config(tmp_path), conn, CHECK_ID, "restart_systemd_unit", proposed_by="agent")
    assert res["status"] == "proposed"
    assert db.get_repair_proposal(conn, res["proposal_id"])["status"] == "proposed"


def test_auto_approve_marks_approved(tmp_path):
    conn = _seed(tmp_path)
    res = repair.propose(_config(tmp_path, auto_approve=True), conn, CHECK_ID, "restart_systemd_unit")
    assert res["status"] == "approved"


def test_approve_then_double_approve_fails(tmp_path):
    conn = _seed(tmp_path)
    pid = repair.propose(_config(tmp_path), conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    assert repair.approve(conn, pid, "alice")["status"] == "approved"
    with pytest.raises(repair.RepairError):
        repair.approve(conn, pid, "alice")  # no longer pending


# --- execute ---------------------------------------------------------------

def test_execute_refuses_unapproved(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path)
    pid = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    with pytest.raises(repair.RepairError, match="not approved"):
        repair.execute(config, conn, pid, runner=_runner())


def test_execute_happy_path_runs_and_verifies(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path)
    pid = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    repair.approve(conn, pid, "alice")
    res = repair.execute(config, conn, pid, runner=_runner(active="active"))
    assert res["status"] == "executed"
    assert res["result"]["ok"] is True
    assert res["verify"]["verified"] is True and res["verify"]["status"] == "ok"
    assert db.get_repair_proposal(conn, pid)["status"] == "executed"


def test_execute_failed_restart_is_failed_no_verify(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path)
    pid = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    repair.approve(conn, pid, "alice")
    res = repair.execute(config, conn, pid, runner=_runner(restart_code=1))
    assert res["status"] == "failed"
    assert res["result"]["ok"] is False
    assert res["verify"]["verified"] is False  # verify skipped on a failed action


def test_execute_verify_still_failing_is_failed(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path)
    pid = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    repair.approve(conn, pid, "alice")
    res = repair.execute(config, conn, pid, runner=_runner(active="failed"))  # restart ok, still failed
    assert res["result"]["ok"] is True
    assert res["verify"]["verified"] is False and res["status"] == "failed"


def test_loop_guard_blocks_after_limit(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path, max_attempts_per_hour=1)
    pid1 = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    repair.approve(conn, pid1, "alice")
    repair.execute(config, conn, pid1, runner=_runner())  # 1st run, allowed
    pid2 = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    repair.approve(conn, pid2, "alice")
    with pytest.raises(repair.RepairError, match="Loop guard"):
        repair.execute(config, conn, pid2, runner=_runner())


# --- restart_container playbook --------------------------------------------

DCHECK_ID = "docker_container_abc123def456"
CONTAINER = "jellyfin"


def _container_check(status="exited"):
    sev = {"running": "ok", "exited": "warning", "created": "warning", "paused": "warning",
           "dead": "critical", "restarting": "critical"}.get(status, "unknown")
    return HealthCheck(DCHECK_ID, f"Docker container: {CONTAINER}", sev,
                       f"{CONTAINER}: status={status}.",
                       evidence={"id": "abc123def456", "name": CONTAINER, "status": status}, group="Applications")


def _dconfig(tmp_path, **playbook):
    pb = {"enabled": True, "allowed_containers": [CONTAINER], "max_attempts_per_hour": 3}
    pb.update(playbook)
    return {
        "app": {"database_path": str(tmp_path / "g.sqlite")},
        "collectors": {"docker": {"enabled": True}},
        "repair": {"enabled": True, "playbooks": {"restart_container": pb}},
    }


def _patch_docker_collect(monkeypatch, status):
    import homelab_guardian.collectors.docker_collector as dc
    monkeypatch.setattr(dc, "collect", lambda config, secrets=None: [_container_check(status)])


def test_container_applies_and_plan(tmp_path):
    config = _dconfig(tmp_path)
    assert repair.applicable_actions(config, _container_check()) == ["restart_container"]
    plan = repair.build_plan(config, _container_check(), "restart_container")
    assert plan["argv"] == ["docker", "restart", CONTAINER]
    assert plan["needs_privilege"] is False


def test_container_plan_use_sudo_and_allowlist(tmp_path):
    cfg = _dconfig(tmp_path, use_sudo=True)
    assert repair.build_plan(cfg, _container_check(), "restart_container")["argv"] == \
        ["sudo", "-n", "docker", "restart", CONTAINER]
    cfg2 = _dconfig(tmp_path, allowed_containers=["other"])
    with pytest.raises(repair.RepairError, match="not in the allowlist"):
        repair.build_plan(cfg2, _container_check(), "restart_container")


def test_container_execute_happy_path(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.save_scan(conn, {"app": "HG", "checks": [_container_check("exited").to_dict()]})
    config = _dconfig(tmp_path)
    pid = repair.propose(config, conn, DCHECK_ID, "restart_container")["proposal_id"]
    repair.approve(conn, pid, "alice")
    _patch_docker_collect(monkeypatch, "running")  # verify sees it healthy again
    res = repair.execute(config, conn, pid, runner=_runner())
    assert res["status"] == "executed" and res["verify"]["status"] == "ok"


def test_container_execute_still_failing(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.save_scan(conn, {"app": "HG", "checks": [_container_check("exited").to_dict()]})
    config = _dconfig(tmp_path)
    pid = repair.propose(config, conn, DCHECK_ID, "restart_container")["proposal_id"]
    repair.approve(conn, pid, "alice")
    _patch_docker_collect(monkeypatch, "exited")  # still down after restart
    res = repair.execute(config, conn, pid, runner=_runner())
    assert res["status"] == "failed" and res["verify"]["verified"] is False
