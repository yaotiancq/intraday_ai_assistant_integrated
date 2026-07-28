"""Point-in-time relative-strength calculations using explicit excess returns."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Iterable, Mapping

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, select_completed_bars


def excess_return(stock_return: float | None, benchmark_return: float | None) -> float | None:
    """Return ``stock return - benchmark return`` using decimal returns."""

    stock = finite_float(stock_return)
    benchmark = finite_float(benchmark_return)
    if stock is None or benchmark is None:
        return None
    return stock - benchmark


def calculate_window_return(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    window_start: str | time | datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> float | None:
    """Calculate an open-to-last-completed-close decimal return for a window."""

    selected = select_completed_bars(
        bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=window_start,
        timezone=timezone,
    )
    if not selected:
        return None
    first = finite_float(selected[0].get("open"))
    last = finite_float(selected[-1].get("close"))
    if first is None or last is None or first <= 0:
        return None
    return last / first - 1.0


def _coerce_return(
    value: Any,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    window_start: str | time | datetime | None,
    timezone: str,
) -> float | None:
    if isinstance(value, Mapping):
        direct = value.get("return")
        if direct is None:
            direct = value.get("return_decimal")
        if direct is None and value.get("return_percent") is not None:
            pct = finite_float(value.get("return_percent"))
            direct = pct / 100.0 if pct is not None else None
        if direct is not None:
            return finite_float(direct)
        bars = value.get("bars")
        if bars is not None:
            return calculate_window_return(
                bars,
                trade_date=trade_date,
                evidence_cutoff=evidence_cutoff,
                window_start=window_start,
                timezone=timezone,
            )
    if isinstance(value, (list, tuple)):
        return calculate_window_return(
            value,
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoff,
            window_start=window_start,
            timezone=timezone,
        )
    return finite_float(value)


def compute_relative_strength(
    stock: Any,
    benchmarks: Mapping[str, Any],
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    window_start: str | time | datetime | None = None,
    window_name: str = "CURRENT_WINDOW",
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Measure a stock against each benchmark without combining time windows.

    ``stock`` and benchmark values may be decimal returns, ``{"return": ...}``
    mappings, or bar lists.  Returned values are decimals (0.01 means 1%).
    """

    stock_return = _coerce_return(
        stock,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        window_start=window_start,
        timezone=timezone,
    )
    comparisons: dict[str, dict[str, Any]] = {}
    valid_excess: list[float] = []
    reason_codes: list[str] = []
    for raw_symbol in sorted(benchmarks):
        symbol = str(raw_symbol).strip().upper()
        benchmark_return = _coerce_return(
            benchmarks[raw_symbol],
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoff,
            window_start=window_start,
            timezone=timezone,
        )
        relative = excess_return(stock_return, benchmark_return)
        comparison_reasons: list[str] = []
        if relative is None:
            comparison_reasons.append(f"MISSING_{symbol}_RETURN")
        elif relative > 0:
            comparison_reasons.append(f"OUTPERFORMING_{symbol}")
            reason_codes.append(f"OUTPERFORMING_{symbol}")
            valid_excess.append(relative)
        elif relative < 0:
            comparison_reasons.append(f"UNDERPERFORMING_{symbol}")
            reason_codes.append(f"UNDERPERFORMING_{symbol}")
            valid_excess.append(relative)
        else:
            comparison_reasons.append(f"MATCHING_{symbol}")
            valid_excess.append(relative)
        comparisons[symbol] = {
            "benchmark_return": benchmark_return,
            "excess_return": relative,
            "reason_codes": comparison_reasons,
        }

    mean_excess = sum(valid_excess) / len(valid_excess) if valid_excess else None
    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": str(evidence_cutoff),
        "window": str(window_name).upper(),
        "stock_return": stock_return,
        "comparisons": comparisons,
        "mean_excess_return": mean_excess,
        "strongest_excess_return": max(valid_excess) if valid_excess else None,
        "weakest_excess_return": min(valid_excess) if valid_excess else None,
        "reason_codes": sorted(set(reason_codes)),
    }


def compute_relative_strength_windows(
    stock_windows: Mapping[str, Any],
    benchmark_windows: Mapping[str, Mapping[str, Any]],
    *,
    trade_date: str | date | datetime,
    evidence_cutoffs: Mapping[str, str | time | datetime],
    window_starts: Mapping[str, str | time | datetime] | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, dict[str, Any]]:
    """Calculate named windows independently, preventing cross-window leakage."""

    starts = window_starts or {}
    output: dict[str, dict[str, Any]] = {}
    for name in sorted(stock_windows):
        if name not in evidence_cutoffs:
            raise ValueError(f"missing evidence cutoff for relative-strength window: {name}")
        per_benchmark = {
            symbol: windows[name]
            for symbol, windows in benchmark_windows.items()
            if name in windows
        }
        output[name] = compute_relative_strength(
            stock_windows[name],
            per_benchmark,
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoffs[name],
            window_start=starts.get(name),
            window_name=name,
            timezone=timezone,
        )
    return output


calculate_relative_strength = compute_relative_strength


__all__ = [
    "calculate_relative_strength",
    "calculate_window_return",
    "compute_relative_strength",
    "compute_relative_strength_windows",
    "excess_return",
]
