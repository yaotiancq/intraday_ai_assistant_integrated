from __future__ import annotations

import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
STAGE_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_market_configuration(config: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(config, Mapping):
        return ["configuration_must_be_object"]

    version = str(config.get("strategy_version", "")).strip()
    if not version:
        errors.append("missing_strategy_version")

    universe = config.get("universe")
    if not isinstance(universe, Mapping):
        return errors + ["universe_must_be_object"]
    if universe.get("mode") != "fixed":
        errors.append("universe_mode_must_be_fixed")
    stocks = universe.get("stocks")
    if not isinstance(stocks, list):
        return errors + ["universe_stocks_must_be_list"]
    if len(stocks) != 30:
        errors.append(f"fixed_universe_must_have_30_stocks:{len(stocks)}")

    symbols: list[str] = []
    for index, stock in enumerate(stocks):
        prefix = f"stock[{index}]"
        if not isinstance(stock, Mapping):
            errors.append(f"{prefix}_must_be_object")
            continue
        symbol = str(stock.get("symbol", "")).strip().upper()
        symbols.append(symbol)
        if not SYMBOL_PATTERN.fullmatch(symbol):
            errors.append(f"{prefix}_invalid_symbol:{symbol}")
        for field in ("company_name", "sector", "sector_zh", "industry"):
            if not str(stock.get(field, "")).strip():
                errors.append(f"{prefix}_missing_{field}")
        comparisons = stock.get("comparison_etfs")
        if not isinstance(comparisons, list) or not comparisons:
            errors.append(f"{prefix}_missing_comparison_etfs")
        elif len(comparisons) != len({str(item).upper() for item in comparisons}):
            errors.append(f"{prefix}_duplicate_comparison_etfs")
    duplicate_symbols = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicate_symbols:
        errors.append("duplicate_stock_symbols:" + ",".join(duplicate_symbols))

    benchmarks = config.get("benchmarks")
    benchmark_symbols: list[str] = []
    if not isinstance(benchmarks, Mapping):
        errors.append("benchmarks_must_be_object")
    else:
        for category in ("broad_market", "sectors", "industries"):
            values = benchmarks.get(category)
            if not isinstance(values, list) or not values:
                errors.append(f"benchmarks_{category}_must_be_nonempty_list")
                continue
            for index, item in enumerate(values):
                if not isinstance(item, Mapping):
                    errors.append(f"benchmark_{category}[{index}]_must_be_object")
                    continue
                symbol = str(item.get("symbol", "")).strip().upper()
                benchmark_symbols.append(symbol)
                if not SYMBOL_PATTERN.fullmatch(symbol):
                    errors.append(f"benchmark_{category}[{index}]_invalid_symbol:{symbol}")
                for field in ("name_en", "name_zh"):
                    if not str(item.get(field, "")).strip():
                        errors.append(f"benchmark_{category}[{index}]_missing_{field}")
    if set(symbols) & set(benchmark_symbols):
        errors.append("benchmark_symbols_must_be_separate_from_stocks")
    if len(benchmark_symbols) != len(set(benchmark_symbols)):
        errors.append("duplicate_benchmark_symbols")
    benchmark_set = set(benchmark_symbols)
    for stock in stocks:
        if isinstance(stock, Mapping):
            unknown = sorted(set(str(x).upper() for x in stock.get("comparison_etfs", [])) - benchmark_set)
            if unknown:
                errors.append(f"unknown_comparison_etf:{stock.get('symbol')}:{','.join(unknown)}")

    _validate_weights(config, "premarket_scoring", 100.0, errors)
    _validate_weights(config, "opening_scoring", 100.0, errors)
    combined = config.get("combined_scoring", {})
    if not isinstance(combined, Mapping) or abs(sum(_number(x) for x in combined.values()) - 1.0) > 1e-9:
        errors.append("combined_scoring_weights_must_sum_to_1")

    scheduler = config.get("scheduler")
    if not isinstance(scheduler, Mapping):
        errors.append("scheduler_must_be_object")
    else:
        try:
            ZoneInfo(str(scheduler.get("timezone", "")))
        except ZoneInfoNotFoundError:
            errors.append("scheduler_invalid_timezone")
        stages = scheduler.get("stages")
        required_stages = ("universe_validation", "premarket", "opening_5m", "opening_15m")
        if not isinstance(stages, Mapping):
            errors.append("scheduler_stages_must_be_object")
        else:
            for stage in required_stages:
                value = stages.get(stage)
                if not isinstance(value, Mapping):
                    errors.append(f"scheduler_missing_stage:{stage}")
                elif not STAGE_TIME_PATTERN.fullmatch(str(value.get("time", ""))):
                    errors.append(f"scheduler_invalid_stage_time:{stage}")

    selection = config.get("candidate_selection", {})
    if isinstance(selection, Mapping):
        premarket_max = int(selection.get("maximum_premarket_candidates", 0) or 0)
        watch_max = int(selection.get("maximum_opening_watchlist", 0) or 0)
        if not (0 < watch_max <= premarket_max <= 30):
            errors.append("candidate_limits_invalid")
    else:
        errors.append("candidate_selection_must_be_object")
    return sorted(set(errors))


def _validate_weights(config: Mapping[str, Any], key: str, expected: float, errors: list[str]) -> None:
    section = config.get(key)
    weights = section.get("weights") if isinstance(section, Mapping) else None
    if not isinstance(weights, Mapping) or not weights:
        errors.append(f"{key}_weights_must_be_object")
        return
    if any(_number(value) < 0 for value in weights.values()):
        errors.append(f"{key}_weights_must_be_nonnegative")
    if abs(sum(_number(value) for value in weights.values()) - expected) > 1e-9:
        errors.append(f"{key}_weights_must_sum_to_{int(expected)}")


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

