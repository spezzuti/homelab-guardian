import yaml

from homelab_guardian.configedit import apply_collector_toggles, write_config

BASE = """app:
  name: HG
collectors:
  docker:
    enabled: false
    socket_url: unix://x
  network:
    # keep me
    enabled: true
    dns_checks: []
  systemd:
    units: []
secrets:
  provider: env
"""


def _collectors(text):
    return (yaml.safe_load(text).get("collectors") or {})


def test_toggle_existing_enabled_value():
    out = apply_collector_toggles(BASE, {"docker": True})
    c = _collectors(out)
    assert c["docker"]["enabled"] is True
    # siblings untouched
    assert c["network"]["enabled"] is True
    assert c["docker"]["socket_url"] == "unix://x"
    # comment and other sections preserved
    assert "# keep me" in out
    assert yaml.safe_load(out)["secrets"]["provider"] == "env"


def test_toggle_true_to_false():
    out = apply_collector_toggles(BASE, {"network": False})
    assert _collectors(out)["network"]["enabled"] is False
    assert "# keep me" in out


def test_insert_enabled_when_absent():
    # systemd has no `enabled:` line in BASE
    out = apply_collector_toggles(BASE, {"systemd": True})
    c = _collectors(out)
    assert c["systemd"]["enabled"] is True
    assert c["systemd"]["units"] == []  # existing sub-key preserved


def test_insert_new_collector_block():
    out = apply_collector_toggles(BASE, {"firewall": True})
    assert _collectors(out)["firewall"]["enabled"] is True
    # didn't disturb existing ones
    assert _collectors(out)["docker"]["enabled"] is False


def test_multiple_toggles_at_once():
    out = apply_collector_toggles(BASE, {"docker": True, "network": False, "systemd": True})
    c = _collectors(out)
    assert c["docker"]["enabled"] is True
    assert c["network"]["enabled"] is False
    assert c["systemd"]["enabled"] is True


def test_no_collectors_section_appends_one():
    out = apply_collector_toggles("app:\n  name: HG\n", {"docker": True})
    assert _collectors(out)["docker"]["enabled"] is True
    assert yaml.safe_load(out)["app"]["name"] == "HG"


def test_write_config_is_atomic_with_backup(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(BASE, encoding="utf-8")
    new = apply_collector_toggles(BASE, {"docker": True})
    write_config(str(path), new)
    assert _collectors(path.read_text(encoding="utf-8"))["docker"]["enabled"] is True
    # prior version preserved as .bak
    assert _collectors((tmp_path / "config.yaml.bak").read_text(encoding="utf-8"))["docker"]["enabled"] is False


# --- v2: whitelisted numeric edits (thresholds & timing) ---

from homelab_guardian.configedit import (  # noqa: E402
    apply_setting_edits,
    editable_settings,
    parse_setting_edits,
    set_scalar,
)

V2 = """app:
  name: HG
  retention_days: 60  # keep two months
collectors:
  disks:
    enabled: true
    paths:
    - id: disk_root
      path: /
      warn_percent: 85
    - path: /srv
  network:
    enabled: true
    tls_checks:
      - id: tls_portal
        host: portal.example.com
        warn_days: 14
notifications:
  telegram:
    confirm_scans: 2
"""


def test_set_scalar_replaces_and_keeps_trailing_comment():
    out = set_scalar(V2, ["app", "retention_days"], 90)
    assert yaml.safe_load(out)["app"]["retention_days"] == 90
    assert "# keep two months" in out


def test_set_scalar_in_compact_list_item():
    # dash at the same indent as its key (compact style)
    out = set_scalar(V2, ["collectors", "disks", ("paths", "id", "disk_root"), "warn_percent"], 90)
    parsed = yaml.safe_load(out)
    assert parsed["collectors"]["disks"]["paths"][0]["warn_percent"] == 90
    assert "path" in parsed["collectors"]["disks"]["paths"][1]  # sibling untouched


def test_set_scalar_in_indented_list_item():
    # dash indented deeper than its key
    out = set_scalar(V2, ["collectors", "network", ("tls_checks", "id", "tls_portal"), "warn_days"], 30)
    assert yaml.safe_load(out)["collectors"]["network"]["tls_checks"][0]["warn_days"] == 30


def test_set_scalar_inserts_missing_key_into_item():
    out = set_scalar(V2, ["collectors", "disks", ("paths", "path", "/srv"), "critical_percent"], 97)
    parsed = yaml.safe_load(out)
    assert parsed["collectors"]["disks"]["paths"][1]["critical_percent"] == 97


def test_set_scalar_refuses_unknown_item():
    import pytest
    with pytest.raises(ValueError):
        set_scalar(V2, ["collectors", "disks", ("paths", "id", "nope"), "warn_percent"], 90)


def test_editable_settings_registry_covers_config():
    settings = {s["token"]: s for s in editable_settings(yaml.safe_load(V2))}
    assert "app.retention_days" in settings
    assert settings["app.retention_days"]["value"] == 60
    assert "collectors.disks.paths[id=disk_root].warn_percent" in settings
    # items with no id fall back to their path
    assert "collectors.disks.paths[path=/srv].warn_percent" in settings
    assert "collectors.network.tls_checks[id=tls_portal].critical_days" in settings
    assert settings["notifications.telegram.confirm_scans"]["kind"] == "int"


def test_editable_settings_skips_disabled_collectors():
    cfg = yaml.safe_load(V2)
    cfg["collectors"]["disks"]["enabled"] = False
    tokens = [s["token"] for s in editable_settings(cfg)]
    assert not any(t.startswith("collectors.disks") for t in tokens)


def test_parse_setting_edits_validates_and_diffs():
    import pytest
    cfg = yaml.safe_load(V2)
    edits = parse_setting_edits(cfg, {"app.retention_days": "90",
                                      "notifications.telegram.confirm_scans": "2"})
    # only the changed value comes back
    assert edits == [(["app", "retention_days"], 90.0)]
    with pytest.raises(ValueError):
        parse_setting_edits(cfg, {"app.retention_days": "lots"})
    with pytest.raises(ValueError):
        parse_setting_edits(cfg, {"collectors.disks.paths[id=disk_root].warn_percent": "5"})
    # unknown tokens are ignored, not applied
    assert parse_setting_edits(cfg, {"repair.enabled": "1"}) == []


def test_apply_setting_edits_end_to_end():
    cfg = yaml.safe_load(V2)
    edits = parse_setting_edits(cfg, {
        "app.retention_days": "90",
        "collectors.disks.paths[id=disk_root].warn_percent": "88",
    })
    out = apply_setting_edits(V2, edits)
    parsed = yaml.safe_load(out)
    assert parsed["app"]["retention_days"] == 90
    assert parsed["collectors"]["disks"]["paths"][0]["warn_percent"] == 88
    assert "# keep two months" in out
