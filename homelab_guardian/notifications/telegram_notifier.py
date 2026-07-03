from __future__ import annotations

import html
import os
from typing import Any

import requests

from homelab_guardian.alerting import AlertEvents
from homelab_guardian.models import HealthCheck
from homelab_guardian.reports.markdown_report import STATUS_ICON, overall_status

TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
DEFAULT_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"

# send_on modes:
#   always   — every scan
#   changes  — when a confirmed failure OR recovery transition happened
#   problems — only when a confirmed failure transition happened
# Transitions are flap-damped: with notifications.telegram.confirm_scans > 1
# a status must hold for that many consecutive scans before it is announced.
VALID_SEND_ON = {"always", "changes", "problems"}


def should_notify(send_on: str, events: AlertEvents) -> bool:
    if send_on == "always":
        return True
    if send_on == "problems":
        return bool(events.confirmed)
    return events.any


def build_message(
    checks: list[HealthCheck],
    events: AlertEvents,
    scan_id: int | None,
    prefix: str | None = None,
) -> str:
    overall = overall_status(checks)
    visible = [c for c in checks if not c.acknowledged]
    icon = STATUS_ICON.get(overall, "•")
    counts = {status: sum(1 for c in visible if c.status == status) for status in STATUS_ICON}

    lines = []
    if prefix:
        lines.append(f"<b>{html.escape(prefix)}</b>")
    lines += [
        f"{icon} <b>Homelab Guardian — {overall.upper()}</b>",
        f"Scan #{scan_id} • {counts['critical']} critical, {counts['warning']} warning, "
        f"{counts['unknown']} unknown, {counts['ok']} ok",
    ]

    if events.confirmed:
        lines.append("")
        lines.append("<b>Now failing (confirmed):</b>")
        for event in events.confirmed[:10]:
            lines.append(
                f"📉 <b>{html.escape(event['name'])}</b>: {event['previous_status']} → "
                f"<b>{event['current_status']}</b> — {html.escape(event['summary'])}"
            )
        if len(events.confirmed) > 10:
            lines.append(f"…and {len(events.confirmed) - 10} more. See the full report.")

    if events.recovered:
        lines.append("")
        lines.append("<b>Recovered:</b>")
        for event in events.recovered[:10]:
            lines.append(
                f"📈 <b>{html.escape(event['name'])}</b>: {event['previous_status']} → "
                f"<b>{event['current_status']}</b>"
            )

    if not events.any:
        ongoing = [c for c in visible if c.status in {"critical", "warning"}]
        if ongoing:
            lines.append("")
            for check in ongoing[:5]:
                check_icon = STATUS_ICON.get(check.status, "•")
                lines.append(f"{check_icon} <b>{html.escape(check.name)}</b>: {html.escape(check.summary)}")

    message = "\n".join(lines)
    if len(message) > TELEGRAM_MESSAGE_LIMIT:
        message = message[: TELEGRAM_MESSAGE_LIMIT - 25].rstrip() + "\n…truncated, see report."
    return message


def notify(
    config: dict[str, Any],
    checks: list[HealthCheck],
    events: AlertEvents,
    scan_id: int | None,
    secrets: Any = None,
    force: bool = False,
    prefix: str | None = None,
) -> bool:
    """Send a Telegram summary if configured to. Never raises; a failed
    notification must not fail the scan that produced the report.

    `force` bypasses the send_on gate (used by the agent-mode critical-fallback,
    which must send regardless of send_on); `prefix` prepends a label line
    (e.g. "Guardian · agent unreachable") so a fallback message is recognizable
    and doubles as an agent-down signal."""
    if not config.get("enabled", False):
        return False

    send_on = str(config.get("send_on", "changes")).lower()
    if send_on not in VALID_SEND_ON:
        print(f"Telegram notifier: invalid send_on '{send_on}', expected one of {sorted(VALID_SEND_ON)}.")
        return False

    if not force and not should_notify(send_on, events):
        return False

    return send_text(config, build_message(checks, events, scan_id, prefix=prefix), secrets=secrets)


def send_text(config: dict[str, Any], text: str, secrets: Any = None) -> bool:
    """Low-level: send one HTML message to the configured Telegram chat,
    resolving token/chat id from env or the secrets provider. Never raises.
    Used by notify() and by the agent-mode overdue-acknowledgement fallback."""
    if not config.get("enabled", False):
        return False

    def _resolve(name: str) -> str:
        if secrets is not None:
            return secrets.get(name) or ""
        return os.getenv(name, "")

    token = _resolve(config.get("bot_token_env") or DEFAULT_BOT_TOKEN_ENV)
    chat_id = str(config.get("chat_id") or _resolve(config.get("chat_id_env") or DEFAULT_CHAT_ID_ENV))
    if not token or not chat_id:
        print("Telegram notifier: enabled but bot token or chat id was not found in the environment or secrets provider.")
        return False

    try:
        response = requests.post(  # nosec B113
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=float(config.get("timeout", 10)),
        )
        if response.status_code != 200:
            print(f"Telegram notifier: send failed with HTTP {response.status_code}: {response.text[:200]}")
            return False
    except requests.exceptions.RequestException as exc:
        print(f"Telegram notifier: send failed: {exc}")
        return False

    print("Telegram notifier: summary sent.")
    return True
