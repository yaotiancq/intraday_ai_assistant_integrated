"""Hard, score-independent market-data and trade-structure risk gates."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Mapping, Sequence

from app.indicators._bars import (
    DEFAULT_TIMEZONE,
    bar_interval_seconds,
    finite_float,
    parse_bar_timestamp,
    parse_cutoff,
    parse_trade_date,
)
from app.scoring._common import json_safe, resolve_section


DEFAULT_RISK_GATES = {
    "maximum_data_age_seconds": 90,
    "maximum_spread_bps": 30,
    "minimum_price": 5.0,
    "minimum_average_daily_dollar_volume": 50_000_000,
    "minimum_premarket_dollar_volume": 750_000,
    "minimum_opening_relative_volume": 1.0,
    "maximum_opening_range_atr_ratio": 0.8,
    "minimum_opening_range_atr_ratio": 0.05,
    "maximum_gap_atr_ratio": 2.5,
    "maximum_entry_extension_atr": 0.35,
    "maximum_breakout_extension_atr": 0.25,
    "minimum_reward_risk_ratio": 1.5,
    "minimum_required_bar_coverage": 1.0,
}


def _number(evidence: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = finite_float(evidence.get(key))
        if value is not None:
            return value
    return None


def evaluate_risk_gates(
    evidence: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    direction: str | None = None,
    entry_plan: Mapping[str, Any] | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Evaluate hard failures without consulting or modifying any numeric score."""

    config = config or {}
    section = resolve_section(config, "risk_gates")
    thresholds = dict(DEFAULT_RISK_GATES)
    for key in thresholds:
        if (value := finite_float(section.get(key))) is not None:
            thresholds[key] = value
    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    day = parse_trade_date(trade_date)
    selected_direction = str(direction or evidence.get("direction", "")).upper()
    plan = entry_plan or (evidence.get("entry_plan") if isinstance(evidence.get("entry_plan"), Mapping) else {})
    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    def add(
        name: str,
        evaluated: bool,
        passed: bool,
        reason_code: str,
        *,
        value: Any = None,
        threshold: Any = None,
    ) -> None:
        record = {
            "gate": name,
            "evaluated": bool(evaluated),
            "passed": bool(passed) if evaluated else None,
            "value": json_safe(value),
            "threshold": json_safe(threshold),
            "reason_code": reason_code if evaluated and not passed else None,
        }
        checks.append(record)
        if evaluated and not passed:
            failures.append(reason_code)

    # Explicit feed/data quality flags.
    stale_flag = bool(evidence.get("stale_market_data") or str(evidence.get("data_status", "")).upper() == "STALE")
    add("stale_market_data_flag", stale_flag, not stale_flag, "STALE_MARKET_DATA", value=stale_flag)
    delayed = bool(evidence.get("delayed_market_data") or evidence.get("is_delayed"))
    add("delayed_market_data", delayed, not delayed, "DELAYED_MARKET_DATA", value=delayed)
    missing_benchmark = bool(evidence.get("missing_benchmark_data") or evidence.get("benchmark_data_complete") is False)
    add("benchmark_data", missing_benchmark, not missing_benchmark, "MISSING_BENCHMARK_DATA", value=not missing_benchmark)
    missing_sector = bool(evidence.get("missing_sector_data") or evidence.get("sector_data_complete") is False)
    add("sector_data", missing_sector, not missing_sector, "MISSING_SECTOR_ETF_DATA", value=not missing_sector)
    upstream_validation = {str(code).upper() for code in evidence.get("validation_reason_codes", [])}
    validation_code_map = {
        "MISSING_BAR_TIMESTAMP": "MISSING_BAR_TIMESTAMPS",
        "DUPLICATE_BARS": "DUPLICATE_BARS",
        "NON_MONOTONIC_TIMESTAMPS": "NON_MONOTONIC_TIMESTAMPS",
        "INCORRECT_SESSION_BARS": "INCORRECT_SESSION_BARS",
        "FUTURE_OR_POST_CUTOFF_BARS": "FUTURE_TIMESTAMPS",
        "INCOMPLETE_BARS": "INCOMPLETE_BARS",
        "INVALID_PRICES": "INVALID_PRICES",
        "INVALID_OHLC_RELATIONSHIP": "INVALID_PRICES",
        "INVALID_VOLUME": "INVALID_VOLUME",
        "MISSING_BARS": "MISSING_BARS",
        "SUSPICIOUS_ZERO_VOLUME_BARS": "SUSPICIOUS_ZERO_VOLUME_BARS",
    }
    for upstream_code, gate_code in validation_code_map.items():
        present = upstream_code in upstream_validation
        add(f"upstream_validation_{upstream_code.lower()}", present, not present, gate_code, value=upstream_code)

    bars_provided = "bars" in evidence or "opening_bars" in evidence
    bars_value = evidence.get("bars", evidence.get("opening_bars"))
    bars = [dict(bar) for bar in bars_value] if isinstance(bars_value, Sequence) and not isinstance(bars_value, (str, bytes)) else []
    parsed = [parse_bar_timestamp(bar, day, timezone) for bar in bars]
    valid_stamps = [stamp for stamp in parsed if stamp is not None]
    unparseable = len(bars) - len(valid_stamps)
    add("bar_timestamps_parseable", bool(bars), unparseable == 0, "MISSING_BAR_TIMESTAMPS", value=unparseable, threshold=0)
    duplicate_count = len(valid_stamps) - len(set(valid_stamps))
    add("duplicate_bars", bool(bars), duplicate_count == 0, "DUPLICATE_BARS", value=duplicate_count, threshold=0)
    monotonic = all(valid_stamps[index] > valid_stamps[index - 1] for index in range(1, len(valid_stamps)))
    add("monotonic_timestamps", bool(bars) and len(valid_stamps) > 1, monotonic, "NON_MONOTONIC_TIMESTAMPS")
    future_count = sum(stamp >= cutoff for stamp in valid_stamps)
    add("future_timestamps", bool(bars), future_count == 0, "FUTURE_TIMESTAMPS", value=future_count, threshold=0)
    session_open = parse_cutoff(day, "09:30", timezone)
    incorrect_session = sum(stamp.date() != day or stamp < session_open for stamp in valid_stamps)
    add("correct_session_bars", bool(bars), incorrect_session == 0, "INCORRECT_SESSION_BARS", value=incorrect_session, threshold=0)

    incomplete_count = 0
    completed_stamps: list[datetime] = []
    for bar, stamp in zip(bars, parsed):
        if stamp is None:
            continue
        end = stamp + timedelta(seconds=bar_interval_seconds(bar))
        incomplete = bar.get("is_complete") is False or bar.get("complete") is False or end > cutoff
        if incomplete and stamp < cutoff:
            incomplete_count += 1
        elif session_open <= stamp < cutoff and end <= cutoff:
            completed_stamps.append(stamp)
    add("complete_bars", bool(bars), incomplete_count == 0, "INCOMPLETE_BARS", value=incomplete_count, threshold=0)

    explicit_expected = _number(evidence, "expected_bar_count")
    expected_count = int(explicit_expected) if explicit_expected is not None else max(0, int((cutoff - session_open).total_seconds() // 60))
    unique_completed = len(set(completed_stamps))
    coverage = unique_completed / expected_count if expected_count else None
    min_coverage = thresholds["minimum_required_bar_coverage"]
    explicit_missing_bars = bool(evidence.get("missing_bars"))
    coverage_evaluated = (bars_provided or explicit_missing_bars) and expected_count > 0
    coverage_passed = not explicit_missing_bars and coverage is not None and coverage >= min_coverage
    add("bar_coverage", coverage_evaluated, coverage_passed, "MISSING_BARS", value=coverage, threshold=min_coverage)

    explicit_age = _number(evidence, "data_age_seconds", "market_data_age_seconds")
    if explicit_age is None and completed_stamps:
        latest_bar = max(completed_stamps)
        latest_bar_source = next(bar for bar, stamp in zip(bars, parsed) if stamp == latest_bar)
        latest_end = latest_bar + timedelta(seconds=bar_interval_seconds(latest_bar_source))
        explicit_age = max(0.0, (cutoff - latest_end).total_seconds())
    max_age = thresholds["maximum_data_age_seconds"]
    add("data_freshness", explicit_age is not None, explicit_age is not None and explicit_age <= max_age, "STALE_MARKET_DATA", value=explicit_age, threshold=max_age)

    invalid_price_count = 0
    invalid_volume_count = 0
    zero_volume_count = 0
    for bar in bars:
        prices = [_number(bar, key) for key in ("open", "high", "low", "close")]
        if any(value is None or value <= 0 for value in prices):
            invalid_price_count += 1
        elif prices[1] < max(prices[0], prices[3]) or prices[2] > min(prices[0], prices[3]) or prices[1] < prices[2]:  # type: ignore[operator]
            invalid_price_count += 1
        volume = _number(bar, "volume")
        if volume is None or volume < 0:
            invalid_volume_count += 1
        elif volume == 0:
            zero_volume_count += 1
    current_price = _number(evidence, "current_price", "last_price", "price")
    min_price = thresholds["minimum_price"]
    add("valid_bar_prices", bool(bars), invalid_price_count == 0, "INVALID_PRICES", value=invalid_price_count, threshold=0)
    add("minimum_price", current_price is not None, current_price is not None and current_price >= min_price, "INVALID_PRICES", value=current_price, threshold=min_price)
    add("valid_bar_volume", bool(bars), invalid_volume_count == 0, "INVALID_VOLUME", value=invalid_volume_count, threshold=0)
    suspicious_zero_threshold = max(2, expected_count // 3) if expected_count else 2
    suspicious_zero = bool(evidence.get("suspicious_zero_volume_bars")) or zero_volume_count >= suspicious_zero_threshold
    add("zero_volume_bars", bool(bars) or bool(evidence.get("suspicious_zero_volume_bars")), not suspicious_zero, "SUSPICIOUS_ZERO_VOLUME_BARS", value=zero_volume_count, threshold=f"<{suspicious_zero_threshold}")

    spread = _number(evidence, "spread_bps", "current_spread_bps")
    add("maximum_spread", spread is not None, spread is not None and spread <= thresholds["maximum_spread_bps"], "SPREAD_ABOVE_HARD_MAXIMUM", value=spread, threshold=thresholds["maximum_spread_bps"])
    adv = _number(evidence, "average_daily_dollar_volume", "avg_daily_dollar_volume")
    add("average_daily_dollar_volume", adv is not None, adv is not None and adv >= thresholds["minimum_average_daily_dollar_volume"], "INSUFFICIENT_AVERAGE_DAILY_DOLLAR_VOLUME", value=adv, threshold=thresholds["minimum_average_daily_dollar_volume"])
    premarket_dv = _number(evidence, "premarket_dollar_volume")
    add("premarket_dollar_volume", premarket_dv is not None, premarket_dv is not None and premarket_dv >= thresholds["minimum_premarket_dollar_volume"], "INSUFFICIENT_PREMARKET_DOLLAR_VOLUME", value=premarket_dv, threshold=thresholds["minimum_premarket_dollar_volume"])
    opening_rvol = _number(evidence, "opening_relative_volume")
    add("opening_relative_volume", opening_rvol is not None, opening_rvol is not None and opening_rvol >= thresholds["minimum_opening_relative_volume"], "INSUFFICIENT_OPENING_RELATIVE_VOLUME", value=opening_rvol, threshold=thresholds["minimum_opening_relative_volume"])

    range_atr = _number(evidence, "opening_range_atr_ratio")
    add("maximum_opening_range", range_atr is not None, range_atr is not None and range_atr <= thresholds["maximum_opening_range_atr_ratio"], "OPENING_RANGE_EXCESSIVELY_WIDE", value=range_atr, threshold=thresholds["maximum_opening_range_atr_ratio"])
    add("minimum_opening_range", range_atr is not None, range_atr is not None and range_atr >= thresholds["minimum_opening_range_atr_ratio"], "OPENING_RANGE_TOO_NARROW", value=range_atr, threshold=thresholds["minimum_opening_range_atr_ratio"])
    gap_atr = abs(_number(evidence, "gap_atr_ratio") or 0.0) if _number(evidence, "gap_atr_ratio") is not None else None
    add("maximum_gap", gap_atr is not None, gap_atr is not None and gap_atr <= thresholds["maximum_gap_atr_ratio"], "GAP_EXCESSIVELY_LARGE", value=gap_atr, threshold=thresholds["maximum_gap_atr_ratio"])
    entry_extension = abs(_number(evidence, "entry_extension_atr", "distance_from_vwap_atr") or 0.0) if _number(evidence, "entry_extension_atr", "distance_from_vwap_atr") is not None else None
    add("entry_vwap_extension", entry_extension is not None, entry_extension is not None and entry_extension <= thresholds["maximum_entry_extension_atr"], "ENTRY_EXCESSIVELY_EXTENDED_FROM_VWAP", value=entry_extension, threshold=thresholds["maximum_entry_extension_atr"])
    breakout_extension = abs(_number(evidence, "breakout_distance_atr", "breakout_extension_atr") or 0.0) if _number(evidence, "breakout_distance_atr", "breakout_extension_atr") is not None else None
    add("breakout_extension", breakout_extension is not None, breakout_extension is not None and breakout_extension <= thresholds["maximum_breakout_extension_atr"], "ENTRY_EXCESSIVELY_EXTENDED_FROM_BREAKOUT_LEVEL", value=breakout_extension, threshold=thresholds["maximum_breakout_extension_atr"])

    valid_stop_value = plan.get("valid_stop") if isinstance(plan, Mapping) else None
    stop = finite_float(plan.get("initial_stop_reference")) if isinstance(plan, Mapping) else None
    entry = finite_float(plan.get("entry_reference_level")) if isinstance(plan, Mapping) else None
    if valid_stop_value is None and plan:
        valid_stop_value = stop is not None and entry is not None and ((stop < entry) if selected_direction == "LONG" else (stop > entry) if selected_direction == "SHORT" else stop != entry)
    add("valid_stop_location", bool(plan) or evidence.get("valid_stop") is not None, bool(valid_stop_value if plan else evidence.get("valid_stop")), "NO_VALID_STOP_LOCATION", value=stop)
    reward_risk = finite_float(plan.get("expected_reward_risk_ratio")) if isinstance(plan, Mapping) else None
    if reward_risk is None:
        reward_risk = _number(evidence, "expected_reward_risk_ratio", "reward_risk_ratio")
    add("minimum_reward_risk", reward_risk is not None, reward_risk is not None and reward_risk >= thresholds["minimum_reward_risk_ratio"], "REWARD_RISK_BELOW_MINIMUM", value=reward_risk, threshold=thresholds["minimum_reward_risk_ratio"])

    halted = bool(evidence.get("trading_halt") or evidence.get("halted") or str(evidence.get("trading_status", "")).upper() == "HALTED")
    add("trading_halt", halted, not halted, "TRADING_HALT_INDICATION", value=halted)
    extreme_market = bool(evidence.get("extreme_market_conflict"))
    add("extreme_market_conflict", extreme_market, not extreme_market, "EXTREME_MARKET_CONFLICT", value=extreme_market)
    extreme_sector = bool(evidence.get("extreme_sector_conflict"))
    add("extreme_sector_conflict", extreme_sector, not extreme_sector, "EXTREME_SECTOR_CONFLICT", value=extreme_sector)

    unique_failures = list(dict.fromkeys(failures))
    return {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": cutoff.isoformat(),
        "direction": selected_direction or None,
        "passed": not unique_failures,
        "hard_gate_passed": not unique_failures,
        "failures": unique_failures,
        "failure_reason_codes": unique_failures,
        "reason_codes": ["ALL_HARD_RISK_GATES_PASSED"] if not unique_failures else unique_failures,
        "checks": checks,
        "thresholds": thresholds,
    }


apply_risk_gates = evaluate_risk_gates


__all__ = ["DEFAULT_RISK_GATES", "apply_risk_gates", "evaluate_risk_gates"]
