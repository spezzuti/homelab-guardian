from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from homelab_guardian.config import load_config
from homelab_guardian.models import HealthCheck
from homelab_guardian.reports.markdown_report import render
from homelab_guardian.secrets import SecretStore, build_store


def _socket_path(socket_url: str) -> Path | None:
    if socket_url.startswith("unix://"):
        raw_path = socket_url.removeprefix("unix://")
        if not raw_path.startswith("/"):
            raw_path = f"/{raw_path}"
        return Path(raw_path)
    return None


def _directory_writable(path: Path) -> tuple[bool, str | None]:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".guardian-preflight-", dir=path, delete=True):
            pass
        return True, None
    except Exception as exc:  # pragma: no cover - depends on host permissions
        return False, str(exc)


def _check_python() -> HealthCheck:
    version = sys.version_info
    ok = version >= (3, 10)
    version_text = f"{version.major}.{version.minor}.{version.micro}"
    return HealthCheck(
        "preflight_python_version",
        "Python version",
        "ok" if ok else "critical",
        f"Python {version_text} is running.",
        {"version": version_text, "minimum_supported": "3.10"},
        "No action required." if ok else "Use Python 3.10 or newer.",
    )


def _check_config(config_path: str | Path) -> tuple[dict[str, Any] | None, HealthCheck]:
    try:
        config = load_config(config_path)
        return config, HealthCheck(
            "preflight_config_loads",
            "Config file loads",
            "ok",
            f"Config file loaded: {config_path}",
            {"config_path": str(config_path)},
            "No action required.",
        )
    except Exception as exc:
        return None, HealthCheck(
            "preflight_config_loads",
            "Config file loads",
            "critical",
            f"Config file could not be loaded: {config_path}",
            {"config_path": str(config_path), "error": str(exc)},
            "Fix the config path or YAML syntax before running a scan.",
        )


def _check_output_paths(config: dict[str, Any]) -> list[HealthCheck]:
    app_config = config.get("app", {})
    report_dir = Path(app_config.get("report_path", "reports/latest.md")).parent
    data_dir = Path(app_config.get("database_path", "data/guardian.sqlite")).parent
    checks: list[HealthCheck] = []

    for check_id, name, path in [
        ("preflight_reports_writable", "Reports directory writable", report_dir),
        ("preflight_data_writable", "Data directory writable", data_dir),
    ]:
        writable, error = _directory_writable(path)
        checks.append(
            HealthCheck(
                check_id,
                name,
                "ok" if writable else "critical",
                f"{path} is writable." if writable else f"{path} is not writable.",
                {"path": str(path), "error": error},
                "No action required." if writable else "Fix directory permissions or choose another local output path.",
            )
        )
    return checks


def _check_docker(config: dict[str, Any]) -> HealthCheck | None:
    docker_config = config.get("collectors", {}).get("docker", {})
    if not docker_config.get("enabled", False):
        return None
    socket_url = os.environ.get("DOCKER_HOST") or docker_config.get("socket_url") or "unix://var/run/docker.sock"
    socket_path = _socket_path(socket_url)
    if socket_path and not socket_path.exists():
        return HealthCheck(
            "preflight_docker_socket",
            "Docker socket",
            "warning",
            "Docker collector is enabled, but /var/run/docker.sock was not found. Run Guardian on a Docker host or mount the Docker socket into the container.",
            {"socket_url": socket_url, "socket_path": str(socket_path), "exists": False},
            "Run Guardian on a Docker host, mount /var/run/docker.sock intentionally, or disable the Docker collector.",
        )
    if socket_path and not os.access(socket_path, os.R_OK | os.W_OK):
        return HealthCheck(
            "preflight_docker_socket",
            "Docker socket",
            "warning",
            f"Docker socket exists but is not accessible: {socket_path}",
            {"socket_url": socket_url, "socket_path": str(socket_path), "exists": True},
            "Check Docker socket permissions or run Guardian where Docker inspection is allowed.",
        )
    if socket_path is None:
        return HealthCheck(
            "preflight_docker_socket",
            "Docker endpoint",
            "ok",
            "Docker collector is enabled and a non-unix Docker endpoint is configured.",
            {"socket_url": socket_url, "socket_path": None, "source": "DOCKER_HOST" if os.environ.get("DOCKER_HOST") else "config"},
            "No action required if this endpoint is reachable from the running container.",
        )
    return HealthCheck(
        "preflight_docker_socket",
        "Docker socket",
        "ok",
        "Docker collector is enabled and the configured socket path exists.",
        {"socket_url": socket_url, "socket_path": str(socket_path)},
        "No action required.",
    )


