from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.config import load_market_config
from app.data_sources.market_data import DeterministicMarketDataSource
from app.pipeline.opening_confirmation_pipeline import run_opening_confirmation_pipeline
from app.pipeline.premarket_pipeline import run_premarket_pipeline
from app.strategy.deterministic_engine import run_deterministic_strategy
from app.universe.fixed_universe import build_fixed_universe


TRADE_DATE = "2026-07-16"
ET = ZoneInfo("America/New_York")


def _bar(minute: int, close: float) -> dict:
    stamp = datetime(2026, 7, 16, 9, minute, tzinfo=ET)
    return {
        "timestamp": stamp.isoformat(),
        "bar_end": stamp.replace(minute=minute + 1).isoformat(),
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 100_000,
        "is_complete": True,
    }


def test_future_bar_is_excluded_before_features_scores_and_decisions():
    visible = [_bar(minute, 100 + (minute - 30) * 0.1) for minute in range(30, 35)]
    with_future = [*visible, _bar(35, 500)]
    common = {
        "atr": 5,
        "expected_opening_volume": 500_000,
        "spread_bps": 5,
        "average_daily_dollar_volume": 500_000_000,
        "premarket_dollar_volume": 5_000_000,
        "premarket_scorecard": {"premarket_score": 70, "long_score": 70, "short_score": 30, "direction": "LONG"},
    }

    first = run_deterministic_strategy(
        {**common, "bars": visible},
        trade_date=TRADE_DATE,
        evidence_cutoff="09:35",
        stage="opening_5m",
    )
    second = run_deterministic_strategy(
        {**common, "bars": with_future},
        trade_date=TRADE_DATE,
        evidence_cutoff="09:35",
        stage="opening_5m",
    )

    assert first["completed_bar_count"] == second["completed_bar_count"] == 5
    assert second["excluded_bar_count"] == 1
    assert second["opening_score"] == first["opening_score"]
    assert second["setup"] == first["setup"]
    assert second["decision"] == first["decision"]
    assert second["opening_metrics"]["current_price"] == pytest.approx(100.4)


def test_opening_stage_reuses_persisted_premarket_score():
    config = load_market_config()
    universe = build_fixed_universe(TRADE_DATE, config)
    source = DeterministicMarketDataSource(universe)
    premarket_cutoff = datetime(2026, 7, 16, 8, 45, tzinfo=ET)
    opening_cutoff = datetime(2026, 7, 16, 9, 35, tzinfo=ET)
    premarket = run_premarket_pipeline(
        trade_date=TRADE_DATE,
        as_of=premarket_cutoff,
        evidence_cutoff=premarket_cutoff,
        universe=universe,
        config=config,
        data_source=source,
    )
    persisted = deepcopy(premarket)
    persisted["opening_watchlist"] = persisted["opening_watchlist"][:1]
    assert persisted["opening_watchlist"]
    persisted["opening_watchlist"][0]["premarket_score"] = 61.2345

    opening = run_opening_confirmation_pipeline(
        trade_date=TRADE_DATE,
        as_of=opening_cutoff,
        evidence_cutoff=opening_cutoff,
        stage="opening_5m",
        universe=universe,
        config=config,
        data_source=source,
        premarket_snapshot=persisted,
    )

    assert opening["candidates"][0]["premarket_score"] == 61.2345
