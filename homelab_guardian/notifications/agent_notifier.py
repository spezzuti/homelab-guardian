from __future__ import annotations

import os
from typing import Any

import requests

from homelab_guardian.alerting import AlertEvents
from homelab_guardian.models import HealthCheck
from homelab_guardian.notifications.telegram_notifier import should_notify
from homelab_guardian.reports.markdown_report import overall_status

# Agent delivery channel. When Guardian is attached to an AI agent (Marcus,
# Claude, ...) over MCP, the *agent* should be the single voice to the user —
# Guardian feeds it the deterministic truth and the agent narrates it and
# offers to fix. So instead of messaging the user directly (the Telegram
# channel), this notifier POSTs the same flap-damped, confirmed transitions to
# the agent's intake webhook as a structured event. Guardian still owns the
# decision of *what* is alert-worthy (it reuses update_alert_states' events);
# only the delivery target changes.
#
# Read-only side note: this sends nothing to the user. The user-facing message
# is the agent's job. The one exception is the critical-fallback in main.py:
# if a *critical* could not be handed to the agent, Guardian sends it directly
# over the existing Telegram channel (same shared bot) so a down agent never
# silently swallows a critical.

DEFAULT_AUTH_HEADER = "Authorization"


def has_critical(events: AlertEvents) -> bool:
    return any(e.get("current_status") == "critical" for e in events.confirmed)


def build_payload(
    checks: list[HealthCheck], events: AlertEvents, scan_id: int | None
) -> dict[str, Any]:
    """A structured event the agent can reason over and act on. Each transition
    is enriched from the full check with the evidence and recommended action so
    the agent can explain *why* and propose the safe next step."""
    by_id = {c.id: c for c in checks}

    def enrich(event: dict[str, Any]) -> dict[str, Any]:
        check = by_id.get(event.get("id"))
        item = {
            "id": event.get("id"),
            "name": event.get("name"),
            "previous_status": event.get("previous_status"),
            "current_status": event.get("current_status"),
            "summary": event.get("summary"),
            "scans_observed": event.get("scans_observed"),
        }
        if check is not None:
            item["group"] = check.group or None
            item["evidence"] = check.evidence
            item["recommended_action"] = check.recommended_action
        return item

    return {
        "source": "homelab-guardian",
        "event": "health_change",
        "scan_id": scan_id,
        "overall": overall_status(checks),
        "confirmed": [enrich(e) for e in events.confirmed],
        "recovered": [enrich(e) for e in events.recovered],
    }


def notify(
    config: dict[str, Any],
    checks: list[HealthCheck],
    events: AlertEvents,
    scan_id: int | None,
    secrets: Any = None,
) -> bool:
    """POST confirmed transitions to the agent's webhook. Returns True only if
    an event was actually delivered; False if there was nothing to send OR the
    delivery failed. Never raises — a failed push must not fail the scan.

    The caller distinguishes 'nothing to send' from 'delivery failed' via
    has_critical(): a confirmed critical always satisfies should_notify, so if
    a critical is present and this returns False, the delivery failed (and the
    caller triggers the Telegram critical-fallback)."""
    if not config.get("enabled", False):
        return False

    send_on = str(config.get("send_on", "changes")).lower()
    if not should_notify(send_on, events):
        return False

    url = config.get("webhook_url")
    if not url:
        print("Agent notifier: enabled but no webhook_url is configured.")
        return False

    def _resolve(name: str) -> str:
        if secrets is not None:
            return secrets.get(name) or ""
        return os.getenv(name, "")

    headers = dict(config.get("headers") or {})
    token_env = config.get("token_env")
    if token_env:
        token = _resolve(token_env)
        if token:
            header_name = config.get("auth_header") or DEFAULT_AUTH_HEADER
            scheme = config.get("auth_scheme", "Bearer")
            headers[header_name] = f"{scheme} {token}".strip()

    try:
        response = requests.post(
            url,
            json=build_payload(checks, events, scan_id),
            headers=headers,
            timeout=float(config.get("timeout", 10)),
        )
        if response.status_code >= 300:
            print(f"Agent notifier: webhook returned HTTP {response.status_code}: {response.text[:200]}")
            return False
    except requests.exceptions.RequestException as exc:
        print(f"Agent notifier: webhook POST failed: {exc}")
        return False

    print(f"Agent notifier: pushed {len(events.confirmed)} confirmed / {len(events.recovered)} recovered to the agent.")
    return True
