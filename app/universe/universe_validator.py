from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.data_sources.market_data import MARKET_TIMEZONE, MarketDataSource
from app.models.universe_models import FixedUniverseSnapshot, UniverseHealthResult, UniverseHealthState
from app.universe.fixed_universe import comparison_etf_map, sector_distribution


_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")


@dataclass(frozen=True)
class UniverseValidationReport:
    trade_date: str
    as_of: str
    configured_symbol_count: int
    configured_benchmark_count: int
    duplicate_symbol_check: bool
    sector_distribution: dict[str, int]
    sector_distribution_zh: dict[str, int]
    benchmark_mappings: dict[str, list[str]]
    unavailable_symbols: tuple[str, ...]
    data_quality_warnings: tuple[str, ...]
    results: tuple[UniverseHealthResult, ...]

    @property
    def active_symbols(self) -> tuple[str, ...]:
        return tuple(result.symbol for result in self.results if result.state is UniverseHealthState.ACTIVE)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "as_of": self.as_of,
            "configured_symbol_count": self.configured_symbol_count,
            "configured_benchmark_count": self.configured_benchmark_count,
            "duplicate_symbol_check": self.duplicate_symbol_check,
            "sector_distribution": dict(self.sector_distribution),
            "sector_distribution_zh": dict(self.sector_distribution_zh),
            "benchmark_mappings": {key: list(value) for key, value in self.benchmark_mappings.items()},
            "unavailable_symbols": list(self.unavailable_symbols),
            "data_quality_warnings": list(self.data_quality_warnings),
            "active_symbols": list(self.active_symbols),
            "results": [result.to_dict() for result in self.results],
        }


def validate_universe_snapshot(universe: FixedUniverseSnapshot) -> dict[str, tuple[str, ...]]:
    """Return stable, per-symbol configuration errors without acquiring data."""

    errors: dict[str, list[str]] = {}

    def add(symbol: str, reason: str) -> None:
        errors.setdefault(symbol, []).append(reason)

    stock_symbols = list(universe.stock_symbols)
    benchmark_symbols = list(universe.benchmark_symbols)
    if universe.mode != "fixed":
        add("__UNIVERSE__", "UNIVERSE_MODE_NOT_FIXED")
    if len(stock_symbols) != 30:
        add("__UNIVERSE__", f"EXPECTED_30_STOCKS_GOT_{len(stock_symbols)}")
    if len(stock_symbols) != len(set(stock_symbols)):
        add("__UNIVERSE__", "DUPLICATE_STOCK_SYMBOLS")
    if len(benchmark_symbols) != len(set(benchmark_symbols)):
        add("__UNIVERSE__", "DUPLICATE_BENCHMARK_SYMBOLS")
    if set(stock_symbols) & set(benchmark_symbols):
        add("__UNIVERSE__", "STOCK_BENCHMARK_OVERLAP")

    benchmark_set = set(benchmark_symbols)
    for stock in universe.stocks:
        if not _SYMBOL_PATTERN.fullmatch(stock.symbol):
            add(stock.symbol or "__EMPTY__", "INVALID_SYMBOL_FORMAT")
        if not stock.company_name:
            add(stock.symbol, "MISSING_COMPANY_NAME")
        if not stock.sector or not stock.sector_zh:
            add(stock.symbol, "MISSING_SECTOR_METADATA")
        if not stock.industry:
            add(stock.symbol, "MISSING_INDUSTRY_METADATA")
        if not stock.comparison_etfs:
            add(stock.symbol, "MISSING_COMPARISON_ETF")
        unknown = sorted(set(stock.comparison_etfs) - benchmark_set)
        if unknown:
            add(stock.symbol, "UNKNOWN_COMPARISON_ETF:" + ",".join(unknown))
    for benchmark in universe.benchmarks:
        if not _SYMBOL_PATTERN.fullmatch(benchmark.symbol):
            add(benchmark.symbol or "__EMPTY__", "INVALID_SYMBOL_FORMAT")
        if not benchmark.category or not benchmark.name_en or not benchmark.name_zh:
            add(benchmark.symbol, "MISSING_BENCHMARK_METADATA")
    return {symbol: tuple(sorted(set(values))) for symbol, values in sorted(errors.items())}


