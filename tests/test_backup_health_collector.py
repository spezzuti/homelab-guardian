import subprocess

from homelab_guardian.collectors import backup_health_collector


def _cp(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["restic"], returncode=returncode, stdout=stdout, stderr=stderr)


def _restic_cfg(**over):
    repo = {"id": "r", "name": "R", "tool": "restic", "repository": "/repo", "password_file": "/pw"}
    repo.update(over)
    return {"repos": [repo]}


def _systemd_cfg(**over):
    repo = {"id": "s", "name": "S", "tool": "systemd", "unit": "backup.service"}
    repo.update(over)
    return {"repos": [repo]}


def test_no_repos_reports_nothing():
    # Enabled-but-unconfigured is calm by default — the guidance lives in
    # `guardian doctor` (preflight), not as noise on the live dashboard.
    assert backup_health_collector.collect({}) == []


def test_restic_fresh_snapshot_is_ok():
    runner = lambda cmd, **k: _cp('[{"time":"2999-01-01T00:00:00+00:00","short_id":"a"}]')
    checks = backup_health_collector.collect(_restic_cfg(), runner=runner)
    assert checks[0].status == "ok"
    assert checks[0].evidence["snapshots"] == 1


def test_restic_stale_snapshot_is_critical():
    runner = lambda cmd, **k: _cp('[{"time":"2000-01-01T00:00:00+00:00","short_id":"a"}]')
    checks = backup_health_collector.collect(_restic_cfg(max_age_hours=36), runner=runner)
    assert checks[0].status == "critical"


def test_restic_no_snapshots_is_critical():
    checks = backup_health_collector.collect(_restic_cfg(), runner=lambda cmd, **k: _cp("[]"))
    assert checks[0].status == "critical"
    assert "no snapshots" in checks[0].summary.lower()


def test_restic_unreadable_repo_is_critical():
    runner = lambda cmd, **k: _cp(returncode=1, stderr="wrong password")
    checks = backup_health_collector.collect(_restic_cfg(), runner=runner)
    assert checks[0].status == "critical"


def test_restic_missing_binary_is_unknown():
    def runner(cmd, **k):
        raise FileNotFoundError("restic")

    checks = backup_health_collector.collect(_restic_cfg(), runner=runner)
    assert checks[0].status == "unknown"


def test_systemd_show_requests_utc_timestamps():
    # Without --timestamp=utc systemd emits local time, which we'd mis-stamp as
    # UTC and get age_hours wrong by the local offset. Lock the flag in.
    seen = {}

    def runner(cmd, **k):
        seen["cmd"] = cmd
        return _cp("ActiveState=active\nResult=success\n")

    backup_health_collector.collect(_systemd_cfg(), runner=runner)
    assert "--timestamp=utc" in seen["cmd"]


def test_systemd_running_is_ok():
    runner = lambda cmd, **k: _cp("ActiveState=active\nResult=success\n")
    checks = backup_health_collector.collect(_systemd_cfg(), runner=runner)
    assert checks[0].status == "ok"


def test_systemd_failed_last_run_is_critical():
    runner = lambda cmd, **k: _cp("Result=failed\nActiveState=inactive\nExecMainStatus=1\n")
    checks = backup_health_collector.collect(_systemd_cfg(), runner=runner)
    assert checks[0].status == "critical"


def test_systemd_stale_success_is_critical():
    out = "Result=success\nActiveState=inactive\nExecMainStatus=0\nExecMainExitTimestamp=Sat 2000-01-01 00:00:00 UTC\n"
    checks = backup_health_collector.collect(_systemd_cfg(max_age_hours=36), runner=lambda cmd, **k: _cp(out))
    assert checks[0].status == "critical"


def test_systemd_no_run_record_is_warning():
    out = "Result=\nActiveState=inactive\nExecMainExitTimestamp=n/a\n"
    checks = backup_health_collector.collect(_systemd_cfg(), runner=lambda cmd, **k: _cp(out))
    assert checks[0].status == "warning"
    assert "no record of a completed run" in checks[0].summary


def test_systemd_success_but_timestamp_cleared_by_reboot_is_warning():
    # success result, but ExecMainExitTimestamp empty (oneshot reset on reboot)
    out = "Result=success\nActiveState=inactive\nExecMainStatus=0\nExecMainExitTimestamp=\n"
    checks = backup_health_collector.collect(_systemd_cfg(), runner=lambda cmd, **k: _cp(out))
    assert checks[0].status == "warning"
    assert "recency can't be confirmed" in checks[0].summary
