from __future__ import annotations

import pytest

from app.strategy.setup_classifier import SUPPORTED_SETUP_TYPES, classify_setup


TRADE_DATE = "2026-07-16"


@pytest.mark.parametrize(
    ("expected", "metrics", "preferred"),
    [
        ("OPENING_DRIVE_LONG", {"current_price": 101, "regular_session_vwap": 100, "opening_return": 0.003, "first_bar_return": 0.003, "opening_range_close_location": 0.8, "opening_relative_volume": 1.5}, "LONG"),
        ("OPENING_DRIVE_SHORT", {"current_price": 99, "regular_session_vwap": 100, "opening_return": -0.003, "first_bar_return": -0.003, "opening_range_close_location": 0.2, "opening_relative_volume": 1.5}, "SHORT"),
        ("OPENING_RANGE_BREAKOUT", {"current_price": 102, "initial_5m_high": 101, "regular_session_vwap": 100, "opening_relative_volume": 2}, "LONG"),
        ("OPENING_RANGE_BREAKDOWN", {"current_price": 98, "initial_5m_low": 99, "regular_session_vwap": 100, "opening_relative_volume": 2}, "SHORT"),
        ("GAP_AND_GO_LONG", {"current_price": 101, "regular_session_vwap": 100, "gap_return": 0.02, "gap_retention": 0.8, "opening_return": 0.001}, "LONG"),
        ("GAP_AND_GO_SHORT", {"current_price": 99, "regular_session_vwap": 100, "gap_return": -0.02, "gap_retention": 0.8, "opening_return": -0.001}, "SHORT"),
        ("PREMARKET_HIGH_BREAKOUT", {"current_price": 102, "premarket_high": 101, "regular_session_vwap": 100, "opening_relative_volume": 2}, "LONG"),
        ("PREMARKET_LOW_BREAKDOWN", {"current_price": 98, "premarket_low": 99, "regular_session_vwap": 100, "opening_relative_volume": 2}, "SHORT"),
        ("VWAP_RECLAIM", {"current_price": 101, "regular_session_vwap": 100, "crossed_above_vwap": True, "opening_return": 0.001}, "LONG"),
        ("VWAP_REJECTION", {"current_price": 99, "regular_session_vwap": 100, "crossed_below_vwap": True, "opening_return": -0.001}, "SHORT"),
        ("FIRST_PULLBACK_LONG", {"current_price": 101, "regular_session_vwap": 100, "opening_return": 0.001, "long_pullback_depth": 0.2, "opening_relative_volume": 2}, "LONG"),
        ("FIRST_PULLBACK_SHORT", {"current_price": 99, "regular_session_vwap": 100, "opening_return": -0.001, "short_pullback_depth": 0.2, "opening_relative_volume": 2}, "SHORT"),
        ("FAILED_BREAKOUT", {"current_price": 100, "initial_5m_high": 101, "opening_range_high": 102, "regular_session_vwap": 99}, "LONG"),
        ("FAILED_BREAKDOWN", {"current_price": 100, "initial_5m_low": 99, "opening_range_low": 98, "regular_session_vwap": 101}, "SHORT"),
        ("FAILED_GAP_UP", {"current_price": 100, "regular_session_vwap": 100, "gap_return": 0.02, "gap_retention": 0}, "LONG"),
        ("FAILED_GAP_DOWN", {"current_price": 100, "regular_session_vwap": 100, "gap_return": -0.02, "gap_retention": 0}, "SHORT"),
        ("NO_VALID_SETUP", {"current_price": 100, "regular_session_vwap": 100}, None),
    ],
)
def test_all_supported_setup_rules_are_explicit_and_classifiable(expected, metrics, preferred):
    result = classify_setup(
        metrics,
        trade_date=TRADE_DATE,
        evidence_cutoff="09:45",
        preferred_direction=preferred,
    )

    assert result["setup_type"] == expected
    assert expected in SUPPORTED_SETUP_TYPES
    assert result["conditions"]
    if expected.startswith("FAILED_"):
        assert result["failure_override"] is True

