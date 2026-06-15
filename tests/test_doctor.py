from homelab_guardian.doctor import _check_backup_health_config, _check_repair_config, _check_web_auth


class _Secrets:
    def __init__(self, **vals):
        self._v = vals

    def get(self, k):
        return self._v.get(k)


def test_web_auth_preflight_skipped_for_none_and_forward_auth():
    assert _check_web_auth({}, _Secrets()) is None
    assert _check_web_auth({"web": {"auth": {"mode": "forward_auth"}}}, _Secrets()) is None


def test_web_auth_preflight_oidc_public_client_skipped():
    assert _check_web_auth({"web": {"auth": {"mode": "oidc"}}}, _Secrets()) is None


def test_web_auth_preflight_critical_when_oidc_secret_missing():
    c = _check_web_auth({"web": {"auth": {"mode": "oidc", "client_secret_env": "GUARDIAN_OIDC_CLIENT_SECRET"}}}, _Secrets())
    assert c.status == "critical" and "refuse to start" in c.summary


def test_web_auth_preflight_ok_when_oidc_secret_present():
    c = _check_web_auth(
        {"web": {"auth": {"mode": "oidc", "client_secret_env": "GUARDIAN_OIDC_CLIENT_SECRET"}}},
        _Secrets(GUARDIAN_OIDC_CLIENT_SECRET="x"),
    )
    assert c.status == "ok"


def test_web_auth_preflight_basic_missing_password_is_critical():
    c = _check_web_auth({"web": {"auth": {"mode": "basic", "password_env": "GUARDIAN_WEB_PASSWORD"}}}, _Secrets())
    assert c.status == "critical"


def _cfg(**backup_health):
    return {"collectors": {"backup_health": backup_health}}


def _repair_cfg(playbooks, *, watched_units=None):
    return {
        "collectors": {"systemd": {"units": [{"unit": u} for u in (watched_units or [])]}},
        "repair": {"enabled": True, "playbooks": playbooks},
    }


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


# --- repair preflight ------------------------------------------------------

def test_repair_preflight_skipped_when_disabled():
    assert _check_repair_config({"repair": {"enabled": False}}) is None


def test_repair_preflight_warns_when_enabled_but_no_playbooks():
    c = _check_repair_config({"repair": {"enabled": True, "playbooks": {}}})
    assert c.status == "warning" and "no playbook" in c.summary


def test_repair_preflight_ok_when_allowed_unit_is_watched():
    c = _check_repair_config(_repair_cfg(
        {"restart_systemd_unit": {"enabled": True, "allowed_units": ["a.service"]}},
        watched_units=["a.service"]))
    assert c.status == "ok"
    assert c.evidence["enabled_playbooks"] == ["restart_systemd_unit"]


def test_repair_preflight_warns_when_allowed_unit_not_watched():
    c = _check_repair_config(_repair_cfg(
        {"restart_systemd_unit": {"enabled": True, "allowed_units": ["ghost.service"]}},
        watched_units=["other.service"]))
    assert c.status == "warning"
    assert any("does not watch" in w for w in c.evidence["warnings"])


def test_repair_preflight_warns_on_system_path_in_prune_dir():
    c = _check_repair_config(_repair_cfg(
        {"prune_dir": {"enabled": True, "allowed_paths": ["/etc"]}}))
    assert c.status == "warning"
    assert any("system directory" in w for w in c.evidence["warnings"])
    assert "prune_dir" in c.evidence["destructive_enabled"]
