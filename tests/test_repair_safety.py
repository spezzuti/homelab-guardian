"""Invariant guards for the repair feature's core safety guarantees."""
from pathlib import Path

from homelab_guardian import repair
from homelab_guardian.models import HealthCheck


def test_no_shell_true_anywhere_in_package():
    # The bedrock guarantee: Guardian never runs a shell. If this ever fails, an
    # injection surface has been introduced.
    pkg = Path(repair.__file__).resolve().parent
    offenders = [str(f) for f in pkg.rglob("*.py") if "shell=True" in f.read_text(encoding="utf-8")]
    assert offenders == [], f"shell=True found in: {offenders}"


def test_every_playbook_builds_list_argv():
    # Every playbook must resolve to a list-of-strings argv (never a shell
    # string), so an attacker-controlled name/path cannot inject a command.
    cases = {
        "restart_systemd_unit": (
            HealthCheck("systemd_unit_x", "x", "critical", "down", evidence={"unit": "x.service", "bus": "system"}),
            {"enabled": True, "allowed_units": ["x.service"]}),
        "restart_container": (
            HealthCheck("docker_container_abc", "x", "warning", "exited", evidence={"name": "c1", "status": "exited"}),
            {"enabled": True, "allowed_containers": ["c1"]}),
        "remount": (
            HealthCheck("mount_mnt_nas", "x", "critical", "not mounted", evidence={"path": "/mnt/nas"}),
            {"enabled": True, "allowed_mounts": ["/mnt/nas"]}),
        "docker_prune": (
            HealthCheck("disk_root", "/", "critical", "full", evidence={"path": "/"}), {"enabled": True}),
        "journal_vacuum": (
            HealthCheck("disk_root", "/", "critical", "full", evidence={"path": "/"}), {"enabled": True}),
        "apt_clean": (
            HealthCheck("disk_root", "/", "critical", "full", evidence={"path": "/"}), {"enabled": True}),
        "prune_dir": (
            HealthCheck("disk_root", "/", "critical", "full", evidence={"path": "/"}),
            {"enabled": True, "allowed_paths": ["/tmp/x"]}),
    }
    assert set(cases) == set(repair.PLAYBOOKS), "a playbook is missing an argv invariant case"
    for action, (check, pb) in cases.items():
        config = {"repair": {"enabled": True, "playbooks": {action: pb}}, "collectors": {}}
        plan = repair.build_plan(config, check, action, latest_checks=[check], with_preview=False)
        argv = plan["argv"]
        assert isinstance(argv, list) and argv and all(isinstance(a, str) for a in argv), action
        assert "shell" not in plan  # no shell string ever stored on the plan


def test_evidence_derived_name_cannot_inject():
    # A hostile container name is passed as a single argv element, not a shell
    # string — it can only ever be one (failing) argument to `docker restart`.
    evil = '; rm -rf / #'
    check = HealthCheck("docker_container_x", "x", "warning", "exited", evidence={"name": evil, "status": "exited"})
    config = {"repair": {"enabled": True, "playbooks": {"restart_container": {"enabled": True, "allowed_containers": [evil]}}}}
    plan = repair.build_plan(config, check, "restart_container", latest_checks=[check], with_preview=False)
    assert plan["argv"] == ["docker", "restart", evil]  # one literal arg, no shell parsing
