from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class UniverseHealthState(str, Enum):
    ACTIVE = "ACTIVE"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"
    SYMBOL_CHANGED = "SYMBOL_CHANGED"
    DELISTED = "DELISTED"
    CONFIGURATION_INVALID = "CONFIGURATION_INVALID"


@dataclass(frozen=True)
class StockDefinition:
    symbol: str
    company_name: str
    sector: str
    sector_zh: str
    industry: str
    comparison_etfs: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StockDefinition":
        return cls(
            symbol=str(value.get("symbol", "")).strip().upper(),
            company_name=str(value.get("company_name", "")).strip(),
            sector=str(value.get("sector", "")).strip(),
            sector_zh=str(value.get("sector_zh", "")).strip(),
            industry=str(value.get("industry", "")).strip(),
            comparison_etfs=tuple(str(x).strip().upper() for x in value.get("comparison_etfs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["comparison_etfs"] = list(self.comparison_etfs)
        return value


@dataclass(frozen=True)
class BenchmarkDefinition:
    symbol: str
    category: str
    name_en: str
    name_zh: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class FixedUniverseSnapshot:
    trade_date: str
    strategy_version: str
    mode: str
    stocks: tuple[StockDefinition, ...]
    benchmarks: tuple[BenchmarkDefinition, ...]

    @property
    def stock_symbols(self) -> tuple[str, ...]:
        return tuple(stock.symbol for stock in self.stocks)

    @property
    def benchmark_symbols(self) -> tuple[str, ...]:
        return tuple(item.symbol for item in self.benchmarks)

    @property
    def allowed_symbols(self) -> frozenset[str]:
        return frozenset(self.stock_symbols + self.benchmark_symbols)

    def stock_by_symbol(self) -> dict[str, StockDefinition]:
        return {stock.symbol: stock for stock in self.stocks}

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "strategy_version": self.strategy_version,
            "mode": self.mode,
            "stocks": [stock.to_dict() for stock in self.stocks],
            "benchmarks": [benchmark.to_dict() for benchmark in self.benchmarks],
        }


@dataclass(frozen=True)
class UniverseHealthResult:
    symbol: str
    state: UniverseHealthState
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "state": self.state.value,
            "reason_codes": list(self.reason_codes),
            "warnings": list(self.warnings),
        }