class UniverseValidator:
    def __init__(
        self,
        data_source: MarketDataSource | None = None,
        *,
        maximum_quote_age_seconds: int = 300,
        minimum_recent_daily_bars: int = 5,
        timezone_name: str = MARKET_TIMEZONE,
    ) -> None:
        self.data_source = data_source
        self.maximum_quote_age_seconds = max(0, int(maximum_quote_age_seconds))
        self.minimum_recent_daily_bars = max(1, int(minimum_recent_daily_bars))
        self.timezone = ZoneInfo(timezone_name)

    def validate_static(
        self,
        universe: FixedUniverseSnapshot,
        as_of: datetime | str | None = None,
    ) -> UniverseValidationReport:
        timestamp = _as_datetime(as_of, universe.trade_date, self.timezone)
        static_errors = validate_universe_snapshot(universe)
        results: list[UniverseHealthResult] = []
        for symbol in universe.stock_symbols + universe.benchmark_symbols:
            reasons = static_errors.get(symbol, ())
            results.append(UniverseHealthResult(
                symbol=symbol,
                state=UniverseHealthState.CONFIGURATION_INVALID if reasons else UniverseHealthState.ACTIVE,
                reason_codes=reasons or ("STATIC_CONFIGURATION_VALID",),
            ))
        global_errors = static_errors.get("__UNIVERSE__", ())
        warnings = tuple(global_errors)
        return _build_report(universe, timestamp, results, warnings)

    def validate_daily(
        self,
        universe: FixedUniverseSnapshot,
        as_of: datetime | str,
    ) -> UniverseValidationReport:
        timestamp = _as_datetime(as_of, universe.trade_date, self.timezone)
        static_errors = validate_universe_snapshot(universe)
        if self.data_source is None:
            raise ValueError("daily universe validation requires a market data source")

        requested = universe.stock_symbols + universe.benchmark_symbols
        try:
            snapshots = self.data_source.snapshots(requested, timestamp)
            snapshot_by_symbol = {
                _normalize_symbol(row.get("symbol")): row
                for row in snapshots
                if isinstance(row, Mapping) and _normalize_symbol(row.get("symbol"))
            }
            snapshot_error: str | None = None
        except Exception as exc:
            snapshot_by_symbol = {}
            snapshot_error = f"SNAPSHOT_REQUEST_FAILED:{_safe_error(exc)}"

        results: list[UniverseHealthResult] = []
        warnings: list[str] = list(static_errors.get("__UNIVERSE__", ()))
        for symbol in requested:
            config_reasons = static_errors.get(symbol, ())
            if config_reasons or static_errors.get("__UNIVERSE__"):
                reasons = tuple(sorted(set(config_reasons + static_errors.get("__UNIVERSE__", ()))))
                results.append(UniverseHealthResult(
                    symbol=symbol,
                    state=UniverseHealthState.CONFIGURATION_INVALID,
                    reason_codes=reasons,
                ))
                continue

            snapshot = snapshot_by_symbol.get(symbol)
            result = self._validate_symbol(symbol, snapshot, timestamp, snapshot_error)
            results.append(result)
            warnings.extend(f"{symbol}:{warning}" for warning in result.warnings)
        return _build_report(universe, timestamp, results, tuple(sorted(set(warnings))))

    def validate(
        self,
        universe: FixedUniverseSnapshot,
        as_of: datetime | str | None = None,
    ) -> UniverseValidationReport:
        if self.data_source is None:
            return self.validate_static(universe, as_of=as_of)
        return self.validate_daily(universe, as_of or datetime.combine(
            date.fromisoformat(universe.trade_date), time(8, 20), self.timezone
        ))

    def _validate_symbol(
        self,
        symbol: str,
        snapshot: Mapping[str, Any] | None,
        as_of: datetime,
        snapshot_error: str | None,
    ) -> UniverseHealthResult:
        if snapshot_error:
            return UniverseHealthResult(
                symbol=symbol,
                state=UniverseHealthState.TEMPORARILY_UNAVAILABLE,
                reason_codes=(snapshot_error,),
            )
        if snapshot is None:
            return UniverseHealthResult(
                symbol=symbol,
                state=UniverseHealthState.TEMPORARILY_UNAVAILABLE,
                reason_codes=("RECENT_QUOTE_UNAVAILABLE",),
            )

        provider_state = str(snapshot.get("status") or snapshot.get("state") or "").strip().upper()
        if snapshot.get("delisted") is True or provider_state == "DELISTED":
            return UniverseHealthResult(symbol, UniverseHealthState.DELISTED, ("DELISTING_INDICATOR",))
        changed_to = str(snapshot.get("symbol_changed_to") or snapshot.get("new_symbol") or "").strip().upper()
        if changed_to or provider_state == "SYMBOL_CHANGED":
            reason = "SYMBOL_CHANGE_INDICATOR" + (f":{changed_to}" if changed_to else "")
            return UniverseHealthResult(symbol, UniverseHealthState.SYMBOL_CHANGED, (reason,))
        if provider_state in {"TEMPORARILY_UNAVAILABLE", "UNAVAILABLE", "HALTED_NO_DATA"}:
            return UniverseHealthResult(symbol, UniverseHealthState.TEMPORARILY_UNAVAILABLE, (provider_state,))

        reasons: list[str] = []
        warnings: list[str] = []
        price = _number(snapshot.get("last", snapshot.get("effective_price")))
        if price is None or price <= 0:
            reasons.append("INVALID_RECENT_QUOTE")
        quote_time = _record_datetime(snapshot, self.timezone)
        if quote_time is None:
            reasons.append("QUOTE_TIMESTAMP_MISSING")
        else:
            age_seconds = (as_of - quote_time).total_seconds()
            if age_seconds < 0:
                reasons.append("FUTURE_QUOTE_TIMESTAMP")
            elif age_seconds > self.maximum_quote_age_seconds:
                reasons.append("STALE_RECENT_QUOTE")
        if snapshot.get("corporate_action_consistent") is False:
            reasons.append("CORPORATE_ACTION_INCONSISTENT")

        try:
            history = self.data_source.daily(
                symbol,
                start_date=as_of.date() - timedelta(days=30),
                end_date=as_of.date(),
                as_of=as_of,
            )
        except Exception as exc:
            history = []
            warnings.append("HISTORICAL_BARS_REQUEST_FAILED:" + _safe_error(exc))
        valid_bars = [bar for bar in history if _valid_daily_bar(bar)]
        if len(valid_bars) < self.minimum_recent_daily_bars:
            reasons.append("RECENT_HISTORICAL_BARS_INCOMPLETE")

        if reasons:
            return UniverseHealthResult(
                symbol=symbol,
                state=UniverseHealthState.DATA_INCOMPLETE,
                reason_codes=tuple(sorted(set(reasons))),
                warnings=tuple(sorted(set(warnings))),
            )
        return UniverseHealthResult(
            symbol=symbol,
            state=UniverseHealthState.ACTIVE,
            reason_codes=("QUOTE_AND_HISTORY_AVAILABLE",),
            warnings=tuple(sorted(set(warnings))),
        )


