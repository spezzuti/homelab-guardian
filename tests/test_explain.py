import json

from app.diff import ScanDiff
from app.explain import build_prompt, explain
from app.models import HealthCheck


def _check(check_id: str, status: str = "ok", summary: str = "fine") -> HealthCheck:
    return HealthCheck(check_id, check_id, status, summary, evidence={"detail": "x"})


def test_disabled_returns_none():
    assert explain({"enabled": False}, [_check("a")], None) is None


def test_enabled_without_endpoint_returns_none(capsys):
    assert explain({"enabled": True}, [_check("a")], None) is None
    assert "skipping" in capsys.readouterr().out


def test_prompt_contains_problems_but_compacts_ok_checks():
    checks = [_check("bad", "critical", "it broke"), _check("fine_one"), _check("fine_two")]
    payload = json.loads(build_prompt(checks, None, {}))
    assert payload["overall_status"] == "critical"
    assert [p["id"] for p in payload["problems"]] == ["bad"]
    assert payload["healthy_checks"] == ["fine_one", "fine_two"]
    assert "changes_since_previous_scan" not in payload


def test_prompt_includes_diff_when_present():
    diff = ScanDiff(previous_scan_id=4, previous_created_at="t")
    diff.regressions.append(
        {"id": "a", "name": "A", "previous_status": "ok", "current_status": "warning", "summary": "s"}
    )
    payload = json.loads(build_prompt([_check("a", "warning")], diff, {}))
    assert payload["changes_since_previous_scan"]["regressions"][0]["id"] == "a"


def test_prompt_truncates_large_evidence():
    check = HealthCheck("big", "big", "warning", "s", evidence={"blob": "y" * 5000})
    payload = json.loads(build_prompt([check], None, {"max_evidence_chars": 100}))
    assert len(payload["problems"][0]["evidence"]) <= 120
    assert payload["problems"][0]["evidence"].endswith("…truncated")


def test_problem_count_capped():
    checks = [_check(f"p{i}", "warning") for i in range(20)]
    payload = json.loads(build_prompt(checks, None, {"max_evidence_checks": 5}))
    assert len(payload["problems"]) == 5
