import yaml

from homelab_guardian import wizard
from homelab_guardian.wizard import DiscoveredService, build_config, discover, render_config_yaml, subnet_hosts


def _svc(ip: str, port: int, label: str, scheme: str) -> DiscoveredService:
    return DiscoveredService(ip=ip, port=port, label=label, scheme=scheme)


def test_subnet_hosts_excludes_self():
    hosts = subnet_hosts("192.168.1.50")
    assert "192.168.1.1" in hosts
    assert "192.168.1.254" in hosts
    assert "192.168.1.50" not in hosts
    assert len(hosts) == 253


def test_discover_uses_port_checker_and_sorts():
    open_pairs = {("10.0.0.5", 8123), ("10.0.0.2", 8006)}
    services = [
        {"port": 8123, "label": "Home Assistant", "scheme": "http"},
        {"port": 8006, "label": "Proxmox VE", "scheme": "https"},
    ]
    found = discover(
        ["10.0.0.5", "10.0.0.2"],
        services=services,
        port_checker=lambda ip, port: (ip, port) in open_pairs,
        resolve_names=False,
    )
    assert [(s.ip, s.port) for s in found] == [("10.0.0.2", 8006), ("10.0.0.5", 8123)]


def test_discover_filters_google_cast_devices():
    # .9 is a Cast device: answers on 9000 and the Cast fingerprint port 8009.
    # .10 is a real Portainer host: answers on 9000 only.
    open_pairs = {("10.0.0.9", 9000), ("10.0.0.9", 8009), ("10.0.0.10", 9000)}
    services = [{"port": 9000, "label": "Portainer (or other web service)", "scheme": "http"}]
    found = discover(
        ["10.0.0.9", "10.0.0.10"],
        services=services,
        port_checker=lambda ip, port: (ip, port) in open_pairs,
        resolve_names=False,
    )
    assert [s.ip for s in found] == ["10.0.0.10"]


def test_build_config_http_and_tcp_checks():
    discovered = [
        _svc("192.168.1.10", 8006, "Proxmox VE", "https"),
        _svc("192.168.1.20", 53, "DNS server (Pi-hole/AdGuard/router)", "tcp"),
        _svc("192.168.1.30", 32400, "Plex", "http"),
    ]
    config = build_config(discovered)
    network = config["collectors"]["network"]
    assert len(network["http_checks"]) == 2
    assert len(network["tcp_checks"]) == 1
    proxmox = network["http_checks"][0]
    assert proxmox["url"] == "https://192.168.1.10:8006"
    assert proxmox["verify_tls"] is False
    plex = network["http_checks"][1]
    assert plex["url"] == "http://192.168.1.30:32400"
    assert "verify_tls" not in plex
    assert network["tcp_checks"][0]["port"] == 53


def test_build_config_enables_ha_from_discovery():
    discovered = [_svc("192.168.1.40", 8123, "Home Assistant", "http")]
    config = build_config(discovered, homeassistant={"token_env": "HOMEASSISTANT_TOKEN"})
    ha = config["collectors"]["homeassistant"]
    assert ha["enabled"] is True
    assert ha["url"] == "http://192.168.1.40:8123"


def test_build_config_optional_sections_absent_by_default():
    config = build_config([])
    assert "homeassistant" not in config["collectors"]
    assert "notifications" not in config
    assert "ai" not in config
    assert "secrets" not in config
    # Host-hardening collectors are opt-in (Linux-host only).
    for name in ("firewall", "exposed_services", "ssh", "updates", "backup_health"):
        assert name not in config["collectors"]
    # v0.3 actuator surface is all opt-in too.
    assert "mounts" not in config["collectors"]
    for name in ("mcp", "web", "repair"):
        assert name not in config


def test_build_config_mounts():
    config = build_config([], mounts=["/mnt/nas", "/mnt/media"])
    paths = [m["path"] for m in config["collectors"]["mounts"]["mounts"]]
    assert config["collectors"]["mounts"]["enabled"] is True and paths == ["/mnt/nas", "/mnt/media"]


def test_build_config_mcp_stdio_and_remote():
    local = build_config([], mcp={"allow_writes": False})
    assert local["mcp"] == {"allow_writes": False}  # no http block for same-host stdio
    remote = build_config([], mcp={"remote": True})
    assert remote["mcp"]["http"]["token_env"] == "GUARDIAN_MCP_TOKEN"
    assert remote["mcp"]["allow_writes"] is False


