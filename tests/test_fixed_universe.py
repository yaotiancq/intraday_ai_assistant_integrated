from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.data_sources.market_data import (
    DeterministicMarketDataSource,
    FutuMarketDataSource,
    ReplayDataError,
    ReplayMarketDataSource,
    SymbolNotAllowedError,
)
from app.universe.fixed_universe import build_fixed_universe, require_allowed_symbols


ET = ZoneInfo("America/New_York")


class FakeFutuClient:
    def get_market_snapshot(self, symbols):
        return [
            {"symbol": "AAPL", "update_time": "2026-07-16T09:34:30-04:00", "last": 200},
            {"symbol": "AAPL", "update_time": "2026-07-16T09:36:00-04:00", "last": 220},
        ]

    def get_realtime_kline(self, symbol, ktype="K_1M", num=1000):
        return [
            {
                "symbol": symbol,
                "time_key": "2026-07-16T09:30:00-04:00",
                "open": 200,
                "high": 201,
                "low": 199,
                "close": 200.5,
                "volume": 1000,
            },
            {
                "symbol": symbol,
                "time_key": "2026-07-16T09:35:00-04:00",
                "open": 200.5,
                "high": 202,
                "low": 200,
                "close": 201,
                "volume": 1000,
            },
        ]

    def request_history_kline(self, symbol, start=None, end=None, ktype="K_DAY"):
        return []


def test_fixed_universe_has_exactly_30_unique_stocks_and_separate_benchmarks():
    universe = build_fixed_universe("2026-07-16")

    assert universe.stock_symbols == (
        "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "TSM", "GOOGL", "META", "NFLX", "AMZN",
        "TSLA", "HD", "MCD", "JPM", "BAC", "C", "GS", "LLY", "UNH", "JNJ", "CAT", "GE",
        "BA", "XOM", "CVX", "WMT", "COST", "LIN", "NEE", "PLD",
    )
    assert len(universe.stock_symbols) == 30
    assert len(set(universe.stock_symbols)) == 30
    assert set(universe.stock_symbols).isdisjoint(universe.benchmark_symbols)
    assert set(universe.benchmark_symbols) == {
        "SPY", "QQQ", "IWM", "DIA", "XLK", "XLC", "XLY", "XLP", "XLE", "XLF", "XLV",
        "XLI", "XLB", "XLU", "XLRE", "SMH", "SOXX", "IGV", "ITA", "VIX",
    }
    assert all(stock.sector and stock.industry and stock.comparison_etfs for stock in universe.stocks)


def test_fixed_universe_rejects_arbitrary_symbols():
    universe = build_fixed_universe("2026-07-16")

    with pytest.raises(ValueError, match="outside fixed universe"):
        require_allowed_symbols(universe, ["AAPL", "SATS"], stocks_only=True)

    source = DeterministicMarketDataSource(universe)
    with pytest.raises(SymbolNotAllowedError, match="SATS"):
        source.snapshots(["SATS"], datetime(2026, 7, 16, 8, 45, tzinfo=ET))


def test_deterministic_source_is_stable_and_respects_opening_cutoffs():
    universe = build_fixed_universe("2026-07-16")
    first = DeterministicMarketDataSource(universe)
    second = DeterministicMarketDataSource(universe)
    premarket_cutoff = datetime(2026, 7, 16, 8, 45, tzinfo=ET)
    five_minute_cutoff = datetime(2026, 7, 16, 9, 35, tzinfo=ET)
    fifteen_minute_cutoff = datetime(2026, 7, 16, 9, 45, tzinfo=ET)

    assert first.snapshots(["AAPL", "SPY"], premarket_cutoff) == second.snapshots(
        ["AAPL", "SPY"], premarket_cutoff
    )
    assert len(first.minute("AAPL", "2026-07-16", five_minute_cutoff)) == 5
    assert len(first.minute("AAPL", "2026-07-16", fifteen_minute_cutoff)) == 15
    assert all(
        datetime.fromisoformat(row["bar_end"]) <= five_minute_cutoff
        for row in first.minute("AAPL", "2026-07-16", five_minute_cutoff)
    )


def test_replay_filters_future_and_incomplete_bars_and_rejects_outside_fixture_symbols():
    payload = {
        "snapshots": {
            "AAPL": [
                {"symbol": "AAPL", "timestamp": "2026-07-16T08:45:00-04:00", "last": 200},
                {"symbol": "AAPL", "timestamp": "2026-07-16T10:00:00-04:00", "last": 220},
            ]
        },
        "minute_bars": {
            "AAPL": [
                {
                    "symbol": "AAPL",
                    "timestamp": "2026-07-16T09:30:00-04:00",
                    "bar_end": "2026-07-16T09:31:00-04:00",
                    "open": 200,
                    "high": 201,
                    "low": 199,
                    "close": 200.5,
                    "volume": 1000,
                    "is_complete": True,
                },
                {
                    "symbol": "AAPL",
                    "timestamp": "2026-07-16T09:31:00-04:00",
                    "bar_end": "2026-07-16T09:32:00-04:00",
                    "open": 200.5,
                    "high": 202,
                    "low": 200,
                    "close": 201,
                    "volume": 1000,
                    "is_complete": False,
                },
                {
                    "symbol": "AAPL",
                    "timestamp": "2026-07-16T09:35:00-04:00",
                    "bar_end": "2026-07-16T09:36:00-04:00",
                    "open": 201,
                    "high": 203,
                    "low": 201,
                    "close": 202,
                    "volume": 1000,
                    "is_complete": True,
                },
            ]
        },
    }
    source = ReplayMarketDataSource(payload, allowed_symbols=["AAPL"])

    snapshots = source.snapshots(["AAPL"], datetime(2026, 7, 16, 8, 45, tzinfo=ET))
    bars = source.minute("AAPL", "2026-07-16", datetime(2026, 7, 16, 9, 35, tzinfo=ET))

    assert snapshots[0]["last"] == 200
    assert [row["timestamp"] for row in bars] == ["2026-07-16T09:30:00-04:00"]

    bad_payload = {"snapshots": {"SATS": {"symbol": "SATS", "timestamp": "2026-07-16T08:45:00-04:00"}}}
    with pytest.raises(ReplayDataError, match="SATS"):
        ReplayMarketDataSource(bad_payload, allowed_symbols=["AAPL"])


def test_futu_adapter_filters_provider_rows_to_requested_cutoff():
    source = FutuMarketDataSource(["AAPL"], client=FakeFutuClient())
    cutoff = datetime(2026, 7, 16, 9, 35, tzinfo=ET)

    snapshots = source.snapshots(["AAPL"], cutoff)
    minute_bars = source.minute("AAPL", "2026-07-16", cutoff)

    assert [row["last"] for row in snapshots] == [200]
    assert [row["timestamp"] for row in minute_bars] == ["2026-07-16T09:30:00-04:00"]
