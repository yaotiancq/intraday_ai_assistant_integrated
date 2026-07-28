from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScoreFactor:
    raw_value: Any
    normalized_score: float
    weight: float
    weighted_contribution: float
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_value": self.raw_value,
            "normalized_score": round(self.normalized_score, 4),
            "weight": self.weight,
            "weighted_contribution": round(self.weighted_contribution, 4),
            "reason_codes": list(self.reason_codes),
        }


def make_factor(raw_value: Any, normalized_score: float, weight: float, *reason_codes: str) -> ScoreFactor:
    score = max(0.0, min(100.0, float(normalized_score)))
    return ScoreFactor(
        raw_value=raw_value,
        normalized_score=score,
        weight=float(weight),
        weighted_contribution=score * float(weight) / 100.0,
        reason_codes=tuple(code for code in reason_codes if code),
    )

