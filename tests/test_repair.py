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


# --- disk-reclaim family: preview / risk tiers / preconditions -------------

DISK_CHECK_ID = "disk__"  # disk collector slug for "/"


def _disk_check(status="critical"):
    return HealthCheck(DISK_CHECK_ID, "Disk space: /", status, "/ is 95.0% full (3.1 GiB free).",
                       evidence={"path": "/", "percent_used": 95.0}, group="Host")


def _rconfig(tmp_path, action, **pb):
    base = {"enabled": True, "max_attempts_per_hour": 2}
    base.update(pb)
    return {
        "app": {"database_path": str(tmp_path / "g.sqlite")},
        "collectors": {"disks": {"enabled": True, "paths": [{"path": "/"}]}},
        "repair": {"enabled": True, "playbooks": {action: base}},
    }


def _reclaim_runner(prune_code=0):
    """Fake for reclaim: read-only preview commands + the reclaim argv."""
    def run(cmd, **kw):
        if "df" in cmd:                       # docker system df preview
            return _CP(cmd, 0, "Images 2 1.1GB / Reclaimable 1.1GB")
        if "--disk-usage" in cmd:             # journalctl preview
            return _CP(cmd, 0, "Archived and active journals take up 740.0M")
        if cmd[:2] == ["du", "-sh"]:          # apt cache preview
            return _CP(cmd, 0, "182M\t/var/cache/apt/archives")
        if "prune" in cmd or "vacuum-size" in " ".join(cmd) or "clean" in cmd:
            return _CP(cmd, prune_code)       # the reclaim action itself
        return _CP(cmd, 0)
    return run


def test_reclaim_applies_to_failing_disk_only(tmp_path):
    config = _rconfig(tmp_path, "docker_prune")
    assert "docker_prune" in repair.applicable_actions(config, _disk_check("critical"))
    assert repair.applicable_actions(config, _disk_check("ok")) == []  # healthy disk → nothing


def test_docker_prune_plan_has_preview_and_destructive_risk(tmp_path):
    config = _rconfig(tmp_path, "docker_prune")
    plan = repair.build_plan(config, _disk_check(), "docker_prune", runner=_reclaim_runner())
    assert plan["argv"] == ["docker", "system", "prune", "-f"]  # never --volumes by default
    assert plan["risk"] == "destructive"
    assert "Reclaimable" in plan["preview"]["docker_system_df"]


def test_allow_volumes_adds_flag(tmp_path):
    config = _rconfig(tmp_path, "docker_prune", allow_volumes=True)
    plan = repair.build_plan(config, _disk_check(), "docker_prune", runner=_reclaim_runner())
    assert plan["argv"][-1] == "--volumes"


def test_journal_and_apt_plans(tmp_path):
    jc = _rconfig(tmp_path, "journal_vacuum", vacuum_size="100M")
    jp = repair.build_plan(jc, _disk_check(), "journal_vacuum", runner=_reclaim_runner())
    assert jp["argv"] == ["sudo", "-n", "journalctl", "--vacuum-size=100M"] and jp["risk"] == "moderate"
    assert "740.0M" in jp["preview"]["current_journal_usage"]
    ac = _rconfig(tmp_path, "apt_clean")
    ap = repair.build_plan(ac, _disk_check(), "apt_clean", runner=_reclaim_runner())
    assert ap["argv"] == ["sudo", "-n", "apt-get", "clean"] and ap["risk"] == "low"
    assert ap["preview"]["apt_cache_size"] == "182M"


def test_destructive_never_auto_approves(tmp_path):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.save_scan(conn, {"app": "HG", "checks": [_disk_check().to_dict()]})
    config = _rconfig(tmp_path, "docker_prune", auto_approve=True)  # opt-in ignored for destructive
    res = repair.propose(config, conn, DISK_CHECK_ID, "docker_prune")
    assert res["status"] == "proposed"  # NOT auto-approved despite auto_approve: true


def test_low_risk_can_auto_approve(tmp_path):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.save_scan(conn, {"app": "HG", "checks": [_disk_check().to_dict()]})
    config = _rconfig(tmp_path, "apt_clean", auto_approve=True)
    res = repair.propose(config, conn, DISK_CHECK_ID, "apt_clean")
    assert res["status"] == "approved"  # low risk + opt-in → auto-approved


