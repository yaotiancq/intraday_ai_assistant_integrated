from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.scoring.market_regime import classify_regime, compute_market_regime
from app.scoring.opening_score import DEFAULT_WEIGHTS as OPENING_WEIGHTS, score_opening
from app.scoring.premarket_score import DEFAULT_WEIGHTS as PREMARKET_WEIGHTS, score_premarket


TRADE_DATE = "2026-07-16"


def test_market_regime_returns_stable_classification_and_reason_codes():
    broad = [
        {"symbol": "SPY", "change_pct": 0.8},
        {"symbol": "QQQ", "change_pct": 1.1},
        {"symbol": "IWM", "change_pct": 0.6},
        {"symbol": "DIA", "change_pct": 0.4},
    ]
    sectors = [
        {"symbol": "XLK", "change_pct": 0.9},
        {"symbol": "XLF", "change_pct": 0.5},
        {"symbol": "SMH", "change_pct": 1.4},
        {"symbol": "SOXX", "change_pct": 1.2},
    ]

    first = compute_market_regime(broad, sectors)
    second = compute_market_regime(broad, sectors)

    assert first == second
    assert first["score"] > 0
    assert first["classification"] in {"RISK_ON", "STRONG_RISK_ON"}
    assert "SPY_POSITIVE" in first["reason_codes"]
    assert classify_regime(60) == "STRONG_RISK_ON"
    assert classify_regime(0) == "MIXED"
    assert classify_regime(-60) == "STRONG_RISK_OFF"


def test_market_regime_excludes_stale_benchmark_observations():
    cutoff = datetime(2026, 7, 16, 8, 45, tzinfo=ZoneInfo("America/New_York"))
    broad = [
        {"symbol": symbol, "change_pct": 1.0, "timestamp": cutoff.isoformat()}
        for symbol in ("SPY", "QQQ", "IWM", "DIA")
    ]
    broad[0]["timestamp"] = (cutoff - timedelta(minutes=5)).isoformat()

    result = compute_market_regime(broad, [], as_of=cutoff, maximum_data_age_seconds=90)

    assert result["index_changes"]["SPY"] is None
    assert "SPY:STALE_BENCHMARK_DATA" in result["data_quality_reason_codes"]
    assert "BENCHMARK_FRESHNESS_FAILURES_PRESENT" in result["reason_codes"]


def test_premarket_score_is_weighted_and_directionally_independent():
    factor_scores = {
        factor: {"long": 80.0, "short": 20.0}
        for factor in PREMARKET_WEIGHTS
    }
    result = score_premarket(
        {"factor_scores": factor_scores},
        {"premarket_scoring": {"weights": PREMARKET_WEIGHTS}},
        trade_date=TRADE_DATE,
        evidence_cutoff="08:45",
    )

    assert result["long_score"] == pytest.approx(80.0)
    assert result["short_score"] == pytest.approx(20.0)
    assert result["premarket_score"] == pytest.approx(80.0)
    assert result["direction"] == "LONG"
    assert result["directional_conflict"] is False
    assert set(result["long_factor_breakdown"]) == set(PREMARKET_WEIGHTS)
    assert sum(
        item["weighted_contribution"]
        for item in result["long_factor_breakdown"].values()
    ) == pytest.approx(80.0)
    for factor, item in result["long_factor_breakdown"].items():
        assert item["normalized_score"] == 80.0
        assert item["weight"] == PREMARKET_WEIGHTS[factor]
        assert item["reason_codes"] == ["EXPLICIT_NORMALIZED_INPUT"]
    json.dumps(result)


def test_premarket_score_rejects_weights_that_do_not_sum_to_100():
    invalid_weights = dict(PREMARKET_WEIGHTS)
    invalid_weights["liquidity"] -= 1

    with pytest.raises(ValueError, match="must sum to 100"):
        score_premarket(
            {},
            {"premarket_scoring": {"weights": invalid_weights}},
            trade_date=TRADE_DATE,
            evidence_cutoff="08:45",
        )


def test_opening_score_applies_explicit_penalty_after_weighted_score():
    factor_scores = {
        factor: {"long": 90.0, "short": 10.0}
        for factor in OPENING_WEIGHTS
    }
    result = score_opening(
        {
            "factor_scores": factor_scores,
            "failed_breakout": True,
        },
        {
            "opening_scoring": {
                "weights": OPENING_WEIGHTS,
                "penalties": {"failed_breakout": 25},
            }
        },
        trade_date=TRADE_DATE,
        evidence_cutoff="09:45",
    )

    assert result["long_gross_score"] == pytest.approx(90.0)
    assert result["long_score"] == pytest.approx(65.0)
    assert result["long_penalty_breakdown"]["failed_breakout"] == {
        "configured_penalty": 25.0,
        "applied": True,
        "deduction": 25.0,
        "reason_code": "FAILED_BREAKOUT",
    }
