from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.scheduling.trading_calendar import TradingCalendar, TradingCalendarError


def test_nyse_holidays_configured_closures_and_early_close_metadata():
    calendar = TradingCalendar(
        {
            "timezone": "America/New_York",
            "configured_non_trading_days": ["2026-07-16"],
        }
    )

    assert not calendar.session("2026-07-04").is_trading_day
    configured = calendar.session("2026-07-16")
    assert not configured.is_trading_day
    assert configured.reason_codes == ("CONFIGURED_NON_TRADING_DAY",)

    early = calendar.session("2026-11-27")  # Friday after Thanksgiving
    assert early.is_trading_day
    assert early.early_close
    assert early.market_close.hour == 13
    assert "EARLY_CLOSE" in early.reason_codes


def test_stage_cutoffs_are_timezone_aware_across_daylight_saving_time():
    calendar = TradingCalendar({"timezone": "America/New_York"})
    winter = calendar.stage_cutoff("2026-01-15", {"time": "09:35", "evidence_cutoff": "09:35"})
    summer = calendar.stage_cutoff("2026-07-16", {"time": "09:35", "evidence_cutoff": "09:35"})

    assert winter.isoformat() == "2026-01-15T09:35:00-05:00"
    assert summer.isoformat() == "2026-07-16T09:35:00-04:00"
    assert summer.astimezone(ZoneInfo("America/Los_Angeles")).hour == 6


def test_calendar_rejects_naive_datetimes():
    calendar = TradingCalendar()
    with pytest.raises(TradingCalendarError):
        calendar.session(datetime(2026, 7, 16, 9, 35))
