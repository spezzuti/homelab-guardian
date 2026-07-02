from homelab_guardian.collectors import docker_collector as dc


def test_running_healthy_is_ok():
    assert dc._status_for_container("running", "healthy")[0] == "ok"
    assert dc._status_for_container("running", None)[0] == "ok"


def test_unhealthy_is_critical():
    assert dc._status_for_container("running", "unhealthy")[0] == "critical"


def test_restarting_is_critical():
    assert dc._status_for_container("restarting", None)[0] == "critical"


def test_clean_stop_is_warning():
    # Intentionally stopped (exit 0) or killed by a stop signal (137/143) stays a
    # warning — not every stopped container is a problem.
    assert dc._status_for_container("exited", None, 0)[0] == "warning"
    assert dc._status_for_container("exited", None, 137)[0] == "warning"
    assert dc._status_for_container("exited", None, 143)[0] == "warning"


def test_application_crash_exit_is_critical():
    # A non-zero application exit (1..127) is a crash, not a clean stop.
    status, action = dc._status_for_container("exited", None, 1)
    assert status == "critical"
    assert "code 1" in action


def test_missing_exit_code_stays_warning():
    assert dc._status_for_container("exited", None, None)[0] == "warning"
