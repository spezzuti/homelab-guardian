from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "name": "Homelab Guardian",
        "report_path": "reports/latest.md",
        "database_path": "data/guardian.sqlite",
        "retention_days": 60,
    },
    "collectors": {
        "docker": {"enabled": False, "socket_url": "unix://var/run/docker.sock", "exclude_containers": []},
        "homeassistant": {"enabled": False, "url": "", "token_env": "HOMEASSISTANT_TOKEN"},
        # All collectors are opt-in (enabled: False by default) so an
        # unconfigured host stays quiet instead of emitting "not configured"
        # tiles. config.example.yaml turns the common ones on.
        "network": {"enabled": False, "dns_checks": [], "tcp_checks": []},
        "backups": {"enabled": False, "paths": []},
        "systemd": {"enabled": False, "include_user": False, "units": []},
        "disks": {"enabled": False, "paths": []},
    },
    "web": {
        # Dashboard authentication. mode: none | basic | forward_auth | oidc.
        # Defaults to none so existing deployments are unchanged until opted in.
        "auth": {"mode": "none"},
    },
    "secrets": {
        "provider": "env",
        "bitwarden": {
            "access_token_env": "BWS_ACCESS_TOKEN",
            "project_id": "",
            "cache_seconds": 300,
            "bws_path": "bws",
        },
    },
    "ai": {
        "enabled": False,
        "base_url": "",
        "model": "",
        "api_key_env": "GUARDIAN_AI_API_KEY",
    },
    "notifications": {
        "telegram": {
            "enabled": False,
            "bot_token_env": "TELEGRAM_BOT_TOKEN",
            "chat_id_env": "TELEGRAM_CHAT_ID",
            "send_on": "changes",
            "confirm_scans": 1,
        },
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}

    if not isinstance(loaded, dict):
        raise ValueError("Config root must be a mapping")

    return deep_merge(DEFAULT_CONFIG, loaded)
