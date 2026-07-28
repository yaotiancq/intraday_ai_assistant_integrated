"""Regular-session VWAP calculations with an explicit 09:30 ET reset."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Iterable, Mapping

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, select_completed_bars


def _price_and_notional(bar: Mapping[str, Any]) -> tuple[float | None, float | None]:
    volume = finite_float(bar.get("volume"))
    if volume is None or volume <= 0:
        return None, None
    turnover = finite_float(bar.get("turnover"))
    if turnover is not None and turnover > 0:
        return turnover / volume, turnover
    high = finite_float(bar.get("high"))
    low = finite_float(bar.get("low"))
    close = finite_float(bar.get("close"))
    if high is not None and low is not None and close is not None:
        price = (high + low + close) / 3.0
    else:
        price = close
    if price is None or price <= 0:
        return None, None
    return price, price * volume


def calculate_vwap(bars: Iterable[Mapping[str, Any]] | None) -> float | None:
    """Calculate VWAP over exactly the supplied bars."""

    total_volume = 0.0
    total_notional = 0.0
    for bar in bars or ():
        volume = finite_float(bar.get("volume"))
        _, notional = _price_and_notional(bar)
        if volume is None or volume <= 0 or notional is None:
            continue
        total_volume += volume
        total_notional += notional
    return total_notional / total_volume if total_volume > 0 else None


def cumulative_vwap(bars: Iterable[Mapping[str, Any]] | None) -> list[float | None]:
    total_volume = 0.0
    total_notional = 0.0
    output: list[float | None] = []
    for bar in bars or ():
        volume = finite_float(bar.get("volume"))
        _, notional = _price_and_notional(bar)
        if volume is not None and volume > 0 and notional is not None:
            total_volume += volume
            total_notional += notional
        output.append(total_notional / total_volume if total_volume > 0 else None)
    return output


def compute_regular_session_vwap(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    current_price: float | None = None,
    session_open: str | time | datetime = "09:30",
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Return regular-session VWAP state; premarket rows are excluded by design."""

    selected = select_completed_bars(
        bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_open,
        timezone=timezone,
    )
    path = cumulative_vwap(selected)
    value = path[-1] if path else None
    valid_path = [item for item in path if item is not None]
    slope = valid_path[-1] - valid_path[0] if len(valid_path) >= 2 else 0.0 if valid_path else None
    price = finite_float(current_price)
    if price is None and selected:
        price = finite_float(selected[-1].get("close"))
    distance = price - value if price is not None and value is not None else None
    distance_percent = distance / value if distance is not None and value else None
    if distance is None:
        state = "UNKNOWN"
    elif distance > 0:
        state = "ABOVE_VWAP"
    elif distance < 0:
        state = "BELOW_VWAP"
    else:
        state = "AT_VWAP"
    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": str(evidence_cutoff),
        "session_reset": "09:30",
        "bar_count": len(selected),
        "regular_session_vwap": value,
        "vwap": value,
        "vwap_slope": slope,
        "current_price": price,
        "price_relative_to_vwap": state,
        "distance_from_vwap": distance,
        "distance_from_vwap_percent": distance_percent,
        "reason_codes": [state] if state != "UNKNOWN" else ["VWAP_UNAVAILABLE"],
    }


def regular_session_vwap(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    session_open: str | time | datetime = "09:30",
    timezone: str = DEFAULT_TIMEZONE,
) -> float | None:
    """Convenience scalar form of :func:`compute_regular_session_vwap`."""

    return compute_regular_session_vwap(
        bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_open=session_open,
        timezone=timezone,
    )["regular_session_vwap"]


compute_vwap = calculate_vwap


__all__ = [
    "calculate_vwap",
    "compute_regular_session_vwap",
    "compute_vwap",
    "cumulative_vwap",
    "regular_session_vwap",
]
