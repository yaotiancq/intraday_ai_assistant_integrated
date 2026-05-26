from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo
import os
from typing import Optional


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class TradingDayDecision:
    is_trading_day: bool
    should_run: bool
    reason: str
    date: str
    timezone: str


def is_us_trading_day(d: date) -> bool:
    """Return whether NYSE is scheduled to trade on the given date."""
    try:
        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        sched = nyse.schedule(start_date=d.isoformat(), end_date=d.isoformat())
        return not sched.empty
    except Exception:
        # Fallback only for resilience. pandas_market_calendars should be installed in Docker.
        return d.weekday() < 5


def should_run_trading_day_task(
    tz_name: str = "America/Los_Angeles",
    force_run: bool = False,
    allow_non_trading_day_test: bool = False,
    now: Optional[datetime] = None,
) -> TradingDayDecision:
    """
    Gate daily tasks to U.S. trading days.

    Runtime overrides:
    - PREMARKET_FORCE_RUN=true: run regardless of trading-day status.
    - ALLOW_NON_TRADING_DAY_TEST=true: run for testing, but mark reason clearly.
    """
    local_now = now or datetime.now(ZoneInfo(tz_name))
    d = local_now.date()
    is_trade_day = is_us_trading_day(d)

    env_force = _as_bool(os.getenv("PREMARKET_FORCE_RUN"), False)
    env_test = _as_bool(os.getenv("ALLOW_NON_TRADING_DAY_TEST"), False)
    force = force_run or env_force
    test = allow_non_trading_day_test or env_test

    if is_trade_day:
        return TradingDayDecision(True, True, "scheduled_us_trading_day", d.isoformat(), tz_name)
    if force:
        return TradingDayDecision(False, True, "forced_run_override", d.isoformat(), tz_name)
    if test:
        return TradingDayDecision(False, True, "non_trading_day_test_override", d.isoformat(), tz_name)
    return TradingDayDecision(False, False, "not_us_trading_day", d.isoformat(), tz_name)