def _check_secrets(config: dict[str, Any]) -> HealthCheck | None:
    secrets_config = config.get("secrets", {}) or {}
    provider = str(secrets_config.get("provider", "env")).lower()
    if provider == "env":
        return None
    store = build_store(secrets_config)
    count = store.preload()
    evidence = {**store.describe(), "secrets_available": count}
    if store.degraded:
        return HealthCheck(
            "preflight_secrets_provider",
            "Secrets provider",
            "warning",
            f"Secrets provider '{provider}' is configured but not working; Guardian will use environment variables only.",
            evidence,
            "Check the access token environment variable, the bws CLI installation, and machine-account permissions.",
        )
    return HealthCheck(
        "preflight_secrets_provider",
        "Secrets provider",
        "ok",
        f"Secrets provider '{provider}' is working: {count} secret(s) readable.",
        evidence,
        "No action required.",
    )


def _check_homeassistant(config: dict[str, Any], secrets: SecretStore) -> list[HealthCheck]:
    ha_config = config.get("collectors", {}).get("homeassistant", {})
    if not ha_config.get("enabled", False):
        return []
    checks: list[HealthCheck] = []
    url = ha_config.get("url")
    token_env = ha_config.get("token_env") or "HOMEASSISTANT_TOKEN"
    token_present = bool(secrets.get(token_env))
    checks.append(
        HealthCheck(
            "preflight_homeassistant_url",
            "Home Assistant URL",
            "ok" if url else "warning",
            "Home Assistant URL is configured." if url else "Home Assistant collector is enabled, but no URL is configured.",
            {"url": url},
            "No action required." if url else "Set collectors.homeassistant.url or disable the collector.",
        )
    )
    checks.append(
        HealthCheck(
            "preflight_homeassistant_token",
            "Home Assistant token",
            "ok" if token_present else "warning",
            f"Home Assistant token is {'available' if token_present else 'not available'} under the name: {token_env}",
            {"token_env": token_env, "token_present": token_present},
            "No action required." if token_present else "Create a long-lived token and expose it under that name through the environment or the configured secrets provider.",
        )
    )
    return checks


def _check_backup_config(config: dict[str, Any]) -> HealthCheck | None:
    backup_config = config.get("collectors", {}).get("backups", {})
    if not backup_config.get("enabled", False):
        return None
    paths = backup_config.get("paths", []) or []
    return HealthCheck(
        "preflight_backup_paths",
        "Backup paths configured",
        "ok" if paths else "warning",
        f"{len(paths)} backup path(s) configured." if paths else "Backup checks are enabled, but no backup paths are configured yet.",
        {"configured_paths": len(paths), "configuration_complete": bool(paths)},
        "No action required." if paths else "This is configuration incomplete, not a detected backup failure. Add backup paths, or disable backup checks until ready.",
    )


def _check_network_config(config: dict[str, Any]) -> HealthCheck | None:
    network_config = config.get("collectors", {}).get("network", {})
    if not network_config.get("enabled", False):
        return None
    dns_count = len(network_config.get("dns_checks", []) or [])
    tcp_count = len(network_config.get("tcp_checks", []) or [])
    http_count = len(network_config.get("http_checks", []) or [])
    total = dns_count + tcp_count + http_count
    return HealthCheck(
        "preflight_network_checks",
        "Network checks configured",
        "ok" if total else "warning",
        f"{total} network check(s) configured." if total else "Network collector is enabled, but no DNS, TCP, or HTTP checks are configured.",
        {"dns_checks": dns_count, "tcp_checks": tcp_count, "http_checks": http_count},
        "No action required." if total else "Add dns_checks, tcp_checks, or http_checks, or disable the network collector.",
    )


def _check_backup_health_config(config: dict[str, Any]) -> HealthCheck | None:
    health_config = config.get("collectors", {}).get("backup_health", {})
    if not health_config.get("enabled", False):
        return None
    repos = health_config.get("repos", []) or []
    return HealthCheck(
        "preflight_backup_health_repos",
        "Backup health repos configured",
        "ok" if repos else "warning",
        f"{len(repos)} backup repo(s) monitored." if repos else "Backup health collector is enabled, but no repos are configured to watch.",
        {"configured_repos": len(repos), "configuration_complete": bool(repos)},
        "No action required." if repos else "Add repos: with a restic repository or a systemd backup unit, or disable the backup_health collector.",
    )


def run_doctor(config_path: str | Path) -> int:
    checks: list[HealthCheck] = [_check_python()]
    config, config_check = _check_config(config_path)
    checks.append(config_check)
    if config is not None:
        checks.extend(_check_output_paths(config))
        optional_checks = [
            _check_secrets(config),
            _check_docker(config),
            _check_backup_config(config),
            _check_backup_health_config(config),
            _check_network_config(config),
        ]
        checks.extend(check for check in optional_checks if check is not None)
        checks.extend(_check_homeassistant(config, build_store(config.get("secrets", {}))))

    print(render(checks))
    return 1 if any(check.status == "critical" for check in checks) else 0
