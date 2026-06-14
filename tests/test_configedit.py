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
