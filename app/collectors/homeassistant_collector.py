from __future__ import annotations

import os
from typing import Any

import requests

from app.models import HealthCheck


def collect(config: dict[str, Any]) -> list[HealthCheck]:
    url = (config.get("url") or "").rstrip("/")
    token_env = config.get("token_env") or "HOME_ASSISTANT_TOKEN"
    token = os.getenv(token_env, "")

    if not url:
        return [HealthCheck("ha_missing_url", "Home Assistant", "unknown", "Home Assistant collector is enabled, but no URL is configured.", {}, "Set collectors.homeassistant.url or disable this collector.")]
    if not token:
        return [HealthCheck("ha_missing_token", "Home Assistant", "unknown", f"Home Assistant token environment variable is not set: {token_env}", {"url": url, "token_env": token_env}, "Set the token in the local environment or disable this collector.")]

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = requests.get(f"{url}/api/states", headers=headers, timeout=10)
        response.raise_for_status()
        states = response.json()
    except Exception as exc:
        return [HealthCheck("ha_unavailable", "Home Assistant", "unknown", "Could not read Home Assistant states.", {"url": url, "error": str(exc)}, "Check Home Assistant URL, token, and network path.")]

    unavailable = [s for s in states if s.get("state") in {"unavailable", "unknown"}]
    if not unavailable:
        return [HealthCheck("ha_entities", "Home Assistant entities", "ok", f"Read {len(states)} entities; none are unavailable or unknown.", {"entity_count": len(states)}, "No action required.")]

    sample = [{"entity_id": s.get("entity_id"), "state": s.get("state")} for s in unavailable[:25]]
    status = "warning" if len(unavailable) < 10 else "critical"
    return [HealthCheck("ha_unavailable_entities", "Home Assistant unavailable entities", status, f"{len(unavailable)} Home Assistant entities are unavailable or unknown.", {"entity_count": len(states), "affected_count": len(unavailable), "sample": sample}, "Check recently changed integrations, batteries, network devices, or Home Assistant logs.")]
