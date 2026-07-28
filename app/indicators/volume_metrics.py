"""Deterministic point-in-time volume and relative-volume features."""

from __future__ import annotations

from datetime import date, datetime, time
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, select_completed_bars


def _expected_value(value: float | Sequence[float] | None) -> float | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        clean = [number for item in value if (number := finite_float(item)) is not None and number > 0]
        return mean(clean) if clean else None
    result = finite_float(value)
    return result if result is not None and result > 0 else None


def _bar_dollar_volume(bar: Mapping[str, Any]) -> float:
    volume = finite_float(bar.get("volume")) or 0.0
    turnover = finite_float(bar.get("turnover"))
    if turnover is not None and turnover >= 0:
        return turnover
    high = finite_float(bar.get("high"))
    low = finite_float(bar.get("low"))
    close = finite_float(bar.get("close"))
    if high is not None and low is not None and close is not None:
        price = (high + low + close) / 3.0
    else:
        price = close or finite_float(bar.get("open")) or 0.0
    return max(0.0, price * volume)


def compute_volume_metrics(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    session_start: str | time | datetime,
    expected_cumulative_volume: float | Sequence[float] | None = None,
    expected_bar_volume: float | Sequence[float] | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    selected = select_completed_bars(
        bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_start,
        timezone=timezone,
    )
    volumes = [max(0.0, finite_float(bar.get("volume")) or 0.0) for bar in selected]
    cumulative = sum(volumes)
    expected_total = _expected_value(expected_cumulative_volume)
    expected_each = _expected_value(expected_bar_volume)
    if expected_total is None and expected_each is not None:
        expected_total = expected_each * len(selected)
    relative = cumulative / expected_total if expected_total else None

    midpoint = max(1, len(volumes) // 2) if volumes else 0
    first_rate = mean(volumes[:midpoint]) if midpoint else None
    second_rate = mean(volumes[midpoint:]) if len(volumes) > midpoint else None
    acceleration = second_rate / first_rate if first_rate and second_rate is not None else None
    latest_ratio = volumes[-1] / expected_each if volumes and expected_each else None
    zero_count = sum(1 for volume in volumes if volume == 0)
    reason_codes: list[str] = []
    if relative is None:
        reason_codes.append("RELATIVE_VOLUME_BASELINE_UNAVAILABLE")
    elif relative >= 1.5:
        reason_codes.append("STRONG_RELATIVE_VOLUME")
    elif relative >= 1.0:
        reason_codes.append("ABOVE_EXPECTED_VOLUME")
    else:
        reason_codes.append("BELOW_EXPECTED_VOLUME")
    if acceleration is not None:
        reason_codes.append("VOLUME_ACCELERATING" if acceleration > 1.1 else "VOLUME_NOT_ACCELERATING")
    if zero_count:
        reason_codes.append("ZERO_VOLUME_BARS_PRESENT")

    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": str(evidence_cutoff),
        "bar_count": len(selected),
        "cumulative_volume": cumulative,
        "dollar_volume": sum(_bar_dollar_volume(bar) for bar in selected),
        "expected_cumulative_volume": expected_total,
        "relative_volume": relative,
        "opening_relative_volume": relative,
        "volume_acceleration": acceleration,
        "latest_bar_relative_volume": latest_ratio,
        "zero_volume_bar_count": zero_count,
        "reason_codes": reason_codes,
    }


def compute_premarket_volume_metrics(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    expected_premarket_volume: float | Sequence[float] | None = None,
    expected_bar_volume: float | Sequence[float] | None = None,
    session_start: str | time | datetime = "04:00",
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    result = compute_volume_metrics(
        bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_start,
        expected_cumulative_volume=expected_premarket_volume,
        expected_bar_volume=expected_bar_volume,
        timezone=timezone,
    )
    result["premarket_volume"] = result["cumulative_volume"]
    result["premarket_dollar_volume"] = result["dollar_volume"]
    result["premarket_relative_volume"] = result["relative_volume"]
    return result


def compute_opening_volume_metrics(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    expected_opening_volume: float | Sequence[float] | None = None,
    expected_bar_volume: float | Sequence[float] | None = None,
    session_start: str | time | datetime = "09:30",
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    return compute_volume_metrics(
        bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_start,
        expected_cumulative_volume=expected_opening_volume,
        expected_bar_volume=expected_bar_volume,
        timezone=timezone,
    )


premarket_volume_metrics = compute_premarket_volume_metrics
opening_volume_metrics = compute_opening_volume_metrics


__all__ = [
    "compute_opening_volume_metrics",
    "compute_premarket_volume_metrics",
    "compute_volume_metrics",
    "opening_volume_metrics",
    "premarket_volume_metrics",
]
