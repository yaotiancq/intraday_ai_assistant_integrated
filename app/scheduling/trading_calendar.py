from __future__ import annotations

"""Centralized NYSE session and timezone-aware cutoff handling."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_TIMEZONE = "America/New_York"
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)


class TradingCalendarError(RuntimeError):
    """Raised for invalid calendar configuration or dates."""


@dataclass(frozen=True)
class TradingSession:
    trade_date: str
    is_trading_day: bool
    timezone: str
    market_open: datetime | None
    market_close: datetime | None
    early_close: bool
    reason_codes: tuple[str, ...] = ()
    source: str = "NYSE"

    @property
    def date(self) -> date:
        return date.fromisoformat(self.trade_date)

    @property
    def open_at(self) -> datetime | None:
        return self.market_open

    @property
    def close_at(self) -> datetime | None:
        return self.market_close

    def to_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "is_trading_day": self.is_trading_day,
            "timezone": self.timezone,
            "market_open": self.market_open.isoformat() if self.market_open else None,
            "market_close": self.market_close.isoformat() if self.market_close else None,
            "early_close": self.early_close,
            "reason_codes": list(self.reason_codes),
            "source": self.source,
        }


class TradingCalendar:
    """NYSE-aware calendar with explicit local closure overrides.

    The preferred source is ``pandas_market_calendars`` (already declared as a
    project dependency).  A deterministic NYSE-rule fallback keeps scheduling
    safe in lightweight environments; it is intentionally conservative and
    centralizes the rules here rather than scattering weekday checks.
    """

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        timezone: str | None = None,
        configured_non_trading_days: Iterable[str | date | Mapping[str, Any]] | None = None,
        configured_early_closes: Iterable[str | date | Mapping[str, Any]] | None = None,
        calendar_name: str = "NYSE",
    ) -> None:
        config = config or {}
        timezone_name = timezone or str(config.get("timezone", DEFAULT_TIMEZONE))
        try:
            self.tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise TradingCalendarError(f"unknown scheduler timezone: {timezone_name}") from exc
        self.timezone = timezone_name
        self.calendar_name = calendar_name
        closure_values = (
            configured_non_trading_days
            if configured_non_trading_days is not None
            else config.get("configured_non_trading_days", ())
        )
        early_close_values = (
            configured_early_closes
            if configured_early_closes is not None
            else config.get("configured_early_closes", ())
        )
        self._configured_closures = _parse_date_overrides(closure_values, "CONFIGURED_NON_TRADING_DAY")
        self._configured_early_closes = _parse_date_overrides(early_close_values, "CONFIGURED_EARLY_CLOSE")
        self._exchange_calendar: Any | None = None
        self._exchange_import_attempted = False

    def session(self, value: str | date | datetime) -> TradingSession:
        session_date = _as_date(value, self.tz)
        configured_reason = self._configured_closures.get(session_date)
        if configured_reason is not None:
            return TradingSession(
                trade_date=session_date.isoformat(),
                is_trading_day=False,
                timezone=self.timezone,
                market_open=None,
                market_close=None,
                early_close=False,
                reason_codes=(configured_reason,),
                source="CONFIGURATION",
            )

        exchange_session = self._exchange_session(session_date)
        if not exchange_session.is_trading_day:
            return exchange_session

        configured_early_reason = self._configured_early_closes.get(session_date)
        if configured_early_reason is None:
            return exchange_session
        close_at = datetime.combine(session_date, EARLY_CLOSE, tzinfo=self.tz)
        reasons = tuple(dict.fromkeys((*exchange_session.reason_codes, configured_early_reason)))
        return TradingSession(
            trade_date=exchange_session.trade_date,
            is_trading_day=True,
            timezone=self.timezone,
            market_open=exchange_session.market_open,
            market_close=close_at,
            early_close=True,
            reason_codes=reasons,
            source=f"{exchange_session.source}+CONFIGURATION",
        )

    # Common naming variants kept deliberately small for call-site clarity.
    session_for = session
    get_session = session

    def is_trading_day(self, value: str | date | datetime) -> bool:
        return self.session(value).is_trading_day

    def is_early_close(self, value: str | date | datetime) -> bool:
        return self.session(value).early_close

    def local_datetime(self, value: str | date, hhmm: str | time) -> datetime:
        session_date = _as_date(value, self.tz)
        clock_time = parse_hhmm(hhmm) if isinstance(hhmm, str) else hhmm
        if clock_time.tzinfo is not None:
            raise TradingCalendarError("schedule clock times must not carry a separate timezone")
        return datetime.combine(session_date, clock_time, tzinfo=self.tz)

    def stage_cutoff(
        self,
        trade_date: str | date,
        stage_config_or_time: Mapping[str, Any] | str | time,
    ) -> datetime:
        """Build a DST-safe cutoff, preferring ``evidence_cutoff`` over time."""

        if isinstance(stage_config_or_time, Mapping):
            value = stage_config_or_time.get("evidence_cutoff", stage_config_or_time.get("time"))
            if value is None:
                raise TradingCalendarError("stage configuration needs time or evidence_cutoff")
        else:
            value = stage_config_or_time
        return self.local_datetime(trade_date, value)

    def _exchange_session(self, session_date: date) -> TradingSession:
        exchange = self._load_exchange_calendar()
        if exchange is not None:
            try:
                schedule = exchange.schedule(
                    start_date=session_date.isoformat(),
                    end_date=session_date.isoformat(),
                )
                if schedule.empty:
                    return _closed_session(session_date, self.timezone, self.tz, source=self.calendar_name)
                row = schedule.iloc[0]
                market_open = _as_local_datetime(row["market_open"], self.tz)
                market_close = _as_local_datetime(row["market_close"], self.tz)
                early_close = market_close.timetz().replace(tzinfo=None) < REGULAR_CLOSE
                reasons = ("EARLY_CLOSE",) if early_close else ()
                return TradingSession(
                    trade_date=session_date.isoformat(),
                    is_trading_day=True,
                    timezone=self.timezone,
                    market_open=market_open,
                    market_close=market_close,
                    early_close=early_close,
                    reason_codes=reasons,
                    source=self.calendar_name,
                )
            except Exception:
                # Scheduler availability must not depend on a pandas API detail;
                # the centralized fallback below still applies exchange rules.
                pass
        return _builtin_nyse_session(session_date, self.timezone, self.tz)

    def _load_exchange_calendar(self) -> Any | None:
        if self._exchange_import_attempted:
            return self._exchange_calendar
        self._exchange_import_attempted = True
        try:
            import pandas_market_calendars as mcal

            self._exchange_calendar = mcal.get_calendar(self.calendar_name)
        except (ImportError, ModuleNotFoundError, RuntimeError):
            self._exchange_calendar = None
        return self._exchange_calendar


def parse_hhmm(value: str) -> time:
    """Parse strict 24-hour ``HH:MM`` schedule text."""

    parts = value.strip().split(":")
    if len(parts) != 2 or any(len(part) != 2 or not part.isdigit() for part in parts):
        raise TradingCalendarError(f"invalid schedule time: {value!r}")
    hour, minute = (int(part) for part in parts)
    try:
        return time(hour, minute)
    except ValueError as exc:
        raise TradingCalendarError(f"invalid schedule time: {value!r}") from exc


def _parse_date_overrides(
    values: Iterable[str | date | Mapping[str, Any]] | Any,
    default_reason: str,
) -> dict[date, str]:
    if values is None:
        return {}
    if isinstance(values, (str, date, Mapping)):
        values = [values]
    parsed: dict[date, str] = {}
    for item in values:
        if isinstance(item, Mapping):
            raw_date = item.get("date", item.get("trade_date"))
            reason = str(item.get("reason_code", item.get("reason", default_reason))).strip() or default_reason
        else:
            raw_date = item
            reason = default_reason
        try:
            override_date = raw_date if isinstance(raw_date, date) else date.fromisoformat(str(raw_date))
        except (TypeError, ValueError) as exc:
            raise TradingCalendarError(f"invalid configured calendar date: {raw_date!r}") from exc
        parsed[override_date] = reason
    return parsed


def _as_date(value: str | date | datetime, timezone: ZoneInfo) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TradingCalendarError("calendar datetimes must be timezone-aware")
        return value.astimezone(timezone).date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise TradingCalendarError(f"invalid calendar date: {value!r}") from exc


def _as_local_datetime(value: Any, timezone: ZoneInfo) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise TradingCalendarError("exchange calendar returned a naive timestamp")
    return value.astimezone(timezone)


def _closed_session(
    session_date: date,
    timezone_name: str,
    timezone: ZoneInfo,
    *,
    source: str,
) -> TradingSession:
    reason = "WEEKEND" if session_date.weekday() >= 5 else "EXCHANGE_HOLIDAY"
    return TradingSession(
        trade_date=session_date.isoformat(),
        is_trading_day=False,
        timezone=timezone_name,
        market_open=None,
        market_close=None,
        early_close=False,
        reason_codes=(reason,),
        source=source,
    )


def _builtin_nyse_session(session_date: date, timezone_name: str, timezone: ZoneInfo) -> TradingSession:
    if session_date.weekday() >= 5 or session_date in _nyse_holidays(session_date.year):
        return _closed_session(session_date, timezone_name, timezone, source="NYSE_RULES")
    early_close = session_date in _nyse_early_closes(session_date.year)
    return TradingSession(
        trade_date=session_date.isoformat(),
        is_trading_day=True,
        timezone=timezone_name,
        market_open=datetime.combine(session_date, REGULAR_OPEN, tzinfo=timezone),
        market_close=datetime.combine(session_date, EARLY_CLOSE if early_close else REGULAR_CLOSE, tzinfo=timezone),
        early_close=early_close,
        reason_codes=("EARLY_CLOSE",) if early_close else (),
        source="NYSE_RULES",
    )


@lru_cache(maxsize=64)
def _nyse_holidays(year: int) -> frozenset[date]:
    holidays: set[date] = set()
    # Include adjacent New Year observance when January 1 falls on Saturday.
    for candidate_year in (year, year + 1):
        observed = _observed_fixed_holiday(date(candidate_year, 1, 1))
        if observed.year == year:
            holidays.add(observed)
    holidays.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day
    holidays.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    holidays.add(_easter_sunday(year) - timedelta(days=2))  # Good Friday
    holidays.add(_last_weekday(year, 5, 0))  # Memorial Day
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(date(year, 6, 19)))
    holidays.add(_observed_fixed_holiday(date(year, 7, 4)))
    holidays.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    holidays.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    holidays.add(_observed_fixed_holiday(date(year, 12, 25)))
    # Known full-market national days of mourning in the modern data range.
    holidays.update(
        item
        for item in (date(2004, 6, 11), date(2007, 1, 2), date(2018, 12, 5), date(2025, 1, 9))
        if item.year == year
    )
    return frozenset(holidays)


@lru_cache(maxsize=64)
def _nyse_early_closes(year: int) -> frozenset[date]:
    candidates: set[date] = set()
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates.add(thanksgiving + timedelta(days=1))
    christmas_eve = date(year, 12, 24)
    if christmas_eve.weekday() < 5:
        candidates.add(christmas_eve)
    july_fourth = date(year, 7, 4)
    if july_fourth.weekday() in (1, 2, 3, 4):  # Tue-Fri: preceding calendar day
        candidates.add(july_fourth - timedelta(days=1))
    elif july_fourth.weekday() == 5:  # Saturday: Thursday July 2
        candidates.add(july_fourth - timedelta(days=2))
    return frozenset(
        item
        for item in candidates
        if item.weekday() < 5 and item not in _nyse_holidays(year)
    )


def _observed_fixed_holiday(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        following = date(year + 1, 1, 1)
    else:
        following = date(year, month + 1, 1)
    candidate = following - timedelta(days=1)
    return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter (Meeus/Jones/Butcher algorithm)."""

    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def is_nyse_trading_day(
    value: str | date | datetime,
    *,
    configured_non_trading_days: Iterable[str | date | Mapping[str, Any]] = (),
) -> bool:
    """Functional convenience wrapper for one-off checks."""

    return TradingCalendar(configured_non_trading_days=configured_non_trading_days).is_trading_day(value)