def test_build_config_web_auth_basic():
    config = build_config([], web_auth={"mode": "basic", "username": "admin",
                                        "password_env": "GUARDIAN_WEB_PASSWORD"})
    assert config["web"]["auth"]["mode"] == "basic"
    assert config["web"]["auth"]["password_env"] == "GUARDIAN_WEB_PASSWORD"


def test_build_config_repair_is_conservative():
    config = build_config([], repair={"unit": "marcus-backup.service"})
    pb = config["repair"]["playbooks"]["restart_systemd_unit"]
    assert config["repair"]["enabled"] is True
    assert pb["allowed_units"] == ["marcus-backup.service"]
    assert pb["auto_approve"] is False  # wizard never enables self-healing unattended
    # Only the one safe playbook — no destructive actions are wired by the wizard.
    assert list(config["repair"]["playbooks"]) == ["restart_systemd_unit"]


def test_build_config_repair_ignored_without_unit():
    assert "repair" not in build_config([], repair={})


def test_build_config_host_checks_enables_zero_config_collectors():
    config = build_config([], host_checks=True)
    for name in ("firewall", "exposed_services", "ssh", "updates"):
        assert config["collectors"][name]["enabled"] is True
    # backup_health needs a target, so it's not added without a unit.
    assert "backup_health" not in config["collectors"]


def test_build_config_backup_unit_adds_systemd_backup_health():
    config = build_config([], host_checks=True, backup_unit="restic-backup.service")
    repos = config["collectors"]["backup_health"]["repos"]
    assert repos[0]["tool"] == "systemd"
    assert repos[0]["unit"] == "restic-backup.service"


def test_build_config_with_all_sections():
    config = build_config(
        [],
        homeassistant={"url": "http://ha.local:8123"},
        telegram={"send_on": "problems"},
        ai={"base_url": "http://localhost:11434/v1", "model": "qwen3:14b"},
        secrets_provider="bitwarden",
    )
    assert config["collectors"]["homeassistant"]["url"] == "http://ha.local:8123"
    assert config["notifications"]["telegram"]["send_on"] == "problems"
    assert config["ai"]["model"] == "qwen3:14b"
    assert config["secrets"]["provider"] == "bitwarden"


def test_rendered_yaml_round_trips():
    config = build_config([_svc("192.168.1.10", 8123, "Home Assistant", "http")])
    assert yaml.safe_load(render_config_yaml(config)) == config


def test_run_init_wires_every_answer_into_the_config(tmp_path, monkeypatch):
    """Drive run_init end-to-end and assert the *written* config contains every
    section the user said yes to. build_config is pure and tested above; this
    guards the run_init -> build_config wiring, where answers were once
    collected and then silently dropped (mounts/mcp/web_auth/repair)."""
    monkeypatch.setattr(wizard.sys, "platform", "linux")

    answers = iter([
        "n",                     # Home Assistant?
        "n",                     # Telegram?
        "n",                     # AI briefing?
        "y",                     # Linux host checks?
        "n",                     # watch a backup unit?
        "y",                     # watch mounts?
        "/mnt/nas, /mnt/media",  # mount paths
        "y",                     # attach an agent over MCP?
        "n",                     # agent on a different machine?
        "y",                     # password-protect the dashboard?
        "",                      # username (accept default: admin)
        "y",                     # enable the guarded repair?
        "myapp.service",         # unit to allow restarting
        "n",                     # Bitwarden secrets?
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "hunter2")

    output = tmp_path / "config.yaml"
    assert wizard.run_init(str(output), discover_network=False) == 0

    config = yaml.safe_load(output.read_text(encoding="utf-8"))
    mounts = config["collectors"]["mounts"]
    assert mounts["enabled"] is True
    assert [m["path"] for m in mounts["mounts"]] == ["/mnt/nas", "/mnt/media"]
    assert config["mcp"]["allow_writes"] is False
    assert config["web"]["auth"]["mode"] == "basic"
    assert config["web"]["auth"]["username"] == "admin"
    assert config["web"]["auth"]["password_env"] == "GUARDIAN_WEB_PASSWORD"
    assert config["repair"]["enabled"] is True
    playbook = config["repair"]["playbooks"]["restart_systemd_unit"]
    assert playbook["allowed_units"] == ["myapp.service"]
    assert playbook["auto_approve"] is False
    assert config["collectors"]["firewall"]["enabled"] is True

    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "GUARDIAN_WEB_PASSWORD=hunter2" in env