def test_precondition_can_block(tmp_path):
    # Inject a synthetic precondition to prove the cross-collector interlock hook.
    monkey = repair.PLAYBOOKS["apt_clean"]
    saved = dict(monkey)
    monkey["preconditions"] = lambda config, pcfg, check, latest: "backup is stale" if any(
        c.id == "backup_health" and c.status != "ok" for c in latest) else None
    try:
        conn = db.connect(str(tmp_path / "g.sqlite"))
        db.save_scan(conn, {"app": "HG", "checks": [
            _disk_check().to_dict(),
            HealthCheck("backup_health", "Backup", "critical", "stale").to_dict(),
        ]})
        config = _rconfig(tmp_path, "apt_clean")
        with pytest.raises(repair.RepairError, match="backup is stale"):
            repair.propose(config, conn, DISK_CHECK_ID, "apt_clean")
    finally:
        repair.PLAYBOOKS["apt_clean"] = saved


def test_reclaim_execute_verifies_disk_recovered(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.save_scan(conn, {"app": "HG", "checks": [_disk_check("critical").to_dict()]})
    config = _rconfig(tmp_path, "docker_prune")
    pid = repair.propose(config, conn, DISK_CHECK_ID, "docker_prune")["proposal_id"]
    repair.approve(conn, pid, "alice")
    import homelab_guardian.collectors.disk_collector as dc
    monkeypatch.setattr(dc, "collect", lambda config, secrets=None: [_disk_check("ok")])  # freed space
    res = repair.execute(config, conn, pid, runner=_reclaim_runner())
    assert res["status"] == "executed" and res["verify"]["status"] == "ok"


# --- prune_dir: backup interlock + typed confirmation ----------------------

def _backup_check(status="ok"):
    return HealthCheck("backup_health_main", "Backup health", status, "snapshot fresh", group="Backups")


def _prune_runner():
    def run(cmd, **kw):
        if "-printf" in cmd:                  # preview: sizes of matching files
            return _CP(cmd, 0, "100\n200\n300\n")
        if "-delete" in cmd:                  # the prune action
            return _CP(cmd, 0)
        return _CP(cmd, 0)
    return run


def _prune_config(tmp_path, **pb):
    base = {"enabled": True, "allowed_paths": ["/srv/tmp"], "older_than_days": 14}
    base.update(pb)
    return {
        "app": {"database_path": str(tmp_path / "g.sqlite")},
        "collectors": {"disks": {"enabled": True}},
        "repair": {"enabled": True, "playbooks": {"prune_dir": base}},
    }


def test_prune_dir_plan_and_preview(tmp_path):
    config = _prune_config(tmp_path)
    plan = repair.build_plan(config, _disk_check(), "prune_dir", latest_checks=[_disk_check()], runner=_prune_runner())
    assert plan["argv"] == ["find", "/srv/tmp", "-type", "f", "-mtime", "+14", "-delete"]
    assert plan["risk"] == "destructive"
    assert plan["preview"]["files"] == 3 and plan["preview"]["bytes"] == 600


def test_prune_dir_no_allowed_paths_refuses(tmp_path):
    config = _prune_config(tmp_path, allowed_paths=[])
    with pytest.raises(repair.RepairError, match="no allowed_paths"):
        repair.build_plan(config, _disk_check(), "prune_dir", latest_checks=[_disk_check()])


def test_prune_dir_refuses_when_backup_not_ok(tmp_path):
    config = _prune_config(tmp_path)
    latest = [_disk_check(), _backup_check("critical")]
    with pytest.raises(repair.RepairError, match="backup safety net is down"):
        repair.build_plan(config, _disk_check(), "prune_dir", latest_checks=latest, runner=_prune_runner())


def test_prune_dir_allows_with_warning_when_no_backup(tmp_path):
    config = _prune_config(tmp_path)
    plan = repair.build_plan(config, _disk_check(), "prune_dir", latest_checks=[_disk_check()], runner=_prune_runner())
    assert "No backup_health signal" in plan["warning"]  # allowed, but warned


def test_prune_dir_require_fresh_backup_refuses_when_none(tmp_path):
    config = _prune_config(tmp_path, require_fresh_backup=True)
    with pytest.raises(repair.RepairError, match="requires a verified backup"):
        repair.build_plan(config, _disk_check(), "prune_dir", latest_checks=[_disk_check()])


def test_prune_dir_clean_when_backup_ok(tmp_path):
    config = _prune_config(tmp_path)
    plan = repair.build_plan(config, _disk_check(), "prune_dir",
                             latest_checks=[_disk_check(), _backup_check("ok")], runner=_prune_runner())
    assert "warning" not in plan  # healthy backup → no warning, allowed


# --- security-review hardening ---------------------------------------------

def test_execute_revalidates_allowlist_drift(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path)
    pid = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    repair.approve(conn, pid, "alice")
    config["repair"]["playbooks"]["restart_systemd_unit"]["allowed_units"] = []  # revoke after approval
    with pytest.raises(repair.RepairError, match="no longer valid"):
        repair.execute(config, conn, pid, runner=_runner())


def test_execute_revalidates_recovered_check(tmp_path):
    conn = _seed(tmp_path)
    config = _config(tmp_path)
    pid = repair.propose(config, conn, CHECK_ID, "restart_systemd_unit")["proposal_id"]
    repair.approve(conn, pid, "alice")
    recovered = HealthCheck(CHECK_ID, "Service", "ok", "active again",
                            evidence={"unit": UNIT, "bus": "system"})
    db.save_scan(conn, {"app": "HG", "checks": [recovered.to_dict()]})  # came back on its own
    with pytest.raises(repair.RepairError, match="no longer valid"):
        repair.execute(config, conn, pid, runner=_runner())


def test_prune_dir_refuses_stale_backup(tmp_path):
    config = _prune_config(tmp_path, require_fresh_backup_hours=24)
    fresh = HealthCheck("backup_health_main", "Backup", "ok", "fresh", evidence={"age_hours": 2}, group="Backups")
    stale = HealthCheck("backup_health_main", "Backup", "ok", "old", evidence={"age_hours": 100}, group="Backups")
    assert repair.build_plan(config, _disk_check(), "prune_dir",
                             latest_checks=[_disk_check(), fresh], runner=_prune_runner())["argv"][0] == "find"
    with pytest.raises(repair.RepairError, match="not verifiably fresh"):
        repair.build_plan(config, _disk_check(), "prune_dir",
                          latest_checks=[_disk_check(), stale], runner=_prune_runner())


def test_prune_dir_days_floored_to_one(tmp_path):
    config = _prune_config(tmp_path, older_than_days=0)  # would otherwise delete everything >24h
    plan = repair.build_plan(config, _disk_check(), "prune_dir", latest_checks=[_disk_check()], runner=_prune_runner())
    assert "+1" in plan["argv"]


def test_destructive_fails_closed_without_state(tmp_path):
    config = _prune_config(tmp_path)
    with pytest.raises(repair.RepairError, match="fail closed"):
        repair.build_plan(config, _disk_check(), "prune_dir", latest_checks=None)  # no state → refuse


def test_typed_confirmation_blocks_then_allows(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "g.sqlite"))
    db.save_scan(conn, {"app": "HG", "checks": [_disk_check().to_dict(), _backup_check("ok").to_dict()]})
    config = _prune_config(tmp_path)
    config["repair"]["require_typed_confirmation"] = True
    pid = repair.propose(config, conn, DISK_CHECK_ID, "prune_dir")["proposal_id"]
    repair.approve(conn, pid, "alice")
    with pytest.raises(repair.RepairError, match="typed confirmation"):
        repair.execute(config, conn, pid, runner=_prune_runner())  # no token
    import homelab_guardian.collectors.disk_collector as dc
    monkeypatch.setattr(dc, "collect", lambda config, secrets=None: [_disk_check("ok"), _backup_check("ok")])
    res = repair.execute(config, conn, pid, runner=_prune_runner(), confirmation=str(pid))
    assert res["status"] == "executed"
