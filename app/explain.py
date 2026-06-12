from __future__ import annotations

import json
import os
from typing import Any

import requests

from app.diff import ScanDiff
from app.models import HealthCheck
from app.reports.markdown_report import overall_status

DEFAULT_API_KEY_ENV = "GUARDIAN_AI_API_KEY"

SYSTEM_PROMPT = """You are the explanation layer of Homelab Guardian, a read-only homelab health monitor.
You receive structured health-check results and a diff against the previous scan. You have no other
access: no shell, no APIs, no ability to change anything, and you must not suggest that you do.

Write a short plain-English briefing for the homelab owner:
1. One or two sentences: overall situation, leading with what matters most.
2. If there are problems: the most likely root cause for each, reasoning only from the provided
   evidence. Group related symptoms (e.g. many entities from one integration) into one cause.
3. The safest next diagnostic step for each problem. Only ever suggest reading/checking actions
   (look at a log, check a cable, open a UI). Never suggest destructive or state-changing commands.
4. If everything is healthy, say so in one sentence and stop.

Be concrete and brief. No headers, no bullet-point spam, no restating raw numbers the report
already shows. Do not invent facts not present in the evidence. If evidence is insufficient,
say what is unknown rather than guessing."""


def _problems_payload(checks: list[HealthCheck], max_checks: int, max_evidence_chars: int) -> list[dict[str, Any]]:
    problems = [c for c in checks if c.status != "ok"]
    payload = []
    for check in problems[:max_checks]:
        evidence = json.dumps(check.evidence, sort_keys=True, default=str)
        if len(evidence) > max_evidence_chars:
            evidence = evidence[:max_evidence_chars] + "…truncated"
        payload.append(
            {
                "id": check.id,
                "name": check.name,
                "status": check.status,
                "summary": check.summary,
                "evidence": evidence,
            }
        )
    return payload


def build_prompt(checks: list[HealthCheck], diff: ScanDiff | None, config: dict[str, Any]) -> str:
    max_checks = int(config.get("max_evidence_checks", 12))
    max_evidence_chars = int(config.get("max_evidence_chars", 800))
    payload: dict[str, Any] = {
        "overall_status": overall_status(checks),
        "counts": {
            status: sum(1 for c in checks if c.status == status)
            for status in ("critical", "warning", "unknown", "ok")
        },
        "problems": _problems_payload(checks, max_checks, max_evidence_chars),
        "healthy_checks": [c.name for c in checks if c.status == "ok"],
    }
    if diff is not None and diff.has_previous:
        payload["changes_since_previous_scan"] = {
            "regressions": diff.regressions,
            "improvements": diff.improvements,
            "new_checks": diff.new_checks,
            "removed_checks": diff.removed_checks,
            "unchanged_count": diff.unchanged_count,
        }
    return json.dumps(payload, indent=2, default=str)


def explain(
    config: dict[str, Any],
    checks: list[HealthCheck],
    diff: ScanDiff | None,
) -> str | None:
    """Ask the configured OpenAI-compatible endpoint for a plain-English
    briefing. Returns None when disabled, unconfigured, or on any error —
    the scan and report never depend on the model being reachable."""
    if not config.get("enabled", False):
        return None

    base_url = str(config.get("base_url", "")).rstrip("/")
    model = config.get("model", "")
    if not base_url or not model:
        print("AI explanation: enabled but base_url or model is missing; skipping.")
        return None

    headers = {"Content-Type": "application/json"}
    api_key = os.getenv(config.get("api_key_env") or DEFAULT_API_KEY_ENV, "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_prompt(checks, diff, config)},
                ],
                "max_tokens": int(config.get("max_tokens", 700)),
                "temperature": float(config.get("temperature", 0.2)),
            },
            timeout=float(config.get("timeout", 60)),
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as exc:
        print(f"AI explanation: request failed: {exc}")
        return None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(f"AI explanation: unexpected response shape: {exc}")
        return None

    text = (content or "").strip()
    return text or None
