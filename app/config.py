from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "name": "Homelab Guardian",
        "report_path": "reports/latest.md",
        "database_path": "data/guardian.sqlite",
    },
    "collectors": {
        "docker": {"enabled": False, "socket_url": "unix://var/run/docker.sock", "exclude_containers": []},
        "homeassistant": {"enabled": False, "url": "", "token_env": "HOMEASSISTANT_TOKEN"},
        "network": {"enabled": True, "dns_checks": [], "tcp_checks": []},
        "backups": {"enabled": True, "paths": []},
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
