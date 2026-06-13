import subprocess

from homelab_guardian.collectors import updates_collector


def _cp(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["apt-check"], returncode=0, stdout="", stderr=stderr)


def _runner(stderr: str):
    return lambda cmd, **kwargs: _cp(stderr)


def _reader(files: dict[str, str]):
    return lambda path: files.get(path)


def test_security_updates_are_warning():
    checks = updates_collector.collect({}, runner=_runner("56;3"), reader=_reader({}))
    assert checks[0].status == "warning"
    assert checks[0].evidence["security_updates"] == 3
    assert checks[0].group == "Updates"


def test_reboot_required_is_warning():
    files = {"/var/run/reboot-required": "*** reboot ***", "/var/run/reboot-required.pkgs": "linux-image\n"}
    checks = updates_collector.collect({}, runner=_runner("10;0"), reader=_reader(files))
    assert checks[0].status == "warning"
    assert checks[0].evidence["reboot_required"] is True
    assert "linux-image" in checks[0].evidence["reboot_required_packages"]


def test_non_security_updates_are_ok():
    checks = updates_collector.collect({}, runner=_runner("5;0"), reader=_reader({}))
    assert checks[0].status == "ok"
    assert checks[0].evidence["pending_updates"] == 5


def test_fully_up_to_date_is_ok():
    checks = updates_collector.collect({}, runner=_runner("0;0"), reader=_reader({}))
    assert checks[0].status == "ok"
    assert "up to date" in checks[0].summary


def test_missing_apt_check_with_reboot_flag_is_warning():
    def runner(cmd, **kwargs):
        raise FileNotFoundError("apt-check")

    files = {"/var/run/reboot-required": "x"}
    checks = updates_collector.collect({}, runner=runner, reader=_reader(files))
    assert checks[0].status == "warning"
