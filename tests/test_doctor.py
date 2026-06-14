from homelab_guardian.doctor import _check_backup_health_config


def _cfg(**backup_health):
    return {"collectors": {"backup_health": backup_health}}


def test_backup_health_preflight_skipped_when_disabled():
    assert _check_backup_health_config(_cfg(enabled=False)) is None


def test_backup_health_preflight_warns_when_enabled_but_empty():
    check = _check_backup_health_config(_cfg(enabled=True, repos=[]))
    assert check is not None
    assert check.id == "preflight_backup_health_repos"
    assert check.status == "warning"


def test_backup_health_preflight_ok_when_repos_present():
    check = _check_backup_health_config(_cfg(enabled=True, repos=[{"unit": "x.service", "tool": "systemd"}]))
    assert check.status == "ok"
    assert check.evidence["configured_repos"] == 1
