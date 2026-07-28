"""Explicit, testable classification of supported opening setup types."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Mapping

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, parse_cutoff


SUPPORTED_SETUP_TYPES = (
    "OPENING_DRIVE_LONG",
    "OPENING_DRIVE_SHORT",
    "OPENING_RANGE_BREAKOUT",
    "OPENING_RANGE_BREAKDOWN",
    "GAP_AND_GO_LONG",
    "GAP_AND_GO_SHORT",
    "PREMARKET_HIGH_BREAKOUT",
    "PREMARKET_LOW_BREAKDOWN",
    "VWAP_RECLAIM",
    "VWAP_REJECTION",
    "FIRST_PULLBACK_LONG",
    "FIRST_PULLBACK_SHORT",
    "FAILED_BREAKOUT",
    "FAILED_BREAKDOWN",
    "FAILED_GAP_UP",
    "FAILED_GAP_DOWN",
    "NO_VALID_SETUP",
)

FAILURE_SETUPS = frozenset({"FAILED_BREAKOUT", "FAILED_BREAKDOWN", "FAILED_GAP_UP", "FAILED_GAP_DOWN"})
LONG_SETUPS = frozenset({
    "OPENING_DRIVE_LONG",
    "OPENING_RANGE_BREAKOUT",
    "GAP_AND_GO_LONG",
    "PREMARKET_HIGH_BREAKOUT",
    "VWAP_RECLAIM",
    "FIRST_PULLBACK_LONG",
})
SHORT_SETUPS = frozenset({
    "OPENING_DRIVE_SHORT",
    "OPENING_RANGE_BREAKDOWN",
    "GAP_AND_GO_SHORT",
    "PREMARKET_LOW_BREAKDOWN",
    "VWAP_REJECTION",
    "FIRST_PULLBACK_SHORT",
})


def _number(metrics: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = finite_float(metrics.get(key))
        if value is not None:
            return value
    return None


def _setting(config: Mapping[str, Any], key: str, default: float) -> float:
    rules = config.get("setup_rules", {}) if isinstance(config.get("setup_rules"), Mapping) else {}
    return finite_float(rules.get(key)) or default


def classify_setup(
    metrics: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    preferred_direction: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Classify every satisfied rule and choose a deterministic primary setup."""

    config = config or {}
    current = _number(metrics, "current_price", "last_price", "last_close")
    vwap = _number(metrics, "regular_session_vwap", "vwap")
    initial_high = _number(metrics, "initial_5m_high", "opening_range_high")
    initial_low = _number(metrics, "initial_5m_low", "opening_range_low")
    full_high = _number(metrics, "opening_range_high", "session_high")
    full_low = _number(metrics, "opening_range_low", "session_low")
    premarket_high = _number(metrics, "premarket_high")
    premarket_low = _number(metrics, "premarket_low")
    if premarket_high is None:
        distance = _number(metrics, "distance_from_premarket_high")
        premarket_high = current - distance if current is not None and distance is not None else None
    if premarket_low is None:
        distance = _number(metrics, "distance_from_premarket_low")
        premarket_low = current - distance if current is not None and distance is not None else None
    opening_return = _number(metrics, "opening_return") or 0.0
    first_return = _number(metrics, "first_bar_return") or 0.0
    close_location = _number(metrics, "opening_range_close_location", "close_location")
    long_sequence = _number(metrics, "bullish_sequence_ratio") or 0.0
    short_sequence = _number(metrics, "bearish_sequence_ratio") or 0.0
    rvol = _number(metrics, "opening_relative_volume")
    gap = _number(metrics, "gap_return")
    if gap is None:
        gap_pct = _number(metrics, "gap_percent")
        gap = gap_pct / 100.0 if gap_pct is not None else 0.0
    retention = _number(metrics, "gap_retention")
    if retention is None:
        retention_pct = _number(metrics, "gap_retention_percent")
        retention = retention_pct / 100.0 if retention_pct is not None else None

    volume_ok = rvol is None or rvol >= _setting(config, "minimum_setup_relative_volume", 1.0)
    above_vwap = current is not None and vwap is not None and current > vwap
    below_vwap = current is not None and vwap is not None and current < vwap
    drive_return = _setting(config, "minimum_opening_drive_return", 0.0025)
    pullback_min = _setting(config, "minimum_pullback_depth", 0.05)
    pullback_max = _setting(config, "maximum_pullback_depth", 0.50)
    long_pullback = _number(metrics, "long_pullback_depth", "pullback_depth")
    short_pullback = _number(metrics, "short_pullback_depth", "pullback_depth")

    conditions: dict[str, dict[str, bool]] = {
        "FAILED_BREAKOUT": {
            "traded_above_initial_range": full_high is not None and initial_high is not None and full_high > initial_high,
            "closed_back_at_or_below_range": current is not None and initial_high is not None and current <= initial_high,
        },
        "FAILED_BREAKDOWN": {
            "traded_below_initial_range": full_low is not None and initial_low is not None and full_low < initial_low,
            "closed_back_at_or_above_range": current is not None and initial_low is not None and current >= initial_low,
        },
        "FAILED_GAP_UP": {
            "gap_up": bool(gap and gap > 0),
            "gap_not_retained": retention is not None and retention <= 0,
        },
        "FAILED_GAP_DOWN": {
            "gap_down": bool(gap and gap < 0),
            "gap_not_retained": retention is not None and retention <= 0,
        },
        "OPENING_DRIVE_LONG": {
            "positive_drive_return": max(first_return, opening_return) >= drive_return,
            "above_vwap": above_vwap,
            "strong_close_location": close_location is None or close_location >= 0.70,
            "volume_confirmed": volume_ok,
        },
        "OPENING_DRIVE_SHORT": {
            "negative_drive_return": min(first_return, opening_return) <= -drive_return,
            "below_vwap": below_vwap,
            "weak_close_location": close_location is None or close_location <= 0.30,
            "volume_confirmed": volume_ok,
        },
        "OPENING_RANGE_BREAKOUT": {
            "above_initial_range": current is not None and initial_high is not None and current > initial_high,
            "above_vwap": above_vwap,
            "volume_confirmed": volume_ok,
        },
        "OPENING_RANGE_BREAKDOWN": {
            "below_initial_range": current is not None and initial_low is not None and current < initial_low,
            "below_vwap": below_vwap,
            "volume_confirmed": volume_ok,
        },
        "GAP_AND_GO_LONG": {
            "gap_up": bool(gap and gap > 0),
            "gap_retained": retention is not None and retention >= 0.75,
            "above_vwap": above_vwap,
            "positive_opening_return": opening_return > 0,
        },
        "GAP_AND_GO_SHORT": {
            "gap_down": bool(gap and gap < 0),
            "gap_retained": retention is not None and retention >= 0.75,
            "below_vwap": below_vwap,
            "negative_opening_return": opening_return < 0,
        },
        "PREMARKET_HIGH_BREAKOUT": {
            "above_premarket_high": current is not None and premarket_high is not None and current > premarket_high,
            "above_vwap": above_vwap,
            "volume_confirmed": volume_ok,
        },
        "PREMARKET_LOW_BREAKDOWN": {
            "below_premarket_low": current is not None and premarket_low is not None and current < premarket_low,
            "below_vwap": below_vwap,
            "volume_confirmed": volume_ok,
        },
        "VWAP_RECLAIM": {
            "crossed_above_vwap": bool(metrics.get("crossed_above_vwap")),
            "closed_above_vwap": above_vwap,
            "positive_structure": opening_return > 0 or long_sequence >= 0.5,
        },
        "VWAP_REJECTION": {
            "crossed_below_vwap": bool(metrics.get("crossed_below_vwap")),
            "closed_below_vwap": below_vwap,
            "negative_structure": opening_return < 0 or short_sequence >= 0.5,
        },
        "FIRST_PULLBACK_LONG": {
            "bullish_structure": long_sequence >= 0.5 or opening_return > 0,
            "valid_pullback_depth": long_pullback is not None and pullback_min <= long_pullback <= pullback_max,
            "holds_vwap": above_vwap,
            "volume_confirmed": volume_ok,
        },
        "FIRST_PULLBACK_SHORT": {
            "bearish_structure": short_sequence >= 0.5 or opening_return < 0,
            "valid_pullback_depth": short_pullback is not None and pullback_min <= short_pullback <= pullback_max,
            "holds_below_vwap": below_vwap,
            "volume_confirmed": volume_ok,
        },
    }

    explicit_types = {str(item).upper() for item in metrics.get("setup_types", [])}
    explicit_primary = str(metrics.get("setup_type", "")).upper()
    if explicit_primary in SUPPORTED_SETUP_TYPES:
        explicit_types.add(explicit_primary)
    matched = [name for name, checks in conditions.items() if checks and all(checks.values())]
    explicit_flag_names = {
        "FAILED_BREAKOUT": "failed_breakout",
        "FAILED_BREAKDOWN": "failed_breakdown",
        "FAILED_GAP_UP": "failed_gap_up",
        "FAILED_GAP_DOWN": "failed_gap_down",
    }
    for name, flag in explicit_flag_names.items():
        if metrics.get(flag) is True and name not in matched:
            matched.append(name)
    if metrics.get("failed_gap") is True:
        inferred_failed_gap = "FAILED_GAP_DOWN" if gap and gap < 0 else "FAILED_GAP_UP"
        if inferred_failed_gap not in matched:
            matched.append(inferred_failed_gap)
    for name in SUPPORTED_SETUP_TYPES:
        if name in explicit_types and name not in matched and name != "NO_VALID_SETUP":
            matched.append(name)

    failures = [name for name in matched if name in FAILURE_SETUPS]
    preferred = str(preferred_direction or "").upper()
    if failures:
        # Failed structural theses always outrank positive setup labels.
        failure_priority = ("FAILED_BREAKOUT", "FAILED_BREAKDOWN", "FAILED_GAP_UP", "FAILED_GAP_DOWN")
        primary = next(name for name in failure_priority if name in failures)
    else:
        positive = [name for name in matched if name not in FAILURE_SETUPS]
        if preferred == "LONG":
            preferred_matches = [name for name in positive if name in LONG_SETUPS]
        elif preferred == "SHORT":
            preferred_matches = [name for name in positive if name in SHORT_SETUPS]
        else:
            preferred_matches = positive
        priority = (
            "OPENING_RANGE_BREAKOUT", "OPENING_RANGE_BREAKDOWN",
            "PREMARKET_HIGH_BREAKOUT", "PREMARKET_LOW_BREAKDOWN",
            "VWAP_RECLAIM", "VWAP_REJECTION",
            "GAP_AND_GO_LONG", "GAP_AND_GO_SHORT",
            "FIRST_PULLBACK_LONG", "FIRST_PULLBACK_SHORT",
            "OPENING_DRIVE_LONG", "OPENING_DRIVE_SHORT",
        )
        eligible = preferred_matches or positive
        primary = next((name for name in priority if name in eligible), "NO_VALID_SETUP")

    long_matched = [name for name in matched if name in LONG_SETUPS]
    short_matched = [name for name in matched if name in SHORT_SETUPS]
    if primary in LONG_SETUPS:
        direction = "LONG"
    elif primary in SHORT_SETUPS:
        direction = "SHORT"
    elif primary in {"FAILED_BREAKOUT", "FAILED_GAP_UP"}:
        direction = "LONG"
    elif primary in {"FAILED_BREAKDOWN", "FAILED_GAP_DOWN"}:
        direction = "SHORT"
    else:
        direction = "WATCH" if long_matched and short_matched else "NONE"
    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    reason_codes = failures or ([f"SETUP_CLASSIFIED_{primary}"] if primary != "NO_VALID_SETUP" else ["NO_VALID_SETUP"])
    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": cutoff.isoformat(),
        "setup_type": primary,
        "primary_setup": primary,
        "setup_types": matched or ["NO_VALID_SETUP"],
        "direction": direction,
        "long_setups": long_matched,
        "short_setups": short_matched,
        "directional_conflict": bool(long_matched and short_matched),
        "failure_override": bool(failures),
        "failed_setups": failures,
        "conditions": conditions,
        "reason_codes": reason_codes,
    }


classify_opening_setup = classify_setup


__all__ = [
    "FAILURE_SETUPS",
    "LONG_SETUPS",
    "SHORT_SETUPS",
    "SUPPORTED_SETUP_TYPES",
    "classify_opening_setup",
    "classify_setup",
]
