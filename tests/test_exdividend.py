from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from scripts import run_get_exdividend_date as exdiv


def test_next_us_trading_day_skips_weekend():
    now = datetime(2026, 6, 5, 12, tzinfo=ZoneInfo("America/New_York"))
    assert exdiv.next_us_trading_day(now=now) == "2026-06-08"


def test_analyze_dividend_history_scores_stable_growth():
    df = pd.DataFrame({
        "date": pd.to_datetime([
            "2022-03-01",
            "2023-03-01",
            "2024-03-01",
            "2025-03-01",
            "2026-03-01",
        ]),
        "dividend": [1.00, 1.05, 1.10, 1.20, 1.25],
    })

    out = exdiv.analyze_dividend_history(df, years=5)
    assert out["years_paid"] >= 5
    assert out["annual_dividend_cagr"] is not None
    assert out["dividend_stability_score"] >= 4


def test_build_discord_report_includes_quality_fields():
    df = pd.DataFrame([
        {
            "symbol": "ABC",
            "tier": "A",
            "score": 8,
            "dividend": 0.25,
            "dividend_yield": 0.035,
            "payout_ratio": 0.45,
            "market_cap": 12_000_000_000,
            "sector": "Industrials",
            "years_paid_5y": 5,
            "payment_date": "2026-06-20",
            "warnings": "",
        }
    ])

    report = exdiv.build_discord_report("2026-06-04", df, top_n=5)
    assert "Target ex-dividend date: 2026-06-04" in report
    assert "ABC | Tier A | Score 8" in report
    assert "Yield 3.50%" in report
