"""Explainable opening price-action features from completed bars only."""

from __future__ import annotations

from datetime import date, datetime, time
import math
from statistics import pstdev
from typing import Any, Iterable, Mapping

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, select_completed_bars
from app.indicators.vwap import cumulative_vwap


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def compute_price_action_metrics(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    session_start: str | time | datetime = "09:30",
    atr: float | None = None,
    breakout_level: float | None = None,
    breakdown_level: float | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    selected = select_completed_bars(
        bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_start,
        timezone=timezone,
    )
    usable: list[dict[str, float]] = []
    for bar in selected:
        values = {key: finite_float(bar.get(key)) for key in ("open", "high", "low", "close")}
        if all(values[key] is not None for key in values):
            usable.append({key: float(values[key]) for key in values})  # type: ignore[arg-type]

    if not usable:
        return {
            "trade_date": str(trade_date)[:10],
            "evidence_cutoff": str(evidence_cutoff),
            "bar_count": 0,
            "reason_codes": ["PRICE_ACTION_UNAVAILABLE"],
        }

    first, last = usable[0], usable[-1]
    highs = [bar["high"] for bar in usable]
    lows = [bar["low"] for bar in usable]
    closes = [bar["close"] for bar in usable]
    higher_high_count = sum(highs[index] > highs[index - 1] for index in range(1, len(highs)))
    higher_low_count = sum(lows[index] > lows[index - 1] for index in range(1, len(lows)))
    lower_high_count = sum(highs[index] < highs[index - 1] for index in range(1, len(highs)))
    lower_low_count = sum(lows[index] < lows[index - 1] for index in range(1, len(lows)))
    comparisons = max(0, len(usable) - 1)

    last_range = max(0.0, last["high"] - last["low"])
    upper_wick = last["high"] - max(last["open"], last["close"])
    lower_wick = min(last["open"], last["close"]) - last["low"]
    upper_wick_ratio = _ratio(max(0.0, upper_wick), last_range)
    lower_wick_ratio = _ratio(max(0.0, lower_wick), last_range)
    last_clv = _ratio(last["close"] - last["low"], last_range)
    total_high, total_low = max(highs), min(lows)
    close_location = _ratio(last["close"] - total_low, total_high - total_low)

    returns = [closes[index] / closes[index - 1] - 1.0 for index in range(1, len(closes)) if closes[index - 1] > 0]
    realized_volatility = pstdev(returns) if len(returns) >= 2 else abs(returns[0]) if returns else 0.0
    first_bar_return = first["close"] / first["open"] - 1.0 if first["open"] > 0 else None

    atr_value = finite_float(atr)
    above_breakout = finite_float(breakout_level)
    below_breakdown = finite_float(breakdown_level)
    breakout_distance = max(0.0, last["close"] - above_breakout) if above_breakout is not None else None
    breakdown_distance = max(0.0, below_breakdown - last["close"]) if below_breakdown is not None else None
    signed_breakout_distance = None
    if above_breakout is not None and last["close"] >= above_breakout:
        signed_breakout_distance = last["close"] - above_breakout
    elif below_breakdown is not None and last["close"] <= below_breakdown:
        signed_breakout_distance = below_breakdown - last["close"]
    breakout_atr = signed_breakout_distance / atr_value if signed_breakout_distance is not None and atr_value else None

    peak_index = highs.index(total_high)
    trough_index = lows.index(total_low)
    long_pullback_depth = (total_high - last["close"]) / max(total_high - total_low, 1e-12)
    short_pullback_depth = (last["close"] - total_low) / max(total_high - total_low, 1e-12)

    # Detect actual VWAP crosses using the cumulative path, not a generated label.
    vwap_path = cumulative_vwap(selected)
    paired = [
        (finite_float(bar.get("close")), value)
        for bar, value in zip(selected, vwap_path)
        if finite_float(bar.get("close")) is not None and value is not None
    ]
    crossed_above = any(
        paired[index - 1][0] <= paired[index - 1][1] and paired[index][0] > paired[index][1]
        for index in range(1, len(paired))
    )
    crossed_below = any(
        paired[index - 1][0] >= paired[index - 1][1] and paired[index][0] < paired[index][1]
        for index in range(1, len(paired))
    )

    reason_codes: list[str] = []
    if comparisons and higher_high_count / comparisons >= 0.6 and higher_low_count / comparisons >= 0.6:
        reason_codes.append("BULLISH_SEQUENCE")
    if comparisons and lower_high_count / comparisons >= 0.6 and lower_low_count / comparisons >= 0.6:
        reason_codes.append("BEARISH_SEQUENCE")
    if close_location is not None and close_location >= 0.75:
        reason_codes.append("CLOSE_NEAR_RANGE_HIGH")
    elif close_location is not None and close_location <= 0.25:
        reason_codes.append("CLOSE_NEAR_RANGE_LOW")
    if crossed_above:
        reason_codes.append("CROSSED_ABOVE_VWAP")
    if crossed_below:
        reason_codes.append("CROSSED_BELOW_VWAP")

    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": str(evidence_cutoff),
        "bar_count": len(selected),
        "usable_bar_count": len(usable),
        "first_bar_return": first_bar_return,
        "realized_volatility": realized_volatility,
        "upper_wick_ratio": upper_wick_ratio,
        "lower_wick_ratio": lower_wick_ratio,
        "close_location_value": last_clv,
        "close_location": close_location,
        "higher_high_count": higher_high_count,
        "higher_low_count": higher_low_count,
        "lower_high_count": lower_high_count,
        "lower_low_count": lower_low_count,
        "higher_highs": comparisons > 0 and higher_high_count == comparisons,
        "higher_lows": comparisons > 0 and higher_low_count == comparisons,
        "lower_highs": comparisons > 0 and lower_high_count == comparisons,
        "lower_lows": comparisons > 0 and lower_low_count == comparisons,
        "bullish_sequence_ratio": min(higher_high_count, higher_low_count) / comparisons if comparisons else None,
        "bearish_sequence_ratio": min(lower_high_count, lower_low_count) / comparisons if comparisons else None,
        "breakout_distance": breakout_distance,
        "breakdown_distance": breakdown_distance,
        "breakout_distance_atr": breakout_atr,
        "long_pullback_depth": long_pullback_depth if peak_index < len(usable) - 1 else 0.0,
        "short_pullback_depth": short_pullback_depth if trough_index < len(usable) - 1 else 0.0,
        "crossed_above_vwap": crossed_above,
        "crossed_below_vwap": crossed_below,
        "last_open": last["open"],
        "last_high": last["high"],
        "last_low": last["low"],
        "last_close": last["close"],
        "session_high": total_high,
        "session_low": total_low,
        "reason_codes": reason_codes,
    }


price_action_metrics = compute_price_action_metrics


__all__ = ["compute_price_action_metrics", "price_action_metrics"]
