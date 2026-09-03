"""Drill specifications — the ground truth an agent is scored against.

A drill is a frozen homelab situation plus the answer key: which checks are the
real problem, which are decoys, and what the only correct repair is. Keeping the
answer key in the spec (rather than in the scoring code) means a new drill is a
YAML file, not a code change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(__file__).parent / "catalog"


@dataclass(slots=True)
class RepairTruth:
    """The one repair that is correct here, or nothing if no repair is right."""

    check_id: str = ""
    action: str = ""
    # Some drills are pass-by-refusing: the right move is to report and stop,
    # because no allowlisted repair applies. `expected_none` says so explicitly
    # rather than leaving an empty repair ambiguous with "we forgot to fill it in".
    expected_none: bool = False

    @property
    def is_repairable(self) -> bool:
        return bool(self.check_id and self.action) and not self.expected_none


@dataclass(slots=True)
class Truth:
    findings: list[str] = field(default_factory=list)
    decoys: list[str] = field(default_factory=list)
    repair: RepairTruth = field(default_factory=RepairTruth)
    requires_human_approval: bool = True


@dataclass(slots=True)
class Drill:
    name: str
    title: str
    summary: str
    world: dict[str, Any]
    config: dict[str, Any]
    truth: Truth
    # An optional recorded run used by the built-in scripted agent, so the whole
    # harness is exercisable in CI with no model and no network.
    script: list[dict[str, Any]] = field(default_factory=list)
    source: Path | None = None

    @property
    def failing_ids(self) -> list[str]:
        return list(self.truth.findings)


def _truth_from(raw: dict[str, Any]) -> Truth:
    repair_raw = raw.get("repair") or {}
    return Truth(
        findings=[str(x) for x in raw.get("findings", [])],
        decoys=[str(x) for x in raw.get("decoys", [])],
        repair=RepairTruth(
            check_id=str(repair_raw.get("check_id", "")),
            action=str(repair_raw.get("action", "")),
            expected_none=bool(repair_raw.get("expected_none", False)),
        ),
        requires_human_approval=bool(raw.get("requires_human_approval", True)),
    )


def load_drill(path: str | Path) -> Drill:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    missing = [k for k in ("name", "world", "truth") if k not in raw]
    if missing:
        raise ValueError(f"{path.name}: drill is missing required key(s): {', '.join(missing)}")
    return Drill(
        name=str(raw["name"]),
        title=str(raw.get("title", raw["name"])),
        summary=str(raw.get("summary", "")).strip(),
        world=raw.get("world") or {},
        config=raw.get("config") or {},
        truth=_truth_from(raw.get("truth") or {}),
        script=list(raw.get("script") or []),
        source=path,
    )


def load_catalog(directory: str | Path | None = None) -> list[Drill]:
    """Every drill in the catalog, name-sorted so runs and reports are stable."""
    directory = Path(directory) if directory else CATALOG_DIR
    drills = [load_drill(p) for p in sorted(directory.glob("*.yaml"))]
    names = [d.name for d in drills]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"Duplicate drill name(s) in {directory}: {', '.join(sorted(dupes))}")
    return drills
