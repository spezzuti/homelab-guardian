from __future__ import annotations

import html
import os
from typing import Any

import requests

from app.diff import ScanDiff
from app.models import HealthCheck
from app.reports.markdown_report import STATUS_ICON, overall_status

TELEGRAM_MESSAGE_LIMIT = 4096
DEFAULT_BOT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
DEFAULT_CHAT_ID_ENV = "TELEGRAM_CHAT_ID"

# send_on modes:
#   always   — every scan
#   changes  — only when something changed vs the previous scan
#   problems — only when overall status is warning or critical
VALID_SEND_ON = {"always", "changes", "problems"}


def should_notify(send_on: str, overall: str, diff: ScanDiff | None) -> bool:
    if send_on == "always":
        return True
    if send_on == "problems":
        return overall in {"warning", "critical"}
    # "changes": first scan counts as a change worth announcing only if unhealthy.
    if diff is None or not diff.has_previous:
        return overall in {"warning", "critical"}
    return diff.has_changes


def _change_lines(diff: ScanDiff) -> list[str]:
    lines: list[str] = []
    for change in diff.regressions:
        lines.append(
            f"📉 <b>{html.escape(change['name'])}</b>: {change['previous_status']} → "
            f"<b>{change['current_status']}</b> — {html.escape(change['summary'])}"
        )
    for change in diff.improvements:
        lines.append(
            f"📈 <b>{html.escape(change['name'])}</b>: {change['previous_status']} → "
            f"<b>{change['current_status']}</b>"
        )
    for check in diff.new_checks:
        if check["status"] != "ok":
            icon = STATUS_ICON.get(check["status"], "•")
            lines.append(
                f"🆕 {icon} <b>{html.escape(check['name'])}</b>: {check['status']} — "
                f"{html.escape(check['summary'])}"
            )
    new_ok = sum(1 for check in diff.new_checks if check["status"] == "ok")
    if new_ok:
        lines.append(f"🆕 {new_ok} new check(s) added, all ok.")
    for check in diff.removed_checks:
        lines.append(f"➖ <b>{html.escape(check['name'])}</b>: no longer checked")
    return lines


def build_message(checks: list[HealthCheck], diff: ScanDiff | None, scan_id: int | None) -> str:
    overall = overall_status(checks)
    checks = [c for c in checks if not c.acknowledged]
    icon = STATUS_ICON.get(overall, "•")
    counts = {status: sum(1 for c in checks if c.status == status) for status in STATUS_ICON}

    lines = [
        f"{icon} <b>Homelab Guardian — {overall.upper()}</b>",
        f"Scan #{scan_id} • {counts['critical']} critical, {counts['warning']} warning, "
        f"{counts['unknown']} unknown, {counts['ok']} ok",
    ]

    problems = [c for c in checks if c.status in {"critical", "warning"}]
    if problems:
        lines.append("")
        for check in problems[:10]:
            check_icon = STATUS_ICON.get(check.status, "•")
            lines.append(f"{check_icon} <b>{html.escape(check.name)}</b>: {html.escape(check.summary)}")
        if len(problems) > 10:
            lines.append(f"…and {len(problems) - 10} more. See the full report.")

    if diff is not None and diff.has_previous and diff.has_changes:
        lines.extend(["", "<b>What changed:</b>"])
        lines.extend(_change_lines(diff))

    message = "\n".join(lines)
    if len(message) > TELEGRAM_MESSAGE_LIMIT:
        message = message[: TELEGRAM_MESSAGE_LIMIT - 25].rstrip() + "\n…truncated, see report."
    return message


def notify(
    config: dict[str, Any],
    checks: list[HealthCheck],
    diff: ScanDiff | None,
    scan_id: int | None,
    secrets: Any = None,
) -> bool:
    """Send a Telegram summary if configured to. Never raises; a failed
    notification must not fail the scan that produced the report."""
    if not config.get("enabled", False):
        return False

    send_on = str(config.get("send_on", "changes")).lower()
    if send_on not in VALID_SEND_ON:
        print(f"Telegram notifier: invalid send_on '{send_on}', expected one of {sorted(VALID_SEND_ON)}.")
        return False

    if not should_notify(send_on, overall_status(checks), diff):
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
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": build_message(checks, diff, scan_id),
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
