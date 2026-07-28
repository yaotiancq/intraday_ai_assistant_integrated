from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class AnalysisStage(str, Enum):
    UNIVERSE_VALIDATION = "universe_validation"
    PREMARKET = "premarket"
    OPENING_5M = "opening_5m"
    OPENING_15M = "opening_15m"

    @classmethod
    def parse(cls, value: str) -> "AnalysisStage":
        normalized = value.strip().lower().replace("-", "_")
        return cls(normalized)


@dataclass(frozen=True)
class StageContext:
    trade_date: str
    stage: AnalysisStage
    strategy_version: str
    scheduled_cutoff: datetime
    actual_started_at: datetime
    late_start: bool

    @property
    def run_key(self) -> str:
        return f"{self.trade_date}:{self.stage.value}:{self.strategy_version}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "stage": self.stage.value,
            "strategy_version": self.strategy_version,
            "run_key": self.run_key,
            "scheduled_cutoff": self.scheduled_cutoff.isoformat(),
            "actual_started_at": self.actual_started_at.isoformat(),
            "late_start": self.late_start,
        }


@dataclass
class StageResult:
    context: StageContext
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.context.to_dict(),
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            **self.payload,
        }

