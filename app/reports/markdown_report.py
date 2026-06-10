from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from app.models import HealthCheck


def render(checks: list[HealthCheck], scan_id: int | None = None) -> str:
    counts = Counter(check.status for check in checks)
    generated_at = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Homelab Guardian Health Report",
        "",
        f"Generated: `{generated_at}`",
    ]
    if scan_id is not None:
        lines.append(f"Scan ID: `{scan_id}`")
    lines.extend([
        "",
        "## Summary",
        "",
        f"- OK: {counts.get('ok', 0)}",
        f"- Warning: {counts.get('warning', 0)}",
        f"- Critical: {counts.get('critical', 0)}",
        f"- Unknown: {counts.get('unknown', 0)}",
        "",
        "## Checks",
        "",
    ])

    for check in checks:
        lines.extend([
            f"### {check.name}",
            "",
            f"- ID: `{check.id}`",
            f"- Status: **{check.status}**",
            f"- Summary: {check.summary}",
            f"- Recommended action: {check.recommended_action}",
            "- Evidence:",
            "",
            "```json",
            json.dumps(check.evidence, indent=2, sort_keys=True, default=str),
            "```",
            "",
        ])

    return "\n".join(lines).rstrip() + "\n"


def write_report(path: str | Path, checks: list[HealthCheck], scan_id: int | None = None) -> Path:
    report_path = Path(path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render(checks, scan_id=scan_id), encoding="utf-8")
    return report_path
