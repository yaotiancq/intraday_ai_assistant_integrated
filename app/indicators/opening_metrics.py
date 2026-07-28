"""Five- and fifteen-minute opening metrics with strict evidence cutoffs."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Iterable, Mapping

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, parse_cutoff, select_completed_bars
from app.indicators.price_action import compute_price_action_metrics
from app.indicators.relative_strength import compute_relative_strength
from app.indicators.volume_metrics import compute_opening_volume_metrics
from app.indicators.vwap import compute_regular_session_vwap


def _distance(price: float | None, level: float | None) -> float | None:
    return price - level if price is not None and level is not None else None


def _direction(value: float | None, tolerance: float = 0.0) -> str:
    if value is None:
        return "UNKNOWN"
    if value > tolerance:
        return "UP"
    if value < -tolerance:
        return "DOWN"
    return "FLAT"


def compute_opening_metrics(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    atr: float | None = None,
    previous_close: float | None = None,
    previous_day_high: float | None = None,
    previous_day_low: float | None = None,
    premarket_high: float | None = None,
    premarket_low: float | None = None,
    expected_opening_volume: float | list[float] | tuple[float, ...] | None = None,
    expected_bar_volume: float | list[float] | tuple[float, ...] | None = None,
    benchmark_bars: Mapping[str, Any] | None = None,
    sector_symbol: str | None = None,
    industry_symbol: str | None = None,
    initial_range_minutes: int = 5,
    session_open: str | time | datetime = "09:30",
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Calculate opening features using only bars complete by ``evidence_cutoff``.

    The opening range covers all evidence available at the stage.  The separate
    ``initial_5m_*`` fields preserve the first five-minute range for 09:45
    breakout/breakdown classification.
    """

    source_bars = [dict(bar) for bar in bars or ()]
    selected = select_completed_bars(
        source_bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_open,
        timezone=timezone,
    )
    valid = [
        bar
        for bar in selected
        if all(finite_float(bar.get(key)) is not None for key in ("open", "high", "low", "close"))
    ]
    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    opening = parse_cutoff(trade_date, session_open, timezone)
    elapsed_minutes = max(0, int((cutoff - opening).total_seconds() // 60))
    stage = "OPENING_5M" if elapsed_minutes <= 5 else "OPENING_15M" if elapsed_minutes <= 15 else "OPENING"

    base: dict[str, Any] = {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": cutoff.isoformat(),
        "stage": stage,
        "bar_label_convention": "START",
        "cutoff_inclusive": False,
        "completed_bar_count": len(selected),
        "expected_bar_count": elapsed_minutes,
        "bar_coverage": len(selected) / elapsed_minutes if elapsed_minutes else None,
    }
    if not valid:
        return {
            **base,
            "opening_range_high": None,
            "opening_range_low": None,
            "opening_range_width": None,
            "reason_codes": ["INSUFFICIENT_OPENING_BARS"],
        }

    opening_high = max(float(bar["high"]) for bar in valid)
    opening_low = min(float(bar["low"]) for bar in valid)
    opening_width = opening_high - opening_low
    opening_price = float(valid[0]["open"])
    last_close = float(valid[-1]["close"])
    atr_value = finite_float(atr)
    previous_close_value = finite_float(previous_close)
    initial = valid[: max(1, int(initial_range_minutes))]
    initial_high = max(float(bar["high"]) for bar in initial)
    initial_low = min(float(bar["low"]) for bar in initial)

    volume = compute_opening_volume_metrics(
        source_bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        expected_opening_volume=expected_opening_volume,
        expected_bar_volume=expected_bar_volume,
        session_start=session_open,
        timezone=timezone,
    )
    vwap = compute_regular_session_vwap(
        source_bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        current_price=last_close,
        session_open=session_open,
        timezone=timezone,
    )
    price_action = compute_price_action_metrics(
        source_bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_open,
        atr=atr_value,
        breakout_level=initial_high if len(valid) > len(initial) else finite_float(premarket_high),
        breakdown_level=initial_low if len(valid) > len(initial) else finite_float(premarket_low),
        timezone=timezone,
    )

    gap = opening_price - previous_close_value if previous_close_value is not None else None
    gap_return = gap / previous_close_value if gap is not None and previous_close_value else None
    retained = (last_close - previous_close_value) / gap if gap not in (None, 0.0) and previous_close_value is not None else None
    close_location = (last_close - opening_low) / opening_width if opening_width > 0 else 0.5

    benchmarks = benchmark_bars or {}
    relative = compute_relative_strength(
        source_bars,
        benchmarks,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        window_start=session_open,
        window_name=stage,
        timezone=timezone,
    ) if benchmarks else {
        "stock_return": last_close / opening_price - 1.0 if opening_price > 0 else None,
        "comparisons": {},
        "mean_excess_return": None,
        "reason_codes": [],
    }

    comparison = relative.get("comparisons", {})
    rs_spy = (comparison.get("SPY") or {}).get("excess_return")
    rs_qqq = (comparison.get("QQQ") or {}).get("excess_return")
    sector_key = str(sector_symbol or "").upper()
    industry_key = str(industry_symbol or "").upper()
    rs_sector = (comparison.get(sector_key) or {}).get("excess_return") if sector_key else None
    rs_industry = (comparison.get(industry_key) or {}).get("excess_return") if industry_key else None

    pre_high = finite_float(premarket_high)
    pre_low = finite_float(premarket_low)
    prev_high = finite_float(previous_day_high)
    prev_low = finite_float(previous_day_low)
    vwap_value = vwap["regular_session_vwap"]
    price_vwap_distance = _distance(last_close, vwap_value)
    breakout_distance = max(0.0, last_close - initial_high)
    breakdown_distance = max(0.0, initial_low - last_close)
    breakout_distance_atr = max(breakout_distance, breakdown_distance) / atr_value if atr_value else None

    # A pullback can only exist after the high/low was established before the last bar.
    high_index = next(index for index, bar in enumerate(valid) if float(bar["high"]) == opening_high)
    low_index = next(index for index, bar in enumerate(valid) if float(bar["low"]) == opening_low)
    long_pullback = (opening_high - last_close) / opening_width if opening_width and high_index < len(valid) - 1 else 0.0
    short_pullback = (last_close - opening_low) / opening_width if opening_width and low_index < len(valid) - 1 else 0.0

    reason_codes = list(price_action.get("reason_codes", [])) + list(volume.get("reason_codes", []))
    if last_close > initial_high and len(valid) > len(initial):
        reason_codes.append("ABOVE_INITIAL_OPENING_RANGE")
    if last_close < initial_low and len(valid) > len(initial):
        reason_codes.append("BELOW_INITIAL_OPENING_RANGE")
    if retained is not None and retained >= 0.5:
        reason_codes.append("GAP_RETAINED")
    elif retained is not None and retained <= 0:
        reason_codes.append("GAP_FAILED")

    return {
        **base,
        "opening_range_high": opening_high,
        "opening_range_low": opening_low,
        "opening_range_width": opening_width,
        "opening_range_atr_ratio": opening_width / atr_value if atr_value else None,
        "opening_range_close_location": close_location,
        "initial_5m_high": initial_high,
        "initial_5m_low": initial_low,
        "initial_5m_width": initial_high - initial_low,
        "opening_price": opening_price,
        "current_price": last_close,
        "last_price": last_close,
        "opening_return": last_close / opening_price - 1.0 if opening_price > 0 else None,
        "opening_cumulative_volume": volume["cumulative_volume"],
        "opening_relative_volume": volume["relative_volume"],
        "volume_acceleration": volume["volume_acceleration"],
        "regular_session_vwap": vwap_value,
        "vwap": vwap_value,
        "price_relative_to_vwap": vwap["price_relative_to_vwap"],
        "vwap_slope": vwap["vwap_slope"],
        "distance_from_vwap": price_vwap_distance,
        "distance_from_vwap_atr": price_vwap_distance / atr_value if price_vwap_distance is not None and atr_value else None,
        "first_bar_return": price_action.get("first_bar_return"),
        "gap_return": gap_return,
        "gap_percent": gap_return * 100.0 if gap_return is not None else None,
        "gap_retention": retained,
        "gap_retention_percent": retained * 100.0 if retained is not None else None,
        "distance_from_premarket_high": _distance(last_close, pre_high),
        "distance_from_premarket_low": _distance(last_close, pre_low),
        "distance_from_previous_day_high": _distance(last_close, prev_high),
        "distance_from_previous_day_low": _distance(last_close, prev_low),
        "relative_strength_vs_spy": rs_spy,
        "relative_strength_vs_qqq": rs_qqq,
        "relative_strength_vs_sector": rs_sector,
        "relative_strength_vs_industry": rs_industry,
        "mean_relative_strength": relative.get("mean_excess_return"),
        "realized_volatility": price_action.get("realized_volatility"),
        "upper_wick_ratio": price_action.get("upper_wick_ratio"),
        "lower_wick_ratio": price_action.get("lower_wick_ratio"),
        "close_location_value": price_action.get("close_location_value"),
        "higher_highs": price_action.get("higher_highs"),
        "higher_lows": price_action.get("higher_lows"),
        "lower_highs": price_action.get("lower_highs"),
        "lower_lows": price_action.get("lower_lows"),
        "bullish_sequence_ratio": price_action.get("bullish_sequence_ratio"),
        "bearish_sequence_ratio": price_action.get("bearish_sequence_ratio"),
        "breakout_distance": breakout_distance,
        "breakdown_distance": breakdown_distance,
        "breakout_distance_atr": breakout_distance_atr,
        "pullback_depth": min(long_pullback, short_pullback),
        "long_pullback_depth": long_pullback,
        "short_pullback_depth": short_pullback,
        "crossed_above_vwap": price_action.get("crossed_above_vwap", False),
        "crossed_below_vwap": price_action.get("crossed_below_vwap", False),
        "market_direction": _direction((comparison.get("SPY") or {}).get("benchmark_return")),
        "sector_direction": _direction(
            (comparison.get(sector_key) or {}).get("benchmark_return") if sector_key else None
        ),
        "relative_strength": relative,
        "volume_metrics": volume,
        "vwap_metrics": vwap,
        "price_action_metrics": price_action,
        "reason_codes": sorted(set(reason_codes + list(relative.get("reason_codes", [])))),
    }


calculate_opening_metrics = compute_opening_metrics


__all__ = ["calculate_opening_metrics", "compute_opening_metrics"]
