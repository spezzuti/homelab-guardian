import subprocess

from homelab_guardian.collectors import exposed_services_collector


def _cp(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ss"], returncode=0, stdout=stdout, stderr="")


def _runner(stdout: str):
    return lambda cmd, **kwargs: _cp(stdout)


SMB_EXPOSED = (
    "tcp   LISTEN 0 4096 0.0.0.0:22        0.0.0.0:*\n"
    "tcp   LISTEN 0 4096 0.0.0.0:445       0.0.0.0:*\n"
    "tcp   LISTEN 0 128  127.0.0.53%lo:53  0.0.0.0:*\n"
    "tcp   LISTEN 0 4096 [::]:5901         [::]:*\n"
)

ONLY_SAFE = (
    "tcp   LISTEN 0 4096 0.0.0.0:8674      0.0.0.0:*\n"
    "tcp   LISTEN 0 128  127.0.0.1:631     0.0.0.0:*\n"
)


def test_sensitive_exposed_is_warning():
    checks = exposed_services_collector.collect({}, runner=_runner(SMB_EXPOSED))
    assert checks[0].status == "warning"
    ports = {e["port"] for e in checks[0].evidence["sensitive_exposed"]}
    assert 445 in ports and 5901 in ports
    assert checks[0].group == "Security"


def test_loopback_is_not_counted_as_exposed():
    checks = exposed_services_collector.collect({}, runner=_runner(SMB_EXPOSED))
    exposed_ports = {e["port"] for e in checks[0].evidence["exposed"]}
    assert 53 not in exposed_ports  # 127.0.0.53%lo is loopback


def test_only_safe_services_is_ok():
    checks = exposed_services_collector.collect({}, runner=_runner(ONLY_SAFE))
    assert checks[0].status == "ok"
    assert checks[0].evidence["exposed_count"] == 1  # 8674; 631 is loopback


def test_allow_ports_silences_a_known_exposure():
    checks = exposed_services_collector.collect({"allow_ports": [445, 5901]}, runner=_runner(SMB_EXPOSED))
    assert checks[0].status == "ok"


def test_missing_ss_is_unknown():
    def runner(cmd, **kwargs):
        raise FileNotFoundError("ss")

    checks = exposed_services_collector.collect({}, runner=runner)
    assert checks[0].status == "unknown"
