"""Independent long/short opening-confirmation scorecards with penalties."""

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
    "opening_structure": 17,
    "vwap_confirmation": 14,
    "volume_confirmation": 14,
    "relative_strength": 15,
    "market_alignment": 8,
    "sector_alignment": 8,
    "gap_retention": 7,
    "price_action_quality": 10,
    "liquidity_and_spread": 4,
    "extension_quality": 3,
}

DEFAULT_PENALTIES = {
    "failed_breakout": 25,
    "failed_breakdown": 25,
    "failed_gap": 20,
    "weak_opening_volume": 10,
    "market_conflict": 10,
    "sector_conflict": 7,
    "excessive_extension": 12,
    "poor_close_location": 7,
    "wide_spread": 15,
}


def _number(evidence: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = finite_float(evidence.get(key))
        if value is not None:
            return value
    return None


def _direction_value(evidence: Mapping[str, Any], key: str) -> float | None:
    raw = evidence.get(key)
    value = finite_float(raw)
    if value is not None:
        return value
    normalized = str(raw or "").upper()
    if normalized in {"UP", "LONG", "BULLISH", "RISK_ON", "STRONG_RISK_ON", "ABOVE_VWAP"}:
        return 1.0
    if normalized in {"DOWN", "SHORT", "BEARISH", "RISK_OFF", "STRONG_RISK_OFF", "BELOW_VWAP"}:
        return -1.0
    return 0.0 if normalized else None


def _relative_strength(evidence: Mapping[str, Any]) -> float | None:
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
    direct = _number(evidence, "mean_relative_strength", "relative_strength_excess_return")
    if direct is not None:
        values.append(direct)
    return mean(values) if values else None


def _factor_value_and_score(
    factor: str,
    direction: str,
    evidence: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[Any, float, list[str]]:
    explicit = explicit_directional_score(evidence, factor, direction)
    if explicit is not None:
        return evidence.get(factor), explicit, ["EXPLICIT_NORMALIZED_INPUT"]
    is_long = direction == "LONG"

    if factor == "opening_structure":
        sequence = _number(evidence, "bullish_sequence_ratio" if is_long else "bearish_sequence_ratio")
        opening_return = _number(evidence, "opening_return")
        signed_score = score_from_signed(opening_return, 0.015, direction)
        sequence_score = clamp((sequence or 0.0) * 100.0) if sequence is not None else 50.0
        score = 0.55 * sequence_score + 0.45 * signed_score
        return {"sequence_ratio": sequence, "opening_return": opening_return}, score, ["OPENING_STRUCTURE_ALIGNED" if score > 55 else "OPENING_STRUCTURE_CONFLICT" if score < 45 else "OPENING_STRUCTURE_MIXED"]

    if factor == "vwap_confirmation":
        distance_atr = _number(evidence, "distance_from_vwap_atr")
        if distance_atr is None:
            price = _number(evidence, "current_price", "last_price")
            vwap = _number(evidence, "regular_session_vwap", "vwap")
            atr = _number(evidence, "atr", "atr14")
            distance_atr = (price - vwap) / atr if price is not None and vwap is not None and atr else None
        if distance_atr is None:
            state = _direction_value(evidence, "price_relative_to_vwap")
            score = score_from_signed(state, 1.0, direction)
        else:
            score = score_from_signed(distance_atr, 0.35, direction)
        crossed = bool(evidence.get("crossed_above_vwap" if is_long else "crossed_below_vwap"))
        if crossed:
            score = max(score, 75.0)
        return distance_atr, score, ["VWAP_CONFIRMED" if score > 55 else "VWAP_CONFLICT" if score < 45 else "VWAP_NEUTRAL"]

    if factor == "volume_confirmation":
        raw = _number(evidence, "opening_relative_volume", "relative_volume")
        acceleration = _number(evidence, "volume_acceleration")
        rvol_score = clamp((raw or 0.0) / 2.0 * 100.0) if raw is not None else 50.0
        acceleration_score = clamp((acceleration or 0.0) / 1.5 * 100.0) if acceleration is not None else 50.0
        score = 0.75 * rvol_score + 0.25 * acceleration_score
        return {"opening_relative_volume": raw, "volume_acceleration": acceleration}, score, ["OPENING_VOLUME_CONFIRMED" if raw is not None and raw >= 1.0 else "WEAK_OPENING_VOLUME" if raw is not None else "OPENING_VOLUME_UNAVAILABLE"]

    if factor == "relative_strength":
        raw = _relative_strength(evidence)
        score = score_from_signed(raw, 0.015, direction)
        return raw, score, ["RELATIVE_STRENGTH_ALIGNED" if score > 55 else "RELATIVE_STRENGTH_CONFLICT" if score < 45 else "RELATIVE_STRENGTH_NEUTRAL"]

    if factor in {"market_alignment", "sector_alignment"}:
        key = "market_direction" if factor == "market_alignment" else "sector_direction"
        raw = _direction_value(evidence, key)
        score = score_from_signed(raw, 1.0, direction)
        prefix = "MARKET" if factor == "market_alignment" else "SECTOR"
        return evidence.get(key), score, [f"{prefix}_ALIGNED" if score > 55 else f"{prefix}_CONFLICT" if score < 45 else f"{prefix}_NEUTRAL"]

    if factor == "gap_retention":
        retention = _number(evidence, "gap_retention")
        if retention is None:
            percent = _number(evidence, "gap_retention_percent")
            retention = percent / 100.0 if percent is not None else None
        gap = _number(evidence, "gap_return")
        if gap is None:
            gap_percent = _number(evidence, "gap_percent")
            gap = gap_percent / 100.0 if gap_percent is not None else None
        if retention is None or gap is None or gap == 0:
            return {"gap": gap, "retention": retention}, 50.0, ["GAP_RETENTION_NOT_APPLICABLE"]
        aligned_gap = gap > 0 if is_long else gap < 0
        retained_score = clamp(retention * 100.0)
        score = retained_score if aligned_gap else 100.0 - retained_score
        return {"gap": gap, "retention": retention}, score, ["GAP_RETAINED_AND_ALIGNED" if score > 55 else "GAP_FAILED_OR_CONFLICTING"]

    if factor == "price_action_quality":
        close_location = _number(evidence, "opening_range_close_location", "close_location")
        upper_wick = _number(evidence, "upper_wick_ratio")
        lower_wick = _number(evidence, "lower_wick_ratio")
        if close_location is None:
            location_score = 50.0
        else:
            location_score = clamp(close_location * 100.0 if is_long else (1.0 - close_location) * 100.0)
        adverse_wick = upper_wick if is_long else lower_wick
        wick_score = clamp((1.0 - adverse_wick) * 100.0) if adverse_wick is not None else 50.0
        score = 0.75 * location_score + 0.25 * wick_score
        return {"close_location": close_location, "adverse_wick_ratio": adverse_wick}, score, ["PRICE_ACTION_CLEAN" if score >= 60 else "POOR_CLOSE_LOCATION"]

    if factor == "liquidity_and_spread":
        spread = _number(evidence, "spread_bps", "current_spread_bps")
        maximum = finite_float((config.get("risk_gates") or {}).get("maximum_spread_bps")) if isinstance(config.get("risk_gates"), Mapping) else None
        maximum = maximum or 30.0
        score = clamp(100.0 * (1.0 - (spread or 0.0) / maximum)) if spread is not None else 50.0
        return spread, score, ["OPENING_SPREAD_ACCEPTABLE" if score > 0 else "WIDE_SPREAD" if spread is not None else "SPREAD_UNAVAILABLE"]

    if factor == "extension_quality":
        extension = abs(_number(evidence, "distance_from_vwap_atr", "entry_extension_atr") or 0.0)
        maximum = finite_float((config.get("risk_gates") or {}).get("maximum_entry_extension_atr")) if isinstance(config.get("risk_gates"), Mapping) else None
        maximum = maximum or 0.35
        score = clamp(100.0 * (1.0 - extension / maximum))
        return extension, score, ["ENTRY_NOT_EXTENDED" if extension <= maximum else "EXCESSIVE_EXTENSION"]

    return evidence.get(factor), 50.0, ["NEUTRAL_UNMAPPED_FACTOR"]


def _penalty_applies(name: str, direction: str, evidence: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    is_long = direction == "LONG"
    setup_types = {str(item).upper() for item in evidence.get("setup_types", [])}
    primary_setup = str(evidence.get("setup_type", evidence.get("primary_setup", ""))).upper()
    if primary_setup:
        setup_types.add(primary_setup)
    reason_codes = {str(item).upper() for item in evidence.get("reason_codes", [])}
    explicit = evidence.get("penalty_flags", {})
    if isinstance(explicit, Mapping) and name in explicit:
        value = explicit[name]
        if isinstance(value, Mapping):
            return bool(value.get(direction.lower(), value.get(direction, False)))
        return bool(value)
    if evidence.get(name) is not None:
        return bool(evidence.get(name))
    upper = name.upper()
    if name == "failed_breakout":
        return is_long and ("FAILED_BREAKOUT" in setup_types or "FAILED_BREAKOUT" in reason_codes)
    if name == "failed_breakdown":
        return not is_long and ("FAILED_BREAKDOWN" in setup_types or "FAILED_BREAKDOWN" in reason_codes)
    if name == "failed_gap":
        failed = bool({"FAILED_GAP_UP", "FAILED_GAP_DOWN", "FAILED_GAP"} & (setup_types | reason_codes))
        gap = _number(evidence, "gap_return")
        return failed and (gap is None or (gap > 0) == is_long)
    if name == "weak_opening_volume":
        value = _number(evidence, "opening_relative_volume")
        minimum = finite_float((config.get("risk_gates") or {}).get("minimum_opening_relative_volume")) if isinstance(config.get("risk_gates"), Mapping) else None
        return value is not None and value < (minimum or 1.0)
    if name in {"market_conflict", "sector_conflict"}:
        key = "market_direction" if name == "market_conflict" else "sector_direction"
        raw = _direction_value(evidence, key)
        return raw is not None and (raw < 0 if is_long else raw > 0)
    if name == "excessive_extension":
        extension = abs(_number(evidence, "distance_from_vwap_atr", "entry_extension_atr") or 0.0)
        maximum = finite_float((config.get("risk_gates") or {}).get("maximum_entry_extension_atr")) if isinstance(config.get("risk_gates"), Mapping) else None
        return extension > (maximum or 0.35)
    if name == "poor_close_location":
        close = _number(evidence, "opening_range_close_location", "close_location")
        return close is not None and (close < 0.25 if is_long else close > 0.75)
    if name == "wide_spread":
        spread = _number(evidence, "spread_bps", "current_spread_bps")
        maximum = finite_float((config.get("risk_gates") or {}).get("maximum_spread_bps")) if isinstance(config.get("risk_gates"), Mapping) else None
        return spread is not None and spread > (maximum or 30.0)
    return upper in reason_codes


def score_opening(
    evidence: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Compute weighted opening evidence and explicit soft penalties."""

    config = config or {}
    section = resolve_section(config, "opening_scoring")
    raw_weights = section.get("weights", DEFAULT_WEIGHTS) if isinstance(section, Mapping) else DEFAULT_WEIGHTS
    weights = validate_weights(raw_weights, 100.0, "opening scoring")
    raw_penalties = section.get("penalties", DEFAULT_PENALTIES) if isinstance(section, Mapping) else DEFAULT_PENALTIES
    penalties = {str(key): float(value) for key, value in raw_penalties.items() if finite_float(value) is not None}
    breakdowns: dict[str, dict[str, dict[str, Any]]] = {"LONG": {}, "SHORT": {}}
    penalty_breakdowns: dict[str, dict[str, dict[str, Any]]] = {"LONG": {}, "SHORT": {}}
    gross_scores: dict[str, float] = {}
    net_scores: dict[str, float] = {}
    for direction in ("LONG", "SHORT"):
        for factor, weight in weights.items():
            raw, normalized, reasons = _factor_value_and_score(factor, direction, evidence, config)
            breakdowns[direction][factor] = factor_record(raw, normalized, weight, reasons)
        gross = sum(item["weighted_contribution"] for item in breakdowns[direction].values())
        assessed = 0.0
        for name, amount in penalties.items():
            applied = _penalty_applies(name, direction, evidence, config)
            penalty_breakdowns[direction][name] = {
                "configured_penalty": round(amount, 4),
                "applied": applied,
                "deduction": round(amount if applied else 0.0, 4),
                "reason_code": name.upper() if applied else None,
            }
            if applied:
                assessed += amount
        gross_scores[direction] = round(gross, 4)
        net_scores[direction] = round(clamp(gross - assessed), 4)

    conflict_threshold = finite_float(
        (config.get("risk_gates") or {}).get("directional_conflict_max_difference")
        if isinstance(config.get("risk_gates"), Mapping)
        else None
    ) or 8.0
    difference = abs(net_scores["LONG"] - net_scores["SHORT"])
    direction = "WATCH" if difference <= conflict_threshold else "LONG" if net_scores["LONG"] > net_scores["SHORT"] else "SHORT"
    selected = direction if direction in {"LONG", "SHORT"} else ("LONG" if net_scores["LONG"] >= net_scores["SHORT"] else "SHORT")
    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    applied_reasons = [
        detail["reason_code"]
        for detail in penalty_breakdowns[selected].values()
        if detail["applied"] and detail["reason_code"]
    ]
    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": cutoff.isoformat(),
        "score_type": "OPENING_CONFIRMATION",
        "opening_score": net_scores[selected],
        "score": net_scores[selected],
        "gross_opening_score": gross_scores[selected],
        "long_score": net_scores["LONG"],
        "short_score": net_scores["SHORT"],
        "long_gross_score": gross_scores["LONG"],
        "short_gross_score": gross_scores["SHORT"],
        "direction": direction,
        "directional_conflict": direction == "WATCH",
        "directional_score_difference": round(difference, 4),
        "factor_breakdown": breakdowns[selected],
        "long_factor_breakdown": breakdowns["LONG"],
        "short_factor_breakdown": breakdowns["SHORT"],
        "penalty_breakdown": penalty_breakdowns[selected],
        "long_penalty_breakdown": penalty_breakdowns["LONG"],
        "short_penalty_breakdown": penalty_breakdowns["SHORT"],
        "reason_codes": (["DIRECTIONAL_CONFLICT"] if direction == "WATCH" else [f"{direction}_OPENING_EVIDENCE_DOMINANT"]) + sorted(applied_reasons),
    }


compute_opening_score = score_opening


__all__ = [
    "DEFAULT_PENALTIES",
    "DEFAULT_WEIGHTS",
    "compute_opening_score",
    "score_opening",
]
