from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.models import HealthCheck

STATUS_ORDER = {"critical": 0, "warning": 1, "unknown": 2, "ok": 3}
STATUS_ICON = {"critical": "🚨", "warning": "⚠️", "unknown": "❔", "ok": "✅"}


def overall_status(checks: list[HealthCheck]) -> str:
    statuses = {check.status for check in checks}
    if "critical" in statuses:
        return "critical"
    if "warning" in statuses:
        return "warning"
    if "unknown" in statuses:
        return "unknown"
    return "ok"


def _sorted_checks(checks: list[HealthCheck]) -> list[HealthCheck]:
    return sorted(checks, key=lambda check: (STATUS_ORDER.get(check.status, 99), check.name.lower()))


def _section_title(status: str) -> str:
    titles = {
        "critical": "Critical issues — check first",
        "warning": "Warnings — likely needs attention",
        "unknown": "Unknown — not enough signal",
        "ok": "OK checks — healthy/readable",
    }
    return titles.get(status, status.title())


def _render_check(check: HealthCheck) -> list[str]:
    icon = STATUS_ICON.get(check.status, "•")
    return [
        f"### {icon} {check.name}",
        "",
        f"- ID: `{check.id}`",
        f"- Status: **{check.status}**",
        f"- Summary: {check.summary}",
        f"- Safest next step: {check.recommended_action}",
        "- Evidence:",
        "",
        "```json",
        json.dumps(check.evidence, indent=2, sort_keys=True, default=str),
        "```",
        "",
    ]


def render(checks: list[HealthCheck], scan_id: int | None = None) -> str:
    counts = Counter(check.status for check in checks)
    generated_at = datetime.now(timezone.utc).isoformat()
    overall = overall_status(checks)
    lines = [
        "# Homelab Guardian Health Report",
        "",
        f"Generated: `{generated_at}`",
    ]
    if scan_id is not None:
        lines.append(f"Scan ID: `{scan_id}`")
    lines.extend(
        [
            "",
            "## Overall status",
            "",
            f"**{STATUS_ICON.get(overall, '')} {overall.upper()}**",
            "",
            "## Summary counts",
            "",
            f"- Critical: {counts.get('critical', 0)}",
            f"- Warning: {counts.get('warning', 0)}",
            f"- Unknown: {counts.get('unknown', 0)}",
            f"- OK: {counts.get('ok', 0)}",
            "",
        ]
    )

    by_status: dict[str, list[HealthCheck]] = {"critical": [], "warning": [], "unknown": [], "ok": []}
    for check in _sorted_checks(checks):
        by_status.setdefault(check.status, []).append(check)

    for status in ["critical", "warning", "unknown", "ok"]:
        group = by_status.get(status, [])
        if not group:
            continue
        lines.extend([f"## {_section_title(status)}", ""])
        if status == "ok" and len(group) > 5:
            lines.extend([f"{len(group)} checks are OK. Showing names only to keep the report readable.", ""])
            for check in group:
                lines.append(f"- ✅ {check.name}: {check.summary}")
            lines.append("")
            continue
        for check in group:
            lines.extend(_render_check(check))

    return "\n".join(lines).rstrip() + "\n"


def write_report(path: str | Path, checks: list[HealthCheck], scan_id: int | None = None) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render(checks, scan_id=scan_id), encoding="utf-8")
    return report_path
