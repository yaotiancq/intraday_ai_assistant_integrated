"""Shared deterministic helpers for point-in-time bar calculations.

Bars are assumed to be start-labelled.  A one-minute bar stamped ``09:34``
represents ``[09:34, 09:35)`` and is therefore complete at a 09:35 cutoff.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
import math
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


DEFAULT_TIMEZONE = "America/New_York"
TIMESTAMP_KEYS = ("timestamp", "datetime", "bar_start", "start", "time_key", "time")


def finite_float(value: Any) -> float | None:
    """Return a finite float without allowing bools to masquerade as numbers."""

    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def parse_trade_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip())


def parse_cutoff(
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> datetime:
    """Resolve an explicit cutoff; naive inputs are interpreted in ``timezone``."""

    zone = ZoneInfo(timezone)
    day = parse_trade_date(trade_date)
    if isinstance(evidence_cutoff, datetime):
        result = evidence_cutoff
    elif isinstance(evidence_cutoff, time):
        result = datetime.combine(day, evidence_cutoff)
    else:
        raw = str(evidence_cutoff).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(raw)
        except ValueError:
            parsed_time = time.fromisoformat(raw)
            result = datetime.combine(day, parsed_time)
    if result.tzinfo is None:
        result = result.replace(tzinfo=zone)
    else:
        result = result.astimezone(zone)
    return result


def parse_bar_timestamp(
    bar: Mapping[str, Any],
    trade_date: str | date | datetime,
    timezone: str = DEFAULT_TIMEZONE,
) -> datetime | None:
    value: Any = None
    for key in TIMESTAMP_KEYS:
        if bar.get(key) is not None:
            value = bar[key]
            break
    if value is None:
        return None

    zone = ZoneInfo(timezone)
    day = parse_trade_date(trade_date)
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, time):
        result = datetime.combine(day, value)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        result = datetime.fromtimestamp(float(value), tz=zone)
    else:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(raw)
        except ValueError:
            try:
                result = datetime.combine(day, time.fromisoformat(raw))
            except ValueError:
                return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=zone)
    else:
        result = result.astimezone(zone)
    return result


def bar_interval_seconds(bar: Mapping[str, Any], default_seconds: int = 60) -> int:
    seconds = finite_float(bar.get("interval_seconds"))
    if seconds is None:
        minutes = finite_float(bar.get("interval_minutes"))
        seconds = minutes * 60.0 if minutes is not None else None
    if seconds is None:
        raw = str(bar.get("interval", "")).strip().lower()
        if raw.endswith("min"):
            seconds = (finite_float(raw[:-3]) or 0.0) * 60.0
        elif raw.endswith("m"):
            seconds = (finite_float(raw[:-1]) or 0.0) * 60.0
        elif raw.endswith("s"):
            seconds = finite_float(raw[:-1])
    return max(1, int(seconds or default_seconds))


def select_completed_bars(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    session_start: str | time | datetime | None = None,
    session_end: str | time | datetime | None = None,
    timezone: str = DEFAULT_TIMEZONE,
    default_interval_seconds: int = 60,
    sort_bars: bool = True,
) -> list[dict[str, Any]]:
    """Select complete point-in-time bars and return timestamp-sorted copies.

    The upper bound is exclusive for start labels, and the calculated bar end
    must be no later than the evidence cutoff. Explicitly incomplete bars are
    always excluded.
    """

    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    day = parse_trade_date(trade_date)
    lower = parse_cutoff(day, session_start, timezone) if session_start is not None else None
    upper = parse_cutoff(day, session_end, timezone) if session_end is not None else None
    selected: list[tuple[datetime, dict[str, Any]]] = []
    for source in bars or ():
        bar = dict(source)
        stamp = parse_bar_timestamp(bar, day, timezone)
        if stamp is None or stamp.date() != day:
            continue
        if bar.get("is_complete") is False or bar.get("complete") is False:
            continue
        interval = bar_interval_seconds(bar, default_interval_seconds)
        bar_end = stamp + timedelta(seconds=interval)
        if stamp >= cutoff or bar_end > cutoff:
            continue
        if lower is not None and stamp < lower:
            continue
        if upper is not None and stamp >= upper:
            continue
        bar["_timestamp"] = stamp.isoformat()
        selected.append((stamp, bar))
    if sort_bars:
        selected.sort(key=lambda pair: pair[0])
    return [bar for _, bar in selected]


def bar_prices(bar: Mapping[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    return tuple(finite_float(bar.get(key)) for key in ("open", "high", "low", "close"))  # type: ignore[return-value]


def clean_public_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove private working fields and non-finite floats for JSON safety."""

    result: dict[str, Any] = {}
    for key, item in value.items():
        if str(key).startswith("_"):
            continue
        if isinstance(item, float) and not math.isfinite(item):
            result[str(key)] = None
        else:
            result[str(key)] = item
    return result
