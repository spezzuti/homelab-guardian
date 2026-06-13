from __future__ import annotations

import os
from typing import Any

import requests

from homelab_guardian.models import HealthCheck, HealthStatus

READ_ONLY_STATES_ENDPOINT = "/api/states"
DEFAULT_TOKEN_ENV = "HOMEASSISTANT_TOKEN"


def _check(
    check_id: str,
    status: HealthStatus,
    summary: str,
    evidence: dict[str, Any],
    recommended_action: str,
    name: str = "Home Assistant",
) -> HealthCheck:
    return HealthCheck(check_id, name, status, summary, evidence, recommended_action)


def collect(config: dict[str, Any], secrets: Any = None) -> list[HealthCheck]:
    url = (config.get("url") or "").rstrip("/")
    token_env = config.get("token_env") or DEFAULT_TOKEN_ENV
    token = secrets.get(token_env) if secrets is not None else os.getenv(token_env, "")
    token = token or ""
    timeout = float(config.get("timeout", 10))

    if not url:
        return [
            _check(
                "ha_missing_url",
                "unknown",
                "Home Assistant collector is enabled, but no URL is configured.",
                {"token_env": token_env, "endpoint": READ_ONLY_STATES_ENDPOINT},
                "Set collectors.homeassistant.url in config.yaml or disable this collector.",
            )
        ]

    base_evidence = {"url": url, "endpoint": READ_ONLY_STATES_ENDPOINT, "token_env": token_env}
    if not token:
        return [
            _check(
                "ha_missing_token",
                "unknown",
                f"Home Assistant token was not found under the name: {token_env}",
                {**base_evidence, "token_present": False},
                "Create a Home Assistant long-lived access token and expose it under that name through the environment or the configured secrets provider. Do not put tokens in config.yaml.",
            )
        ]

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = requests.get(f"{url}{READ_ONLY_STATES_ENDPOINT}", headers=headers, timeout=timeout)
        if response.status_code in {401, 403}:
            return [
                _check(
                    "ha_invalid_token",
                    "unknown",
                    "Home Assistant rejected the configured token.",
                    {**base_evidence, "status_code": response.status_code, "token_present": True},
                    "Create a new long-lived access token, update only the local .env value, and retry. Do not commit the token.",
                )
            ]
        response.raise_for_status()
        states = response.json()
    except requests.exceptions.Timeout:
        return [
            _check(
                "ha_timeout",
                "unknown",
                "Timed out while reading Home Assistant states.",
                {**base_evidence, "timeout_seconds": timeout, "token_present": True},
                "Confirm the Home Assistant URL is reachable from this machine or container, then retry with the same read-only collector.",
            )
        ]
    except requests.exceptions.ConnectionError as exc:
        return [
            _check(
                "ha_unreachable",
                "unknown",
                "Could not reach Home Assistant at the configured URL.",
                {**base_evidence, "error": str(exc), "token_present": True},
                "Check the Home Assistant URL, DNS, port, container network path, or VPN/LAN reachability. Guardian did not call any services.",
            )
        ]
    except requests.exceptions.RequestException as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        return [
            _check(
                "ha_api_error",
                "unknown",
                "Home Assistant returned an error while reading states.",
                {**base_evidence, "status_code": status_code, "error": str(exc), "token_present": True},
                "Check the Home Assistant URL and API availability. Guardian only attempted a read-only GET /api/states request.",
            )
        ]
    except ValueError as exc:
        return [
            _check(
                "ha_invalid_response",
                "unknown",
                "Home Assistant returned a response that was not valid JSON.",
                {**base_evidence, "error": str(exc), "token_present": True},
                "Confirm the URL points at the Home Assistant HTTP API root, not a proxy error page or unrelated service.",
            )
        ]

    if not isinstance(states, list):
        return [
            _check(
                "ha_unexpected_response",
                "unknown",
                "Home Assistant states response did not have the expected list shape.",
                {**base_evidence, "response_type": type(states).__name__, "token_present": True},
                "Confirm the URL points at a normal Home Assistant instance and retry.",
            )
        ]

    unavailable = [s for s in states if s.get("state") in {"unavailable", "unknown"}]
    if not unavailable:
        return [
            _check(
                "ha_entities",
                "ok",
                f"Read {len(states)} entities with read-only GET /api/states; none are unavailable or unknown.",
                {"entity_count": len(states), "endpoint": READ_ONLY_STATES_ENDPOINT},
                "No action required.",
                name="Home Assistant entities",
            )
        ]

    sample = [{"entity_id": s.get("entity_id"), "state": s.get("state")} for s in unavailable[:25]]
    status: HealthStatus = "warning" if len(unavailable) < 10 else "critical"
    return [
        _check(
            "ha_unavailable_entities",
            status,
            f"{len(unavailable)} Home Assistant entities are unavailable or unknown.",
            {"entity_count": len(states), "affected_count": len(unavailable), "sample": sample, "endpoint": READ_ONLY_STATES_ENDPOINT},
            "Check recently changed integrations, batteries, network devices, or Home Assistant logs. Guardian did not modify Home Assistant.",
            name="Home Assistant unavailable entities",
        )
    ]