def validate_universe_health(
    universe: FixedUniverseSnapshot,
    data_source: MarketDataSource,
    as_of: datetime | str,
    **kwargs: Any,
) -> UniverseValidationReport:
    return UniverseValidator(data_source, **kwargs).validate_daily(universe, as_of)


def _build_report(
    universe: FixedUniverseSnapshot,
    as_of: datetime,
    results: Iterable[UniverseHealthResult],
    warnings: Iterable[str],
) -> UniverseValidationReport:
    result_tuple = tuple(results)
    unavailable = tuple(
        result.symbol for result in result_tuple if result.state is not UniverseHealthState.ACTIVE
    )
    all_symbols = universe.stock_symbols + universe.benchmark_symbols
    return UniverseValidationReport(
        trade_date=universe.trade_date,
        as_of=as_of.isoformat(),
        configured_symbol_count=len(universe.stock_symbols),
        configured_benchmark_count=len(universe.benchmark_symbols),
        duplicate_symbol_check=len(all_symbols) == len(set(all_symbols)),
        sector_distribution=sector_distribution(universe),
        sector_distribution_zh=_sector_distribution_zh(universe),
        benchmark_mappings=comparison_etf_map(universe),
        unavailable_symbols=unavailable,
        data_quality_warnings=tuple(sorted(set(warnings))),
        results=result_tuple,
    )


def _as_datetime(value: datetime | str | None, trade_date: str, timezone: ZoneInfo) -> datetime:
    if value is None:
        return datetime.combine(date.fromisoformat(trade_date), time(8, 20), timezone)
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        result = datetime.fromisoformat(text)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone)
    return result.astimezone(timezone)


def _record_datetime(row: Mapping[str, Any], timezone: ZoneInfo) -> datetime | None:
    value = row.get("timestamp") or row.get("update_time") or row.get("as_of") or row.get("data_time")
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            result = value
        else:
            text = str(value).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            result = datetime.fromisoformat(text)
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone)
        return result.astimezone(timezone)
    except (TypeError, ValueError):
        return None


def _sector_distribution_zh(universe: FixedUniverseSnapshot) -> dict[str, int]:
    result: dict[str, int] = {}
    for stock in universe.stocks:
        result[stock.sector_zh] = result.get(stock.sector_zh, 0) + 1
    return dict(sorted(result.items()))


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _valid_daily_bar(row: Mapping[str, Any]) -> bool:
    prices = [_number(row.get(key)) for key in ("open", "high", "low", "close")]
    volume = _number(row.get("volume"))
    return all(value is not None and value > 0 for value in prices) and volume is not None and volume >= 0


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if symbol.startswith("US."):
        symbol = symbol.split(".", 1)[1]
    return symbol


def _safe_error(exc: Exception) -> str:
    value = str(exc).replace("\n", " ").strip() or exc.__class__.__name__
    return value[:160]
