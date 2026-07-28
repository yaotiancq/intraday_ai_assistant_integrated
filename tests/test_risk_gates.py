from __future__ import annotations

from copy import deepcopy

import pytest

from app.strategy.risk_gates import evaluate_risk_gates


TRADE_DATE = "2026-07-16"


def _evidence():
    return {
        "current_price": 100,
        "spread_bps": 8,
        "average_daily_dollar_volume": 500_000_000,
        "premarket_dollar_volume": 5_000_000,
        "opening_relative_volume": 1.5,
        "opening_range_atr_ratio": 0.5,
        "gap_atr_ratio": 0.5,
        "distance_from_vwap_atr": 0.2,
        "breakout_distance_atr": 0.1,
    }


def _plan():
    return {
        "valid_stop": True,
        "entry_reference_level": 100,
        "initial_stop_reference": 99,
        "expected_reward_risk_ratio": 2.0,
    }


def _run(evidence, plan=None):
    return evaluate_risk_gates(
        evidence,
        {},
        trade_date=TRADE_DATE,
        evidence_cutoff="09:45",
        direction="LONG",
        entry_plan=plan or _plan(),
    )


def test_complete_liquid_structure_passes_hard_gates():
    assert _run(_evidence())["passed"] is True


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("delayed_market_data", True, "DELAYED_MARKET_DATA"),
        ("missing_benchmark_data", True, "MISSING_BENCHMARK_DATA"),
        ("missing_sector_data", True, "MISSING_SECTOR_ETF_DATA"),
        ("spread_bps", 31, "SPREAD_ABOVE_HARD_MAXIMUM"),
        ("opening_relative_volume", 0.9, "INSUFFICIENT_OPENING_RELATIVE_VOLUME"),
        ("opening_range_atr_ratio", 0.9, "OPENING_RANGE_EXCESSIVELY_WIDE"),
        ("opening_range_atr_ratio", 0.01, "OPENING_RANGE_TOO_NARROW"),
        ("gap_atr_ratio", 3.0, "GAP_EXCESSIVELY_LARGE"),
        ("distance_from_vwap_atr", 0.4, "ENTRY_EXCESSIVELY_EXTENDED_FROM_VWAP"),
        ("breakout_distance_atr", 0.3, "ENTRY_EXCESSIVELY_EXTENDED_FROM_BREAKOUT_LEVEL"),
        ("trading_halt", True, "TRADING_HALT_INDICATION"),
        ("extreme_market_conflict", True, "EXTREME_MARKET_CONFLICT"),
        ("extreme_sector_conflict", True, "EXTREME_SECTOR_CONFLICT"),
    ],
)
def test_score_independent_hard_failures(field, value, reason):
    evidence = _evidence()
    evidence[field] = value

    result = _run(evidence)

    assert result["passed"] is False
    assert reason in result["failures"]


def test_invalid_stop_and_low_reward_risk_are_hard_failures():
    invalid_stop = deepcopy(_plan())
    invalid_stop["valid_stop"] = False
    low_reward = deepcopy(_plan())
    low_reward["expected_reward_risk_ratio"] = 1.0

    assert "NO_VALID_STOP_LOCATION" in _run(_evidence(), invalid_stop)["failures"]
    assert "REWARD_RISK_BELOW_MINIMUM" in _run(_evidence(), low_reward)["failures"]

