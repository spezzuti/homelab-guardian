import yaml

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
