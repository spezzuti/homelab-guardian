import json
import subprocess

from homelab_guardian.collectors import systemd_collector


def _completed(stdout: str = "[]", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["systemctl"], returncode=returncode, stdout=stdout, stderr=stderr)


def _units_json(*units: tuple[str, str, str]) -> str:
    return json.dumps(
        [{"unit": u, "load": "loaded", "active": active, "sub": sub, "description": u} for u, active, sub in units]
    )


def test_all_healthy_sweep():
    runner = lambda *a, **k: _completed(_units_json(("good.service", "active", "running")))
    checks = systemd_collector.collect({}, runner=runner)
    assert len(checks) == 1
    assert checks[0].id == "systemd_system_units"
    assert checks[0].status == "ok"
    assert checks[0].evidence["total_services"] == 1


def test_failed_unit_is_critical():
    runner = lambda *a, **k: _completed(
        _units_json(("good.service", "active", "running"), ("bad.service", "failed", "failed"))
    )
    checks = systemd_collector.collect({}, runner=runner)
    assert checks[0].status == "critical"
    assert checks[0].evidence["failed"][0]["unit"] == "bad.service"


def test_restart_loop_detected_without_failed_state():
    # the claudeclaw pathology: never "failed", forever activating/auto-restart
    runner = lambda *a, **k: _completed(_units_json(("loop.service", "activating", "auto-restart")))
    checks = systemd_collector.collect({}, runner=runner)
    assert checks[0].status == "critical"
    assert "restart loop" in checks[0].summary
    assert checks[0].evidence["restart_looping"][0]["unit"] == "loop.service"


def test_user_bus_included_when_configured():
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return _completed("[]")

    checks = systemd_collector.collect({"include_user": True}, runner=runner)
    assert [c.id for c in checks] == ["systemd_system_units", "systemd_user_units"]
    assert any("--user" in c for c in calls)


def test_missing_systemctl_is_unknown():
    def runner(command, **kwargs):
        raise FileNotFoundError("systemctl")

    checks = systemd_collector.collect({}, runner=runner)
    assert checks[0].status == "unknown"
    assert "not available" in checks[0].summary


def test_plain_text_fallback_for_old_systemd():
    plain = "● bad.service loaded failed failed Some bot\ngood.service loaded active running Fine service\n"
    responses = iter([_completed("", returncode=1, stderr="unknown option"), _completed(plain)])
    checks = systemd_collector.collect({}, runner=lambda *a, **k: next(responses))
    assert checks[0].status == "critical"
    assert checks[0].evidence["failed"][0]["unit"] == "bad.service"


def test_watched_unit_states():
    def show(stdout):
        return lambda *a, **k: _completed(stdout)

    base = {"units": [{"unit": "x.service"}]}

    sweep = _completed("[]")

    def runner_for(show_output):
        responses = iter([sweep, _completed(show_output)])
        return lambda *a, **k: next(responses)

    ok = systemd_collector.collect(base, runner=runner_for("ActiveState=active\nSubState=running\nNRestarts=0"))
    assert ok[1].status == "ok"

    failed = systemd_collector.collect(base, runner=runner_for("ActiveState=failed\nSubState=failed\nNRestarts=7"))
    assert failed[1].status == "critical"
    assert failed[1].evidence["restarts"] == "7"

    looping = systemd_collector.collect(base, runner=runner_for("ActiveState=activating\nSubState=auto-restart"))
    assert looping[1].status == "critical"
    assert "restart loop" in looping[1].summary

    stopped = systemd_collector.collect(base, runner=runner_for("ActiveState=inactive\nSubState=dead"))
    assert stopped[1].status == "critical"

    optional = {"units": [{"unit": "x.service", "required": False}]}
    stopped_optional = systemd_collector.collect(optional, runner=runner_for("ActiveState=inactive\nSubState=dead"))
    assert stopped_optional[1].status == "warning"
