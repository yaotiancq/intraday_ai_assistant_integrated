from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Mapping


SECTOR_SYMBOLS = {"XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV", "XLI", "XLB", "XLU", "XLRE"}
INDUSTRY_SYMBOLS = {"SMH", "SOXX", "IGV", "ITA"}
REQUIRED_BROAD_SYMBOLS = {"SPY", "QQQ", "IWM", "DIA"}


def compute_market_regime(
    index_snapshots: list[dict[str, Any]],
    sector_snapshots: list[dict[str, Any]],
    volatility_snapshot: Mapping[str, Any] | None = None,
    *,
    as_of: datetime | None = None,
    maximum_data_age_seconds: int = 90,
) -> dict[str, Any]:
    """Classify the point-in-time market regime using transparent rules.

    Returns use percentage-point returns (for example, ``0.45`` means 0.45%).
    Missing inputs reduce completeness and can produce ``UNKNOWN``; they are not
    silently treated as bullish or bearish observations.
    """

    raw_rows = [*index_snapshots, *sector_snapshots]
    data_quality_reason_codes: list[str] = []
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        reason = _freshness_reason(item, as_of, maximum_data_age_seconds)
        if reason is None:
            rows.append(item)
        else:
            data_quality_reason_codes.append(f"{str(item.get('symbol', 'UNKNOWN')).upper()}:{reason}")
    volatility_reason = _freshness_reason(volatility_snapshot, as_of, maximum_data_age_seconds)
    if volatility_reason is not None:
        if volatility_snapshot:
            data_quality_reason_codes.append(
                f"{str(volatility_snapshot.get('symbol', 'VOLATILITY_PROXY')).upper()}:{volatility_reason}"
            )
        volatility_snapshot = None
    by_symbol = {str(item.get("symbol", "")).upper(): item for item in rows if item.get("symbol")}
    returns = {symbol: _pct(snapshot) for symbol, snapshot in by_symbol.items()}
    broad_available = sum(returns.get(symbol) is not None for symbol in REQUIRED_BROAD_SYMBOLS)
    tracked_sector_returns = [
        value for symbol, value in returns.items() if symbol in SECTOR_SYMBOLS and value is not None
    ]
    semiconductor_values = [returns.get("SMH"), returns.get("SOXX")]
    semiconductor_values = [value for value in semiconductor_values if value is not None]

    spy = returns.get("SPY")
    qqq = returns.get("QQQ")
    iwm = returns.get("IWM")
    dia = returns.get("DIA")
    semiconductors = sum(semiconductor_values) / len(semiconductor_values) if semiconductor_values else None
    sector_breadth = (
        sum(1 for value in tracked_sector_returns if value > 0) / len(tracked_sector_returns)
        if tracked_sector_returns
        else None
    )
    valid_expected = broad_available + len(tracked_sector_returns) + len(semiconductor_values)
    expected_total = 4 + len(SECTOR_SYMBOLS) + 2
    completeness = valid_expected / expected_total

    components = {
        "spy_return": _component(spy, cap=1.2, weight=22),
        "qqq_return": _component(qqq, cap=1.4, weight=20),
        "small_cap_participation": _component(iwm, cap=1.2, weight=11),
        "dow_participation": _component(dia, cap=1.0, weight=7),
        "sector_participation": _component(
            None if sector_breadth is None else (sector_breadth - 0.5) * 2,
            cap=1.0,
            weight=18,
        ),
        "semiconductor_leadership": _component(semiconductors, cap=1.8, weight=14),
        "growth_relative_strength": _component(
            None if spy is None or qqq is None else qqq - spy,
            cap=0.8,
            weight=8,
        ),
    }
    score = round(max(-100.0, min(100.0, sum(item["contribution"] for item in components.values()))), 2)

    vix_level = _float((volatility_snapshot or {}).get("last"))
    vix_change = _pct(volatility_snapshot)
    spreads = [_spread_bps(item) for item in rows]
    spreads = [value for value in spreads if value is not None]
    median_spread = sorted(spreads)[len(spreads) // 2] if spreads else None

    reason_codes: list[str] = []
    if spy is not None:
        reason_codes.append("SPY_POSITIVE" if spy > 0 else "SPY_NEGATIVE" if spy < 0 else "SPY_FLAT")
    if qqq is not None and spy is not None:
        reason_codes.append("QQQ_OUTPERFORMING_SPY" if qqq > spy else "QQQ_UNDERPERFORMING_SPY")
    if iwm is not None:
        reason_codes.append("SMALL_CAP_PARTICIPATION" if iwm > 0 else "SMALL_CAP_LAGGING")
    if sector_breadth is not None:
        reason_codes.append("BROAD_SECTOR_PARTICIPATION" if sector_breadth >= 0.64 else "NARROW_SECTOR_PARTICIPATION")
    if semiconductors is not None:
        reason_codes.append("SEMICONDUCTOR_LEADERSHIP" if semiconductors > 0.35 else "SEMICONDUCTOR_LAGGING")

    if completeness < 0.35 or broad_available < 2:
        classification = "UNKNOWN"
        reason_codes.append("INSUFFICIENT_BENCHMARK_DATA")
    elif median_spread is not None and median_spread > 45:
        classification = "LOW_LIQUIDITY"
        reason_codes.append("BENCHMARK_SPREADS_WIDE")
    elif vix_level is not None and (vix_level >= 30 or (vix_change is not None and vix_change >= 12)):
        classification = "HIGH_VOLATILITY"
        reason_codes.append("VOLATILITY_PROXY_ELEVATED")
    else:
        classification = classify_regime(score)
    if data_quality_reason_codes:
        reason_codes.append("BENCHMARK_FRESHNESS_FAILURES_PRESENT")

    strong = sorted(
        (symbol for symbol, value in returns.items() if symbol in SECTOR_SYMBOLS | INDUSTRY_SYMBOLS and value is not None and value >= 0.35),
        key=lambda symbol: (-float(returns[symbol]), symbol),
    )
    weak = sorted(
        (symbol for symbol, value in returns.items() if symbol in SECTOR_SYMBOLS | INDUSTRY_SYMBOLS and value is not None and value <= -0.35),
        key=lambda symbol: (float(returns[symbol]), symbol),
    )
    return {
        "classification": classification,
        "label": label_regime(score),
        "score": score,
        "direction": "LONG" if score >= 20 else "SHORT" if score <= -20 else "MIXED",
        "reason_codes": sorted(set(reason_codes)),
        "data_quality_reason_codes": sorted(set(data_quality_reason_codes)),
        "data_completeness": round(completeness, 4),
        "index_changes": {symbol: returns.get(symbol) for symbol in sorted(REQUIRED_BROAD_SYMBOLS)},
        "sector_changes": {
            symbol: returns[symbol]
            for symbol in sorted(returns)
            if symbol in SECTOR_SYMBOLS | INDUSTRY_SYMBOLS
        },
        "sector_breadth": None if sector_breadth is None else round(sector_breadth, 4),
        "qqq_vs_spy": None if spy is None or qqq is None else round(qqq - spy, 4),
        "small_cap_participation": iwm,
        "semiconductor_return": None if semiconductors is None else round(semiconductors, 4),
        "volatility": {"level": vix_level, "change_pct": vix_change},
        "median_benchmark_spread_bps": median_spread,
        "components": components,
        "strong_sectors": strong[:6],
        "weak_sectors": weak[:6],
        "summary": _summary(classification, score, strong, weak),
    }


def classify_regime(score: float) -> str:
    if score >= 50:
        return "STRONG_RISK_ON"
    if score >= 20:
        return "RISK_ON"
    if score > -20:
        return "MIXED"
    if score > -50:
        return "RISK_OFF"
    return "STRONG_RISK_OFF"


def label_regime(score: float) -> str:
    """Backward-compatible label retained for legacy report consumers."""

    return _legacy_label(classify_regime(score))


def _legacy_label(classification: str) -> str:
    return {
        "STRONG_RISK_ON": "strong_risk_on",
        "RISK_ON": "mild_risk_on",
        "MIXED": "neutral_choppy",
        "RISK_OFF": "mild_risk_off",
        "STRONG_RISK_OFF": "strong_risk_off",
        "HIGH_VOLATILITY": "high_volatility",
        "LOW_LIQUIDITY": "low_liquidity",
        "UNKNOWN": "unknown",
    }[classification]


def _summary(classification: str, score: float, strong: list[str], weak: list[str]) -> str:
    return (
        f"{classification} (score={score:.2f}); "
        f"leaders={','.join(strong[:4]) or 'none'}; laggards={','.join(weak[:4]) or 'none'}."
    )


def _component(value: float | None, cap: float, weight: float) -> dict[str, Any]:
    normalized = 0.0 if value is None else max(-1.0, min(1.0, value / cap))
    return {
        "raw_value": value,
        "normalized_value": round(normalized, 6),
        "weight": weight,
        "contribution": round(normalized * weight, 6),
        "available": value is not None,
    }


def _pct(snapshot: Mapping[str, Any] | None) -> float | None:
    if not snapshot:
        return None
    value = snapshot.get("effective_change_pct", snapshot.get("change_pct"))
    if value is None:
        last = _float(snapshot.get("effective_price", snapshot.get("last")))
        previous = _float(snapshot.get("prev_close"))
        if last is not None and previous and previous > 0:
            value = (last / previous - 1.0) * 100.0
    return _float(value)


def _spread_bps(snapshot: Mapping[str, Any]) -> float | None:
    bid = _float(snapshot.get("bid_price", snapshot.get("bid")))
    ask = _float(snapshot.get("ask_price", snapshot.get("ask")))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2.0
    return (ask - bid) / midpoint * 10_000.0 if midpoint else None


def _float(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _freshness_reason(
    snapshot: Mapping[str, Any] | None,
    as_of: datetime | None,
    maximum_age_seconds: int,
) -> str | None:
    if as_of is None or snapshot is None:
        return None
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("market-regime as_of must be timezone-aware")
    value = (
        snapshot.get("timestamp")
        or snapshot.get("update_time")
        or snapshot.get("as_of")
        or snapshot.get("data_time")
    )
    if not value:
        return "MISSING_BENCHMARK_TIMESTAMP"
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "INVALID_BENCHMARK_TIMESTAMP"
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=as_of.tzinfo)
    age = (as_of - stamp.astimezone(as_of.tzinfo)).total_seconds()
    if age < 0:
        return "FUTURE_BENCHMARK_TIMESTAMP"
    if age > max(0, int(maximum_age_seconds)):
        return "STALE_BENCHMARK_DATA"
    return None
