import subprocess

from homelab_guardian.collectors import ssh_collector

MAIN = "Include /etc/ssh/sshd_config.d/*.conf\nPasswordAuthentication yes\nX11Forwarding yes\n"


def _cp(stdout: str = "inactive") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["systemctl"], returncode=0, stdout=stdout, stderr="")


def _runner(fail2ban: bool = False):
    return lambda cmd, **kwargs: _cp("active" if fail2ban else "inactive")


def _collect(main: str | None, dropins: dict[str, str], fail2ban: bool = False):
    files = {"/etc/ssh/sshd_config": main, **dropins}
    return ssh_collector.collect(
        {},
        runner=_runner(fail2ban),
        reader=lambda path: files.get(path),
        dropin_lister=lambda: sorted(dropins),
    )


def test_dropin_first_match_wins_over_main():
    # sshd reads the Include (drop-ins) at the top, first match wins: a
    # 00-hardening drop-in beats both 50-cloud-init and the main file.
    settings = ssh_collector.effective_sshd_settings(
        MAIN,
        [
            ("/etc/ssh/sshd_config.d/00-hardening.conf", "PasswordAuthentication no\n"),
            ("/etc/ssh/sshd_config.d/50-cloud-init.conf", "PasswordAuthentication yes\n"),
        ],
    )
    assert settings["passwordauthentication"] == "no"


def test_hardened_is_ok():
    checks = _collect(
        MAIN,
        {"/etc/ssh/sshd_config.d/00-hardening.conf": "PasswordAuthentication no\nPermitRootLogin no\n"},
    )
    assert checks[0].status == "ok"
    assert checks[0].group == "Security"


def test_password_auth_on_is_warning():
    checks = _collect(MAIN, {"/etc/ssh/sshd_config.d/50-cloud-init.conf": "PasswordAuthentication yes\n"})
    assert checks[0].status == "warning"
    assert "password authentication" in checks[0].summary
    assert checks[0].evidence["fail2ban_active"] is False


def test_root_login_yes_is_warning():
    checks = _collect("PermitRootLogin yes\nPasswordAuthentication no\n", {})
    assert checks[0].status == "warning"
    assert "root login" in checks[0].summary


def test_missing_config_is_unknown():
    checks = _collect(None, {})
    assert checks[0].status == "unknown"


def test_match_block_password_auth_is_not_treated_as_global():
    # A conditional `Match` that re-enables password auth for one source must NOT
    # be read as a global setting — resolution stops at the first Match line.
    settings = ssh_collector.effective_sshd_settings(
        "PasswordAuthentication no\n"
        "PermitRootLogin no\n"
        "Match Address 10.0.0.0/8\n"
        "    PasswordAuthentication yes\n",
        [],
    )
    assert settings["passwordauthentication"] == "no"


def test_global_directive_after_match_is_ignored():
    # Once inside a Match block there is no return to global scope, so a value
    # that appears only after a Match falls back to sshd's default.
    settings = ssh_collector.effective_sshd_settings(
        "Match User backup\n"
        "    PermitRootLogin yes\n",
        [],
    )
    assert settings["permitrootlogin"] == "prohibit-password"  # sshd default
