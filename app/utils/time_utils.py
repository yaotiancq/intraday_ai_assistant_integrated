from __future__ import annotations

from datetime import datetime, time, date
from zoneinfo import ZoneInfo
from typing import Dict, Any


def now_local(tz_name: str = 'America/New_York') -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def fmt_dt(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M %Z')


def get_market_session(dt: datetime | None = None, tz_name: str = 'America/New_York') -> str:
    dt = dt or now_local(tz_name)
    t = dt.time()
    if time(4, 0) <= t < time(9, 30):
        return 'premarket'
    if time(9, 30) <= t < time(16, 0):
        return 'regular'
    if time(16, 0) <= t < time(20, 0):
        return 'afterhours'
    return 'closed'


def is_us_trading_day(d: date | None = None) -> bool:
    d = d or datetime.now().date()
    from app.integration.trading_calendar import is_us_trading_day as _is_us_trading_day
    return _is_us_trading_day(d)


def trading_context(tz_name: str = 'America/New_York') -> Dict[str, Any]:
    dt = now_local(tz_name)
    return {
        'as_of': fmt_dt(dt),
        'date': dt.date().isoformat(),
        'timezone': tz_name,
        'session': get_market_session(dt, tz_name),
        'is_trading_day': is_us_trading_day(dt.date()),
    }
