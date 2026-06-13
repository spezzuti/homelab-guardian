import subprocess

from homelab_guardian.collectors import firewall_collector


def _cp(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout=stdout, stderr="")


def _runner(active: set[str] | None):
    """active=None simulates systemctl missing (raises)."""
    def runner(cmd, **kwargs):
        if active is None:
            raise FileNotFoundError("systemctl")
        service = cmd[-1]
        return _cp("active" if service in active else "inactive")
    return runner


def _reader(mapping: dict[str, str]):
    return lambda path: mapping.get(path)


def test_ufw_active_default_deny_is_ok():
    reader = _reader({
        "/etc/ufw/ufw.conf": "ENABLED=yes\n",
        "/etc/default/ufw": 'DEFAULT_INPUT_POLICY="DROP"\n',
    })
    checks = firewall_collector.collect({}, runner=_runner({"ufw"}), reader=reader)
    assert checks[0].status == "ok"
    assert checks[0].group == "Security"
    assert checks[0].evidence["ufw_default_input_policy"] == "DROP"


def test_ufw_active_but_default_allow_is_warning():
    reader = _reader({
        "/etc/ufw/ufw.conf": "ENABLED=yes\n",
        "/etc/default/ufw": 'DEFAULT_INPUT_POLICY="ACCEPT"\n',
    })
    checks = firewall_collector.collect({}, runner=_runner({"ufw"}), reader=reader)
    assert checks[0].status == "warning"
    assert "not deny" in checks[0].summary


def test_no_firewall_active_is_warning():
    checks = firewall_collector.collect({}, runner=_runner(set()), reader=_reader({}))
    assert checks[0].status == "warning"
    assert "No host firewall" in checks[0].summary


def test_nftables_active_is_ok():
    checks = firewall_collector.collect({}, runner=_runner({"nftables"}), reader=_reader({}))
    assert checks[0].status == "ok"
    assert "nftables" in checks[0].summary


def test_no_systemctl_is_unknown():
    checks = firewall_collector.collect({}, runner=_runner(None), reader=_reader({}))
    assert checks[0].status == "unknown"
