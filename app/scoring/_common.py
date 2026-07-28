"""Small shared helpers for transparent deterministic scorecards."""

from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum
import math
from typing import Any, Mapping

from app.indicators._bars import finite_float


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return json_safe(value.value)
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_safe(item) for item in value]
    number = finite_float(value)
    return number if number is not None else str(value)


def validate_weights(weights: Mapping[str, Any], expected_total: float, label: str) -> dict[str, float]:
    clean: dict[str, float] = {}
    for factor, raw_weight in weights.items():
        weight = finite_float(raw_weight)
        if weight is None or weight < 0:
            raise ValueError(f"{label} weight for {factor!r} must be a non-negative number")
        clean[str(factor)] = weight
    if not clean:
        raise ValueError(f"{label} weights cannot be empty")
    if not math.isclose(sum(clean.values()), expected_total, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError(f"{label} weights must sum to {expected_total:g}; got {sum(clean.values()):g}")
    return clean


def factor_record(
    raw_value: Any,
    normalized_score: float,
    weight: float,
    reason_codes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    score = clamp(normalized_score)
    return {
        "raw_value": json_safe(raw_value),
        "normalized_score": round(score, 4),
        "weight": round(float(weight), 6),
        "weighted_contribution": round(score * float(weight) / 100.0, 4),
        "reason_codes": sorted(set(str(code) for code in (reason_codes or ()) if code)),
    }


def explicit_directional_score(evidence: Mapping[str, Any], factor: str, direction: str) -> float | None:
    """Read an optional caller-supplied normalized factor score."""

    direction = direction.lower()
    sources = (evidence.get("factor_scores"), evidence.get("normalized_scores"))
    for source in sources:
        if not isinstance(source, Mapping) or factor not in source:
            continue
        value = source[factor]
        if isinstance(value, Mapping):
            selected = value.get(direction, value.get(direction.upper(), value.get("score")))
        else:
            selected = value
        score = finite_float(selected)
        if score is not None:
            return clamp(score)
    directional_key = f"{direction}_{factor}_score"
    score = finite_float(evidence.get(directional_key))
    if score is None:
        score = finite_float(evidence.get(f"{factor}_{direction}_score"))
    if score is None:
        score = finite_float(evidence.get(f"{factor}_score"))
    return clamp(score) if score is not None else None


def score_from_signed(value: float | None, cap_abs: float, direction: str) -> float:
    if value is None:
        return 50.0
    signed = max(-1.0, min(1.0, value / cap_abs)) if cap_abs > 0 else 0.0
    if direction.upper() == "SHORT":
        signed *= -1.0
    return 50.0 + signed * 50.0


def resolve_section(config: Mapping[str, Any], section: str) -> Mapping[str, Any]:
    value = config.get(section)
    return value if isinstance(value, Mapping) else config
