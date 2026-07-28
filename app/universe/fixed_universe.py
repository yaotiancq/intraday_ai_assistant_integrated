from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.config import load_market_config
from app.models.universe_models import BenchmarkDefinition, FixedUniverseSnapshot, StockDefinition
from app.validators.configuration_validator import validate_market_configuration


def build_fixed_universe(
    trade_date: date | datetime | str | Mapping[str, Any] | Any,
    market_config: Mapping[str, Any] | Any | str | Path | None = None,
) -> FixedUniverseSnapshot:
    """Build the immutable membership snapshot used for one trading date.

    Configuration is copied into typed frozen models. No runtime observation can
    add, remove, or replace a member of this snapshot.
    """

    # Also accept ``build_fixed_universe(config, trade_date)`` for callers that
    # naturally put the configuration first.
    if isinstance(trade_date, Mapping) or hasattr(trade_date, "market_config"):
        if not isinstance(market_config, (date, datetime, str)):
            raise TypeError("trade_date is required when configuration is the first argument")
        config_value = trade_date
        trade_date_value = market_config
    else:
        config_value = market_config
        trade_date_value = trade_date

    config = _coerce_config(config_value)
    errors = validate_market_configuration(config)
    if errors:
        raise ValueError("invalid fixed-universe configuration: " + "; ".join(errors))

    normalized_date = _coerce_date(trade_date_value)
    universe = config["universe"]
    stocks = tuple(StockDefinition.from_dict(item) for item in universe["stocks"])
    benchmarks: list[BenchmarkDefinition] = []
    benchmark_config = config["benchmarks"]
    for config_key, category in (
        ("broad_market", "broad_market"),
        ("sectors", "sector"),
        ("industries", "industry"),
    ):
        for item in benchmark_config[config_key]:
            benchmarks.append(BenchmarkDefinition(
                symbol=str(item["symbol"]).strip().upper(),
                category=category,
                name_en=str(item["name_en"]).strip(),
                name_zh=str(item["name_zh"]).strip(),
            ))
    volatility_proxy = str(benchmark_config.get("volatility_proxy", "")).strip().upper()
    if volatility_proxy and volatility_proxy not in {item.symbol for item in benchmarks}:
        benchmarks.append(BenchmarkDefinition(
            symbol=volatility_proxy,
            category="volatility",
            name_en="Volatility Proxy",
            name_zh="波动率指标",
        ))

    return FixedUniverseSnapshot(
        trade_date=normalized_date.isoformat(),
        strategy_version=str(config["strategy_version"]),
        mode=str(universe["mode"]),
        stocks=stocks,
        benchmarks=tuple(benchmarks),
    )


def fixed_universe_from_config(
    market_config: Mapping[str, Any] | Any | str | Path,
    trade_date: date | datetime | str,
) -> FixedUniverseSnapshot:
    return build_fixed_universe(trade_date, market_config=market_config)


def load_fixed_universe(
    trade_date: date | datetime | str,
    market_config: Mapping[str, Any] | Any | str | Path | None = None,
) -> FixedUniverseSnapshot:
    return build_fixed_universe(trade_date, market_config=market_config)


def require_allowed_symbols(
    universe: FixedUniverseSnapshot,
    symbols: Iterable[str],
    *,
    stocks_only: bool = False,
) -> tuple[str, ...]:
    """Normalize a request and reject every symbol outside the snapshot."""

    normalized = tuple(_normalize_symbol(symbol) for symbol in symbols)
    allowed = frozenset(universe.stock_symbols if stocks_only else universe.allowed_symbols)
    outside = sorted(set(normalized) - allowed)
    if outside:
        raise ValueError("symbols outside fixed universe: " + ",".join(outside))
    return normalized


def sector_distribution(universe: FixedUniverseSnapshot) -> dict[str, int]:
    result: dict[str, int] = {}
    for stock in universe.stocks:
        result[stock.sector] = result.get(stock.sector, 0) + 1
    return dict(sorted(result.items()))


def comparison_etf_map(universe: FixedUniverseSnapshot) -> dict[str, list[str]]:
    return {stock.symbol: list(stock.comparison_etfs) for stock in universe.stocks}


def _coerce_config(value: Mapping[str, Any] | Any | str | Path | None) -> Mapping[str, Any]:
    if value is None or isinstance(value, (str, Path)):
        return load_market_config(value)
    if hasattr(value, "market_config"):
        value = value.market_config
    if not isinstance(value, Mapping):
        raise TypeError("market_config must be a mapping, Settings, path, or None")
    return value


def _coerce_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if symbol.startswith("US."):
        symbol = symbol.split(".", 1)[1]
    if not symbol:
        raise ValueError("empty symbol")
    return symbol
