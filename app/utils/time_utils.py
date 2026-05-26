from __future__ import annotations

from datetime import datetime, time, date
from zoneinfo import ZoneInfo
from typing import Dict, Any


def now_local(tz_name: str = 'America/Los_Angeles') -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def fmt_dt(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M %Z')


def get_market_session(dt: datetime | None = None, tz_name: str = 'America/Los_Angeles') -> str:
    dt = dt or now_local(tz_name)
    t = dt.time()
    # U.S. regular session 06:30-13:00 PT; broad pre/post windows.
    if time(1, 0) <= t < time(6, 30):
        return 'premarket'
    if time(6, 30) <= t < time(13, 0):
        return 'regular'
    if time(13, 0) <= t < time(17, 0):
        return 'afterhours'
    return 'closed'


def is_us_trading_day(d: date | None = None) -> bool:
    d = d or datetime.now().date()
    try:
        from app.integration.trading_calendar import is_us_trading_day as _is_us_trading_day
        return _is_us_trading_day(d)
    except Exception:
        return d.weekday() < 5


def trading_context(tz_name: str = 'America/Los_Angeles') -> Dict[str, Any]:
    dt = now_local(tz_name)
    return {
        'as_of': fmt_dt(dt),
        'date': dt.date().isoformat(),
        'timezone': tz_name,
        'session': get_market_session(dt, tz_name),
        'is_trading_day': is_us_trading_day(dt.date()),
    }
