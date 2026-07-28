"""Exchange-aware scheduling and process-safe job coordination."""

from app.scheduling.job_lock import JobLock, LockUnavailable, LockOwner, lock_filename
from app.scheduling.trading_calendar import (
    TradingCalendar,
    TradingCalendarError,
    TradingSession,
    is_nyse_trading_day,
    parse_hhmm,
)

__all__ = [
    "JobLock",
    "LockOwner",
    "LockUnavailable",
    "MarketScheduler",
    "ScheduledStage",
    "SchedulerDecision",
    "SchedulerError",
    "ScheduleState",
    "TradingCalendar",
    "TradingCalendarError",
    "TradingSession",
    "is_nyse_trading_day",
    "lock_filename",
    "parse_hhmm",
    "stage_context",
]


def __getattr__(name: str):
    """Load scheduler orchestration lazily to avoid a repository lock cycle."""

    if name in {
        "MarketScheduler",
        "ScheduledStage",
        "SchedulerDecision",
        "SchedulerError",
        "ScheduleState",
        "stage_context",
    }:
        from app.scheduling import market_scheduler

        return getattr(market_scheduler, name)
    raise AttributeError(name)
