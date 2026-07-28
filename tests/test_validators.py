from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import load_market_config
from app.validators.configuration_validator import validate_market_configuration
from app.validators.snapshot_validator import validate_bar_evidence, validate_snapshot_timestamp


TRADE_DATE = "2026-07-16"
EASTERN = ZoneInfo("America/New_York")


def _bar(minute: int, *, complete: bool = True) -> dict:
    price = 100.0 + (minute - 30) * 0.1
    return {
        "timestamp": f"{TRADE_DATE}T09:{minute:02d}:00-04:00",
        "open": price,
        "high": price + 0.2,
        "low": price - 0.1,
        "close": price + 0.1,
        "volume": 100_000,
        "is_complete": complete,
    }


def test_default_market_configuration_is_valid():
    config = load_market_config()

    assert validate_market_configuration(config) == []
    assert len(config["universe"]["stocks"]) == 30


def test_configuration_validator_rejects_invalid_weights_and_duplicate_stocks():
    config = deepcopy(load_market_config())
    config["premarket_scoring"]["weights"]["liquidity"] -= 1
    config["universe"]["stocks"][1]["symbol"] = config["universe"]["stocks"][0]["symbol"]

    errors = validate_market_configuration(config)

    assert "premarket_scoring_weights_must_sum_to_100" in errors
    assert "duplicate_stock_symbols:AAPL" in errors


def test_configuration_validator_rejects_unknown_comparison_etf():
    config = deepcopy(load_market_config())
    config["universe"]["stocks"][0]["comparison_etfs"] = ["NOTETF"]

    errors = validate_market_configuration(config)

    assert "unknown_comparison_etf:AAPL:NOTETF" in errors


def test_snapshot_timestamp_enforces_age_and_future_boundaries():
    cutoff = datetime(2026, 7, 16, 9, 35, tzinfo=EASTERN)

    assert validate_snapshot_timestamp(
        {"timestamp": (cutoff - timedelta(seconds=90)).isoformat()},
        as_of=cutoff,
        maximum_age_seconds=90,
    ) == []
    assert validate_snapshot_timestamp(
        {"timestamp": (cutoff - timedelta(seconds=91)).isoformat()},
        as_of=cutoff,
        maximum_age_seconds=90,
    ) == ["STALE_MARKET_DATA"]
    assert validate_snapshot_timestamp(
        {"timestamp": (cutoff + timedelta(seconds=1)).isoformat()},
        as_of=cutoff,
        maximum_age_seconds=90,
    ) == ["FUTURE_QUOTE_TIMESTAMP"]


def test_bar_at_cutoff_is_excluded_and_flagged_as_future_evidence():
    bars = [_bar(minute) for minute in range(30, 36)]

    result = validate_bar_evidence(
        bars,
        trade_date=TRADE_DATE,
        evidence_cutoff="09:35",
        session_start="09:30",
        expected_bar_count=5,
    )

    assert result["accepted_bar_count"] == 5
    assert [bar["timestamp"] for bar in result["accepted_bars"]] == [
        f"{TRADE_DATE}T09:{minute:02d}:00-04:00"
        for minute in range(30, 35)
    ]
    assert "FUTURE_OR_POST_CUTOFF_BARS" in result["reason_codes"]
    assert result["passed"] is False


def test_incomplete_bar_is_excluded_and_causes_missing_bar_failure():
    bars = [_bar(minute, complete=minute != 34) for minute in range(30, 35)]

    result = validate_bar_evidence(
        bars,
        trade_date=TRADE_DATE,
        evidence_cutoff="09:35",
        session_start="09:30",
        expected_bar_count=5,
    )

    assert result["accepted_bar_count"] == 4
    assert "INCOMPLETE_BARS" in result["reason_codes"]
    assert "MISSING_BARS" in result["reason_codes"]
    assert result["passed"] is False


def test_duplicate_and_non_monotonic_bars_are_hard_failures():
    bars = [_bar(30), _bar(32), _bar(31), _bar(31), _bar(34)]

    result = validate_bar_evidence(
        bars,
        trade_date=TRADE_DATE,
        evidence_cutoff="09:35",
        session_start="09:30",
        expected_bar_count=5,
    )

    assert "DUPLICATE_BARS" in result["reason_codes"]
    assert "NON_MONOTONIC_TIMESTAMPS" in result["reason_codes"]
    assert result["passed"] is False
