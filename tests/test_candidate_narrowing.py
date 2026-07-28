from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import load_market_config
from app.pipeline.candidate_builder import narrow_candidates
from app.pipeline.news_processor import classify_news_catalysts
from app.pipeline.premarket_features import apply_premarket_eligibility


def _features(**overrides):
    values = {
        "price": 100.0,
        "average_daily_dollar_volume": 500_000_000.0,
        "premarket_dollar_volume": 5_000_000.0,
        "spread_bps": 10.0,
        "premarket_relative_volume": 1.0,
        "gap_percent": 0.1,
        "relative_strength_mean": 0.005,
        "catalyst": {"confirmed": False},
        "data_reason_codes": [],
    }
    values.update(overrides)
    return values


def test_relative_strength_candidate_can_qualify_without_news():
    filters = load_market_config()["premarket_filters"]

    result = apply_premarket_eligibility(_features(), filters)

    assert result["eligible"] is True
    assert result["qualified"] is True
    assert result["candidate_type"] == "RELATIVE_STRENGTH"


def test_confirmed_catalyst_candidate_can_qualify_without_relative_strength():
    filters = load_market_config()["premarket_filters"]

    result = apply_premarket_eligibility(
        _features(relative_strength_mean=0.0, catalyst={"confirmed": True}),
        filters,
    )

    assert result["qualified"] is True
    assert result["candidate_type"] == "EVENT_DRIVEN"


def test_wide_spread_and_stale_data_fail_closed():
    filters = load_market_config()["premarket_filters"]

    wide = apply_premarket_eligibility(_features(spread_bps=100.0), filters)
    stale = apply_premarket_eligibility(
        _features(data_reason_codes=["STALE_MARKET_DATA"]),
        filters,
    )

    assert wide["qualified"] is False
    assert "PREMARKET_SPREAD_TOO_WIDE" in wide["reason_codes"]
    assert stale["qualified"] is False
    assert "STALE_MARKET_DATA" in stale["reason_codes"]


def test_missing_market_or_sector_benchmark_fails_closed():
    filters = load_market_config()["premarket_filters"]

    market = apply_premarket_eligibility(_features(benchmark_data_complete=False), filters)
    sector = apply_premarket_eligibility(_features(sector_data_complete=False), filters)

    assert market["qualified"] is False
    assert "MISSING_BENCHMARK_DATA" in market["reason_codes"]
    assert sector["qualified"] is False
    assert "MISSING_SECTOR_ETF_DATA" in sector["reason_codes"]


def test_candidate_and_sector_limits_do_not_backfill_weak_names():
    candidates = [
        {"symbol": "A", "sector": "Technology", "premarket_score": 90},
        {"symbol": "B", "sector": "Technology", "premarket_score": 80},
        {"symbol": "C", "sector": "Technology", "premarket_score": 70},
        {"symbol": "D", "sector": "Financials", "premarket_score": 60},
        {"symbol": "E", "sector": "Energy", "premarket_score": 54},
    ]

    selected = narrow_candidates(
        candidates,
        minimum_score=55,
        maximum_candidates=4,
        maximum_per_sector=2,
    )

    assert [item["symbol"] for item in selected] == ["A", "B", "D"]


def test_only_configured_credible_news_source_confirms_a_catalyst():
    as_of = datetime(2026, 7, 16, 8, 45, tzinfo=ZoneInfo("America/New_York"))
    items = [
        {
            "title": "Apple raises guidance",
            "url": "https://untrusted.example/aapl",
            "published_at": "2026-07-16T08:00:00-04:00",
            "related_symbols": ["AAPL"],
        },
        {
            "title": "Microsoft raises guidance",
            "url": "https://reuters.com/msft",
            "published_at": "2026-07-16T08:00:00-04:00",
            "related_symbols": ["MSFT"],
        },
    ]
    result = classify_news_catalysts(
        items,
        allowed_symbols=["AAPL", "MSFT"],
        as_of=as_of,
        config={
            "maximum_age_hours": 48,
            "credible_domains": ["reuters.com"],
            "catalyst_keywords": {"GUIDANCE": ["guidance"]},
        },
    )

    assert result["AAPL"][0]["confirmed"] is False
    assert "UNVERIFIED_SOURCE" in result["AAPL"][0]["reason_codes"]
    assert result["MSFT"][0]["confirmed"] is True
