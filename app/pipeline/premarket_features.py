from __future__ import annotations

from datetime import date, datetime
from statistics import mean
from typing import Any, Iterable, Mapping

from app.indicators._bars import finite_float, parse_bar_timestamp, select_completed_bars
from app.indicators.relative_strength import compute_relative_strength
from app.indicators.volume_metrics import compute_premarket_volume_metrics
from app.validators.snapshot_validator import validate_snapshot_timestamp


def build_premarket_features(
    *,
    symbol: str,
    trade_date: str,
    evidence_cutoff: datetime,
    snapshot: Mapping[str, Any],
    daily_bars: Iterable[Mapping[str, Any]],
    premarket_bars: Iterable[Mapping[str, Any]],
    benchmark_snapshots: Mapping[str, Mapping[str, Any]],
    comparison_etfs: Iterable[str],
    catalyst: Mapping[str, Any],
    maximum_data_age_seconds: int,
) -> dict[str, Any]:
    """Build one point-in-time, JSON-safe premarket feature row."""

    daily = _daily_before_trade_date(daily_bars, trade_date)
    pm_bars = select_completed_bars(
        premarket_bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start="04:00",
    )
    last_price = _last_price(snapshot, pm_bars)
    previous_close = _first_number(snapshot, "prev_close")
    if previous_close is None and daily:
        previous_close = finite_float(daily[-1].get("close"))

    atr = _atr(daily, 14)
    adv20 = _average_dollar_volume(daily[-20:])
    adv60 = _average_dollar_volume(daily[-60:])
    average_daily_dollar_volume = _first_number(snapshot, "average_daily_dollar_volume", "avg_daily_dollar_volume")
    if average_daily_dollar_volume is None:
        average_daily_dollar_volume = adv20

    expected_premarket_volume = _first_number(snapshot, "expected_premarket_volume", "average_premarket_volume")
    if expected_premarket_volume is None:
        history = snapshot.get("premarket_volume_history")
        if isinstance(history, list):
            values = [value for item in history if (value := finite_float(item)) is not None and value > 0]
            expected_premarket_volume = mean(values) if values else None
    pm_volume = compute_premarket_volume_metrics(
        pm_bars,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        expected_premarket_volume=expected_premarket_volume,
    )
    if not pm_bars:
        direct_volume = _first_number(snapshot, "pre_volume", "premarket_volume")
        if direct_volume is not None:
            pm_volume["premarket_volume"] = direct_volume
            pm_volume["cumulative_volume"] = direct_volume
            pm_volume["premarket_dollar_volume"] = direct_volume * (last_price or 0.0)
            pm_volume["dollar_volume"] = pm_volume["premarket_dollar_volume"]
            if expected_premarket_volume:
                pm_volume["premarket_relative_volume"] = direct_volume / expected_premarket_volume
                pm_volume["relative_volume"] = direct_volume / expected_premarket_volume
    if pm_volume.get("premarket_relative_volume") is None:
        direct_rvol = _first_number(snapshot, "premarket_relative_volume", "pre_volume_ratio", "volume_ratio")
        if direct_rvol is not None:
            pm_volume["premarket_relative_volume"] = direct_rvol
            pm_volume["relative_volume"] = direct_rvol

    gap_return = (last_price / previous_close - 1.0) if last_price is not None and previous_close else None
    spread_bps = _spread_bps(snapshot, last_price)
    premarket_high = max((finite_float(bar.get("high")) or float("-inf") for bar in pm_bars), default=None)
    premarket_low = min((finite_float(bar.get("low")) or float("inf") for bar in pm_bars), default=None)
    if premarket_high in (None, float("-inf")):
        premarket_high = _first_number(snapshot, "pre_high_price", "premarket_high")
    if premarket_low in (None, float("inf")):
        premarket_low = _first_number(snapshot, "pre_low_price", "premarket_low")

    previous_high = finite_float(daily[-1].get("high")) if daily else _first_number(snapshot, "previous_day_high")
    previous_low = finite_float(daily[-1].get("low")) if daily else _first_number(snapshot, "previous_day_low")
    previous_open = finite_float(daily[-1].get("open")) if daily else None
    stock_return = gap_return
    benchmark_returns: dict[str, float | None] = {}
    benchmark_data_reason_codes: list[str] = []
    comparison_symbols = _ordered_comparisons(comparison_etfs)
    for benchmark in comparison_symbols:
        row = benchmark_snapshots.get(benchmark, {})
        timestamp_failures = validate_snapshot_timestamp(
            row,
            as_of=evidence_cutoff,
            maximum_age_seconds=maximum_data_age_seconds,
        )
        if timestamp_failures:
            benchmark_returns[benchmark] = None
            benchmark_data_reason_codes.extend(f"{benchmark}:{reason}" for reason in timestamp_failures)
        else:
            benchmark_returns[benchmark] = _snapshot_return(row)
    relative = compute_relative_strength(
        stock_return,
        benchmark_returns,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        window_name="PREMARKET",
    )
    comparisons = relative.get("comparisons", {})
    benchmark_data_complete = all(
        (comparisons.get(symbol) or {}).get("benchmark_return") is not None
        for symbol in ("SPY", "QQQ")
    )
    mapped_comparisons = [symbol for symbol in comparison_symbols if symbol not in {"SPY", "QQQ"}]
    sector_data_complete = bool(mapped_comparisons) and any(
        (comparisons.get(symbol) or {}).get("benchmark_return") is not None
        for symbol in mapped_comparisons
    )

    technical_location = _technical_location(
        price=last_price,
        previous_high=previous_high,
        previous_low=previous_low,
        premarket_high=premarket_high,
        premarket_low=premarket_low,
        atr=atr,
    )
    timestamp_reasons = validate_snapshot_timestamp(
        snapshot,
        as_of=evidence_cutoff,
        maximum_age_seconds=maximum_data_age_seconds,
    )
    if snapshot.get("delayed") is True or str(snapshot.get("data_status", "")).upper() == "DELAYED":
        timestamp_reasons.append("DELAYED_MARKET_DATA")
    if snapshot.get("halted") is True or str(snapshot.get("status", "")).upper() == "HALTED":
        timestamp_reasons.append("TRADING_HALT_INDICATION")
    return {
        "symbol": symbol,
        "last_price": last_price,
        "price": last_price,
        "previous_close": previous_close,
        "previous_day_open": previous_open,
        "previous_day_high": previous_high,
        "previous_day_low": previous_low,
        "premarket_high": premarket_high,
        "premarket_low": premarket_low,
        "gap_return": gap_return,
        "gap_percent": None if gap_return is None else gap_return * 100.0,
        "spread_bps": spread_bps,
        "average_daily_dollar_volume": average_daily_dollar_volume,
        "average_daily_dollar_volume_20d": adv20,
        "average_daily_dollar_volume_60d": adv60,
        "premarket_volume": pm_volume.get("premarket_volume"),
        "premarket_dollar_volume": pm_volume.get("premarket_dollar_volume"),
        "premarket_relative_volume": pm_volume.get("premarket_relative_volume"),
        "atr": atr,
        "atr_percent": (atr / last_price * 100.0) if atr is not None and last_price else None,
        "technical_location": technical_location,
        "technical_location_bias": technical_location.get("directional_bias"),
        "relative_strength": relative,
        "relative_strength_mean": relative.get("mean_excess_return"),
        "relative_strength_vs_spy": (relative.get("comparisons", {}).get("SPY") or {}).get("excess_return"),
        "relative_strength_vs_qqq": (relative.get("comparisons", {}).get("QQQ") or {}).get("excess_return"),
        "relative_strength_vs_sector": _first_comparison_excess(relative, comparison_symbols, exclude={"SPY", "QQQ"}),
        "benchmark_data_complete": benchmark_data_complete,
        "sector_data_complete": sector_data_complete,
        "missing_benchmark_data": not benchmark_data_complete,
        "missing_sector_data": not sector_data_complete,
        "benchmark_data_reason_codes": sorted(set(benchmark_data_reason_codes)),
        "catalyst": dict(catalyst),
        "catalyst_quality": finite_float(catalyst.get("quality_score")) or 0.0,
        "catalyst_direction": catalyst.get("catalyst_direction", "NEUTRAL"),
        "snapshot": dict(snapshot),
        "premarket_bar_count": len(pm_bars),
        "data_reason_codes": timestamp_reasons,
        "data_fresh": not timestamp_reasons,
    }


