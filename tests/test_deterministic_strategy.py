from __future__ import annotations

import json

import pytest

from app.indicators.opening_metrics import compute_opening_metrics
from app.scoring.opening_score import DEFAULT_WEIGHTS as OPENING_WEIGHTS, score_opening
from app.scoring.premarket_score import DEFAULT_WEIGHTS as PREMARKET_WEIGHTS, score_premarket
from app.strategy.deterministic_engine import run_deterministic_strategy
from app.strategy.risk_gates import evaluate_risk_gates


TRADE_DATE = "2026-07-16"


def _bar(minute: int, price: float, *, volume: int = 100) -> dict:
    return {
        "timestamp": f"{TRADE_DATE}T09:{minute:02d}:00-04:00",
        "open": price,
        "high": price + 0.2,
        "low": price - 0.1,
        "close": price + 0.1,
        "volume": volume,
    }


def test_opening_cutoff_is_exclusive_and_vwap_resets_at_0930():
    bars = [
        {**_bar(29, 50), "timestamp": f"{TRADE_DATE}T09:29:00-04:00"},
        _bar(30, 100),
        _bar(34, 101),
        _bar(35, 500),
    ]
    metrics = compute_opening_metrics(
        bars,
        trade_date=TRADE_DATE,
        evidence_cutoff="09:35",
        atr=5,
        expected_opening_volume=200,
    )
    assert metrics["completed_bar_count"] == 2
    assert metrics["current_price"] == pytest.approx(101.1)
    assert 99 < metrics["regular_session_vwap"] < 102


def test_scorecards_expose_independent_weighted_factor_breakdowns():
    pm_scores = {name: {"long": 80, "short": 20} for name in PREMARKET_WEIGHTS}
    opening_scores = {name: {"long": 90, "short": 10} for name in OPENING_WEIGHTS}
    premarket = score_premarket(
        {"factor_scores": pm_scores},
        {"premarket_scoring": {"weights": PREMARKET_WEIGHTS}},
        trade_date=TRADE_DATE,
        evidence_cutoff="08:45",
    )
    opening = score_opening(
        {"factor_scores": opening_scores},
        {"opening_scoring": {"weights": OPENING_WEIGHTS, "penalties": {}}},
        trade_date=TRADE_DATE,
        evidence_cutoff="09:45",
    )
    assert premarket["long_score"] == pytest.approx(80)
    assert premarket["short_score"] == pytest.approx(20)
    assert opening["long_score"] == pytest.approx(90)
    assert opening["short_score"] == pytest.approx(10)
    assert sum(item["weighted_contribution"] for item in premarket["long_factor_breakdown"].values()) == pytest.approx(80)
    json.dumps({"premarket": premarket, "opening": opening})


def _engine_evidence(*, spread_bps: float = 8.0) -> dict:
    return {
        "current_price": 102.0,
        "initial_5m_high": 101.0,
        "initial_5m_low": 99.0,
        "opening_range_high": 102.2,
        "opening_range_low": 99.0,
        "opening_range_atr_ratio": 0.64,
        "opening_range_close_location": 0.94,
        "opening_return": 0.015,
        "first_bar_return": 0.004,
        "regular_session_vwap": 100.5,
        "distance_from_vwap_atr": 0.30,
        "opening_relative_volume": 1.8,
        "volume_acceleration": 1.2,
        "breakout_distance_atr": 0.20,
        "bullish_sequence_ratio": 0.9,
        "bearish_sequence_ratio": 0.0,
        "market_direction": "UP",
        "sector_direction": "UP",
        "gap_return": 0.01,
        "gap_retention": 1.0,
        "atr": 5.0,
        "spread_bps": spread_bps,
        "average_daily_dollar_volume": 1_000_000_000,
        "premarket_dollar_volume": 5_000_000,
        "gap_atr_ratio": 0.2,
        "initial_stop_reference": 99.0,
        "target_reference": 105.0,
        "factor_scores": {name: {"long": 90, "short": 10} for name in OPENING_WEIGHTS},
        "premarket_scorecard": {
            "premarket_score": 80,
            "long_score": 80,
            "short_score": 20,
            "direction": "LONG",
        },
    }


def test_engine_uses_40_60_combination_and_builds_observable_plan():
    config = {
        "opening_scoring": {"weights": OPENING_WEIGHTS, "penalties": {}},
        "combined_scoring": {"premarket_weight": 0.4, "opening_weight": 0.6},
        "risk_gates": {"maximum_spread_bps": 30, "minimum_reward_risk_ratio": 1.5},
        "risk_management": {
            "maximum_risk_per_trade_dollars": 150,
            "maximum_position_notional_dollars": 10_000,
            "estimated_slippage_bps": 5,
        },
    }
    result = run_deterministic_strategy(
        _engine_evidence(),
        config,
        trade_date=TRADE_DATE,
        evidence_cutoff="10:00",
        stage="opening_15m",
    )
    assert result["evidence_cutoff"].endswith("09:45:00-04:00")
    assert result["combined_long_score"] == pytest.approx(80 * 0.4 + 90 * 0.6)
    assert result["decision"] == "CONFIRMED_LONG"
    assert result["entry_plan"]["initial_stop_reference"] == 99.0
    assert result["entry_plan"]["first_target_reference"] == 105.0
    assert result["entry_plan"]["position_sizing"]["analysis_share_quantity"] > 0


def test_hard_spread_gate_overrides_high_score():
    result = run_deterministic_strategy(
        _engine_evidence(spread_bps=50),
        {
            "opening_scoring": {"weights": OPENING_WEIGHTS, "penalties": {}},
            "risk_gates": {"maximum_spread_bps": 30, "minimum_reward_risk_ratio": 1.5},
        },
        trade_date=TRADE_DATE,
        evidence_cutoff="09:45",
        stage="opening_15m",
    )
    assert result["combined_score"] > 80
    assert result["decision"] == "REJECTED"
    assert "SPREAD_ABOVE_HARD_MAXIMUM" in result["risk_gates"]["failures"]


def test_invalid_structural_stop_forces_no_trade():
    evidence = _engine_evidence()
    evidence["initial_stop_reference"] = 103.0
    result = run_deterministic_strategy(
        evidence,
        {
            "opening_scoring": {"weights": OPENING_WEIGHTS, "penalties": {}},
            "risk_gates": {"maximum_spread_bps": 30, "minimum_reward_risk_ratio": 1.5},
        },
        trade_date=TRADE_DATE,
        evidence_cutoff="09:45",
        stage="opening_15m",
    )
    assert result["decision"] == "NO_TRADE"
    assert "NO_VALID_STOP_LOCATION" in result["risk_gates"]["failures"]


def test_empty_bar_evidence_fails_missing_bar_gate():
    gates = evaluate_risk_gates(
        {"bars": [], "expected_bar_count": 5},
        {},
        trade_date=TRADE_DATE,
        evidence_cutoff="09:35",
    )
    assert gates["passed"] is False
    assert "MISSING_BARS" in gates["failures"]
