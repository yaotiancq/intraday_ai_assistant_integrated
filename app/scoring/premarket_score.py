"""Transparent 0-100 premarket scorecards for long and short evidence."""

from __future__ import annotations

from datetime import date, datetime, time
from statistics import mean
from typing import Any, Mapping

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, parse_cutoff
from app.scoring._common import (
    clamp,
    explicit_directional_score,
    factor_record,
    resolve_section,
    score_from_signed,
    validate_weights,
)


DEFAULT_WEIGHTS = {
    "liquidity": 10,
    "premarket_relative_volume": 13,
    "gap_quality": 10,
    "catalyst_quality": 9,
    "technical_location": 14,
    "relative_strength": 16,
    "sector_alignment": 11,
    "market_regime_alignment": 8,
    "volatility_quality": 5,
    "spread_quality": 4,
}


def _number(evidence: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = finite_float(evidence.get(key))
        if value is not None:
            return value
    return None


def _gap_percent(evidence: Mapping[str, Any]) -> float | None:
    value = _number(evidence, "gap_percent", "premarket_gap_percent", "change_pct")
    if value is None:
        ratio = _number(evidence, "gap_return", "gap_decimal")
        value = ratio * 100.0 if ratio is not None else None
    return value


def _relative_strength(evidence: Mapping[str, Any]) -> float | None:
    direct = _number(
        evidence,
        "mean_relative_strength",
        "relative_strength_excess_return",
        "relative_strength",
    )
    if direct is not None:
        return direct
    values = [
        value
        for key in (
            "relative_strength_vs_spy",
            "relative_strength_vs_qqq",
            "relative_strength_vs_sector",
            "relative_strength_vs_industry",
        )
        if (value := finite_float(evidence.get(key))) is not None
    ]
    relative = evidence.get("relative_strength")
    if isinstance(relative, Mapping):
        nested = finite_float(relative.get("mean_excess_return"))
        if nested is not None:
            values.append(nested)
    return mean(values) if values else None


def _directional_bias(evidence: Mapping[str, Any], factor: str) -> float | None:
    raw = evidence.get(factor)
    if isinstance(raw, str):
        normalized = raw.strip().upper()
        if any(token in normalized for token in ("BULL", "HIGH", "LONG", "ABOVE", "UP")):
            return 1.0
        if any(token in normalized for token in ("BEAR", "LOW", "SHORT", "BELOW", "DOWN")):
            return -1.0
        return 0.0
    return _number(evidence, f"{factor}_bias", factor)


def _factor_value_and_score(
    factor: str,
    direction: str,
    evidence: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[Any, float, list[str]]:
    explicit = explicit_directional_score(evidence, factor, direction)
    if explicit is not None:
        return evidence.get(factor, evidence.get("factor_scores", {}).get(factor) if isinstance(evidence.get("factor_scores"), Mapping) else None), explicit, ["EXPLICIT_NORMALIZED_INPUT"]

    filters = config.get("premarket_filters", {}) if isinstance(config.get("premarket_filters"), Mapping) else {}
    is_long = direction == "LONG"
    if factor == "liquidity":
        raw = _number(evidence, "average_daily_dollar_volume", "avg_daily_dollar_volume")
        threshold = finite_float(filters.get("minimum_average_daily_dollar_volume")) or 50_000_000.0
        score = clamp((raw or 0.0) / (2.0 * threshold) * 100.0) if raw is not None else 50.0
        return raw, score, ["LIQUIDITY_ABOVE_MINIMUM" if raw is not None and raw >= threshold else "LIQUIDITY_UNVERIFIED" if raw is None else "LIQUIDITY_BELOW_MINIMUM"]

    if factor == "premarket_relative_volume":
        raw = _number(evidence, "premarket_relative_volume", "relative_volume", "premarket_rvol")
        score = clamp((raw or 0.0) / 2.0 * 100.0) if raw is not None else 50.0
        return raw, score, ["ABNORMAL_PREMARKET_VOLUME" if raw is not None and raw >= 1.5 else "NORMAL_PREMARKET_VOLUME" if raw is not None else "PREMARKET_VOLUME_BASELINE_UNAVAILABLE"]

    if factor == "gap_quality":
        raw = _gap_percent(evidence)
        maximum = finite_float(filters.get("maximum_absolute_gap_percent")) or 15.0
        if raw is None:
            return raw, 50.0, ["GAP_UNAVAILABLE"]
        magnitude = abs(raw)
        quality = 0.0 if magnitude > maximum else clamp(40.0 + min(magnitude / 3.0, 1.0) * 60.0)
        aligned = raw > 0 if is_long else raw < 0
        score = quality if aligned else max(0.0, 100.0 - quality) if raw else 50.0
        return raw, score, ["GAP_DIRECTION_ALIGNED" if aligned else "GAP_DIRECTION_CONFLICT" if raw else "FLAT_GAP"]

    if factor == "catalyst_quality":
        raw = _number(evidence, "catalyst_quality", "catalyst_score", "news_score")
        if raw is None:
            raw = 100.0 if evidence.get("material_catalyst") else 0.0
        catalyst_direction = str(evidence.get("catalyst_direction", "NEUTRAL")).upper()
        aligned = catalyst_direction in {"NEUTRAL", direction, "BULLISH" if is_long else "BEARISH"}
        score = clamp(raw if aligned else 100.0 - raw)
        return {"quality": raw, "direction": catalyst_direction}, score, ["CATALYST_ALIGNED" if aligned and raw > 0 else "CATALYST_CONFLICT" if raw > 0 else "NO_MATERIAL_CATALYST"]

    if factor == "technical_location":
        raw = evidence.get("technical_location", evidence.get("position"))
        bias = _directional_bias(evidence, "technical_location")
        if bias is None and isinstance(raw, str):
            bias = _directional_bias({"technical_location": raw}, "technical_location")
        score = score_from_signed(bias, 1.0, direction)
        return raw, score, ["TECHNICAL_LOCATION_ALIGNED" if score > 55 else "TECHNICAL_LOCATION_CONFLICT" if score < 45 else "TECHNICAL_LOCATION_NEUTRAL"]

    if factor == "relative_strength":
        raw = _relative_strength(evidence)
        score = score_from_signed(raw, 0.015, direction)
        label = "OUTPERFORMING_BENCHMARKS" if raw is not None and raw > 0 else "UNDERPERFORMING_BENCHMARKS" if raw is not None and raw < 0 else "RELATIVE_STRENGTH_NEUTRAL"
        if not is_long and raw is not None:
            label = "UNDERPERFORMANCE_SUPPORTS_SHORT" if raw < 0 else "OUTPERFORMANCE_CONFLICTS_WITH_SHORT" if raw > 0 else label
        return raw, score, [label]

    if factor == "sector_alignment":
        raw = _number(evidence, "relative_strength_vs_sector", "sector_return", "sector_change")
        if raw is None:
            raw = _directional_bias(evidence, "sector_direction")
            cap = 1.0
        else:
            cap = 0.01
        score = score_from_signed(raw, cap, direction)
        return raw, score, ["SECTOR_ALIGNED" if score > 55 else "SECTOR_CONFLICT" if score < 45 else "SECTOR_NEUTRAL"]

    if factor == "market_regime_alignment":
        raw = _number(evidence, "market_regime_score", "market_score", "market_return")
        if raw is None and isinstance(evidence.get("market_regime"), Mapping):
            raw = finite_float(evidence["market_regime"].get("score"))
        cap = 100.0 if raw is not None and abs(raw) > 1.0 else 0.01
        score = score_from_signed(raw, cap, direction)
        return raw, score, ["MARKET_ALIGNED" if score > 55 else "MARKET_CONFLICT" if score < 45 else "MARKET_NEUTRAL"]

    if factor == "volatility_quality":
        raw = _number(evidence, "atr_percent", "volatility_percent", "realized_volatility")
        if raw is None:
            return raw, 50.0, ["VOLATILITY_UNAVAILABLE"]
        # Reward tradable movement while discounting both dead and extreme tape.
        raw_percent = raw * 100.0 if abs(raw) < 0.2 else raw
        score = clamp(100.0 - abs(raw_percent - 2.0) * 25.0)
        return raw, score, ["VOLATILITY_TRADABLE" if score >= 60 else "VOLATILITY_LOW_QUALITY"]

    if factor == "spread_quality":
        raw = _number(evidence, "spread_bps", "premarket_spread_bps")
        maximum = finite_float(filters.get("maximum_premarket_spread_bps")) or 35.0
        score = clamp(100.0 * (1.0 - (raw or 0.0) / maximum)) if raw is not None else 50.0
        return raw, score, ["TIGHT_SPREAD" if score >= 60 else "WIDE_SPREAD" if raw is not None else "SPREAD_UNAVAILABLE"]

    raw = evidence.get(factor)
    return raw, 50.0, ["NEUTRAL_UNMAPPED_FACTOR"]


def score_premarket(
    evidence: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Return independent long/short premarket scorecards and factor audit data."""

    config = config or {}
    section = resolve_section(config, "premarket_scoring")
    raw_weights = section.get("weights", DEFAULT_WEIGHTS) if isinstance(section, Mapping) else DEFAULT_WEIGHTS
    weights = validate_weights(raw_weights, 100.0, "premarket scoring")
    breakdowns: dict[str, dict[str, dict[str, Any]]] = {"LONG": {}, "SHORT": {}}
    scores: dict[str, float] = {}
    for direction in ("LONG", "SHORT"):
        for factor, weight in weights.items():
            raw, normalized, reasons = _factor_value_and_score(factor, direction, evidence, config)
            breakdowns[direction][factor] = factor_record(raw, normalized, weight, reasons)
        scores[direction] = round(sum(item["weighted_contribution"] for item in breakdowns[direction].values()), 4)

    conflict_threshold = finite_float(
        (config.get("risk_gates") or {}).get("directional_conflict_max_difference")
        if isinstance(config.get("risk_gates"), Mapping)
        else None
    ) or 8.0
    difference = abs(scores["LONG"] - scores["SHORT"])
    if difference <= conflict_threshold:
        direction = "WATCH"
    else:
        direction = "LONG" if scores["LONG"] > scores["SHORT"] else "SHORT"
    selected_direction = direction if direction in {"LONG", "SHORT"} else ("LONG" if scores["LONG"] >= scores["SHORT"] else "SHORT")

    gap = abs(_gap_percent(evidence) or 0.0)
    catalyst = _number(evidence, "catalyst_quality", "catalyst_score", "news_score") or (100.0 if evidence.get("material_catalyst") else 0.0)
    rvol = _number(evidence, "premarket_relative_volume", "relative_volume") or 0.0
    event_gap = finite_float((config.get("premarket_filters") or {}).get("event_gap_percent")) if isinstance(config.get("premarket_filters"), Mapping) else None
    event_rvol = finite_float((config.get("premarket_filters") or {}).get("event_relative_volume")) if isinstance(config.get("premarket_filters"), Mapping) else None
    candidate_type = "EVENT_DRIVEN" if catalyst > 0 or gap >= (event_gap or 2.0) or rvol >= (event_rvol or 1.5) else "RELATIVE_STRENGTH"
    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    reason_codes = ["DIRECTIONAL_CONFLICT"] if direction == "WATCH" else [f"{direction}_EVIDENCE_DOMINANT"]
    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": cutoff.isoformat(),
        "score_type": "PREMARKET",
        "candidate_type": candidate_type,
        "premarket_score": scores[selected_direction],
        "score": scores[selected_direction],
        "long_score": scores["LONG"],
        "short_score": scores["SHORT"],
        "direction": direction,
        "directional_conflict": direction == "WATCH",
        "directional_score_difference": round(difference, 4),
        "factor_breakdown": breakdowns[selected_direction],
        "long_factor_breakdown": breakdowns["LONG"],
        "short_factor_breakdown": breakdowns["SHORT"],
        "reason_codes": reason_codes,
    }


compute_premarket_score = score_premarket


__all__ = ["DEFAULT_WEIGHTS", "compute_premarket_score", "score_premarket"]