def apply_premarket_eligibility(features: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    reasons: list[str] = list(features.get("data_reason_codes", []))
    price = finite_float(features.get("price"))
    adv = finite_float(features.get("average_daily_dollar_volume"))
    pm_dollars = finite_float(features.get("premarket_dollar_volume"))
    spread = finite_float(features.get("spread_bps"))
    rvol = finite_float(features.get("premarket_relative_volume"))
    gap = finite_float(features.get("gap_percent"))

    if features.get("benchmark_data_complete") is False:
        reasons.append("MISSING_BENCHMARK_DATA")
    if features.get("sector_data_complete") is False:
        reasons.append("MISSING_SECTOR_ETF_DATA")

    _minimum(reasons, price, float(config["minimum_price"]), "PRICE_BELOW_MINIMUM", "INVALID_PRICE")
    _minimum(reasons, adv, float(config["minimum_average_daily_dollar_volume"]), "INSUFFICIENT_AVERAGE_DAILY_DOLLAR_VOLUME")
    _minimum(reasons, pm_dollars, float(config["minimum_premarket_dollar_volume"]), "INSUFFICIENT_PREMARKET_DOLLAR_VOLUME")
    _minimum(reasons, rvol, float(config["minimum_premarket_relative_volume"]), "INSUFFICIENT_PREMARKET_RELATIVE_VOLUME")
    if spread is None:
        reasons.append("MISSING_SPREAD")
    elif spread > float(config["maximum_premarket_spread_bps"]):
        reasons.append("PREMARKET_SPREAD_TOO_WIDE")
    if gap is None:
        reasons.append("MISSING_GAP")
    elif abs(gap) > float(config["maximum_absolute_gap_percent"]):
        reasons.append("GAP_ABOVE_MAXIMUM")

    hard_failures = {
        "STALE_MARKET_DATA", "FUTURE_QUOTE_TIMESTAMP", "MISSING_QUOTE_TIMESTAMP", "INVALID_QUOTE_TIMESTAMP",
        "DELAYED_MARKET_DATA", "TRADING_HALT_INDICATION",
        "MISSING_BENCHMARK_DATA", "MISSING_SECTOR_ETF_DATA",
        "PRICE_BELOW_MINIMUM", "INVALID_PRICE", "INSUFFICIENT_AVERAGE_DAILY_DOLLAR_VOLUME",
        "INSUFFICIENT_PREMARKET_DOLLAR_VOLUME", "INSUFFICIENT_PREMARKET_RELATIVE_VOLUME",
        "MISSING_SPREAD", "PREMARKET_SPREAD_TOO_WIDE", "MISSING_GAP", "GAP_ABOVE_MAXIMUM",
    }
    eligible = not any(reason in hard_failures for reason in reasons)
    catalyst = features.get("catalyst", {}) or {}
    event_driven = bool(catalyst.get("confirmed")) or (
        gap is not None and abs(gap) >= float(config["event_gap_percent"])
    ) or (rvol is not None and rvol >= float(config["event_relative_volume"]))
    relative_value = finite_float(features.get("relative_strength_mean"))
    relative_strength = relative_value is not None and abs(relative_value * 100.0) >= float(config["minimum_relative_strength_percent"])
    candidate_type = "EVENT_DRIVEN" if event_driven else "RELATIVE_STRENGTH" if relative_strength else "NONE"
    if candidate_type == "NONE":
        reasons.append("NO_EVENT_OR_RELATIVE_STRENGTH_QUALIFIER")
    qualified = eligible and candidate_type != "NONE"
    return {
        "eligible": eligible,
        "qualified": qualified,
        "candidate_type": candidate_type,
        "reason_codes": sorted(set(reasons)),
    }


def _daily_before_trade_date(bars: Iterable[Mapping[str, Any]], trade_date: str) -> list[dict[str, Any]]:
    day = date.fromisoformat(trade_date)
    selected: list[tuple[str, dict[str, Any]]] = []
    for raw in bars:
        bar = dict(raw)
        raw_date = bar.get("date", bar.get("timestamp", bar.get("time_key", "")))
        try:
            bar_day = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            continue
        if bar_day < day:
            selected.append((bar_day.isoformat(), bar))
    selected.sort(key=lambda item: item[0])
    return [bar for _, bar in selected]


def _last_price(snapshot: Mapping[str, Any], bars: list[Mapping[str, Any]]) -> float | None:
    if bars:
        value = finite_float(bars[-1].get("close"))
        if value is not None:
            return value
    return _first_number(snapshot, "effective_price", "pre_price", "last")


def _first_number(value: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        number = finite_float(value.get(key))
        if number is not None:
            return number
    return None


def _average_dollar_volume(bars: list[Mapping[str, Any]]) -> float | None:
    values: list[float] = []
    for bar in bars:
        turnover = finite_float(bar.get("turnover"))
        if turnover is None:
            close = finite_float(bar.get("close"))
            volume = finite_float(bar.get("volume"))
            turnover = close * volume if close is not None and volume is not None else None
        if turnover is not None and turnover >= 0:
            values.append(turnover)
    return mean(values) if values else None


def _atr(bars: list[Mapping[str, Any]], window: int) -> float | None:
    if not bars:
        return None
    ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars[-(window + 1):]:
        high = finite_float(bar.get("high"))
        low = finite_float(bar.get("low"))
        close = finite_float(bar.get("close"))
        if high is None or low is None or close is None:
            continue
        values = [high - low]
        if previous_close is not None:
            values.extend([abs(high - previous_close), abs(low - previous_close)])
        ranges.append(max(values))
        previous_close = close
    return mean(ranges[-window:]) if ranges else None


def _spread_bps(snapshot: Mapping[str, Any], price: float | None) -> float | None:
    direct = _first_number(snapshot, "spread_bps", "premarket_spread_bps")
    if direct is not None:
        return direct
    bid = _first_number(snapshot, "bid_price", "bid")
    ask = _first_number(snapshot, "ask_price", "ask")
    midpoint = (bid + ask) / 2.0 if bid is not None and ask is not None else price
    if bid is None or ask is None or midpoint is None or midpoint <= 0 or ask < bid:
        return None
    return (ask - bid) / midpoint * 10_000.0


def _snapshot_return(snapshot: Mapping[str, Any]) -> float | None:
    percent = _first_number(snapshot, "effective_change_pct", "change_pct", "pre_change_rate")
    if percent is not None:
        return percent / 100.0
    price = _first_number(snapshot, "effective_price", "pre_price", "last")
    previous = _first_number(snapshot, "prev_close")
    return price / previous - 1.0 if price is not None and previous else None


def _technical_location(**values: float | None) -> dict[str, Any]:
    price = values["price"]
    atr = values["atr"]
    levels = {name: value for name, value in values.items() if name not in {"price", "atr"} and value is not None}
    distances = {name: (price - value) / atr for name, value in levels.items()} if price is not None and atr else {}
    nearest = min(distances, key=lambda name: abs(distances[name])) if distances else None
    important = nearest is not None and abs(distances[nearest]) <= 0.35
    directional_bias = 0.0
    if nearest in {"previous_high", "premarket_high"}:
        directional_bias = 0.7
    elif nearest in {"previous_low", "premarket_low"}:
        directional_bias = -0.7
    return {
        "nearest_level": nearest,
        "distance_atr": distances.get(nearest) if nearest else None,
        "important_location": important,
        "directional_bias": directional_bias,
        "distances_atr": distances,
        "reason_codes": ["AT_IMPORTANT_TECHNICAL_LOCATION"] if important else ["MID_RANGE_LOCATION"],
    }


def _ordered_comparisons(values: Iterable[str]) -> list[str]:
    symbols = ["SPY", "QQQ", *(str(item).upper() for item in values)]
    return list(dict.fromkeys(symbols))


def _first_comparison_excess(relative: Mapping[str, Any], symbols: list[str], exclude: set[str]) -> float | None:
    for symbol in symbols:
        if symbol in exclude:
            continue
        value = finite_float((relative.get("comparisons", {}).get(symbol) or {}).get("excess_return"))
        if value is not None:
            return value
    return None


def _minimum(reasons: list[str], value: float | None, threshold: float, low_code: str, missing_code: str | None = None) -> None:
    if value is None:
        reasons.append(missing_code or low_code)
    elif value < threshold:
        reasons.append(low_code)
