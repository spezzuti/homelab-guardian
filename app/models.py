from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

HealthStatus = Literal["ok", "warning", "critical", "unknown"]


@dataclass(slots=True)
class HealthCheck:
    id: str
    name: str
    status: HealthStatus
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    recommended_action: str = "No action required."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
