"""Guardian's agent evaluation harness.

Scripted incident drills that score an attached agent's detect -> diagnose ->
repair performance against ground truth, and -- more importantly -- assert that
Guardian's approval gate holds regardless of how the agent behaves.

See docs/drills.md.
"""
from homelab_guardian.drills.runner import (
    CallableAgent,
    DrillAnswer,
    DrillResult,
    Scorecard,
    ScriptedAgent,
    load_catalog,
    load_drill,
    render_report,
    run_catalog,
    run_drill,
)

__all__ = ["CallableAgent", "DrillAnswer", "DrillResult", "Scorecard", "ScriptedAgent",
           "load_catalog", "load_drill", "render_report", "run_catalog", "run_drill"]
