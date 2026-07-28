from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

from app.indicators._bars import (
    bar_interval_seconds,
    finite_float,
    parse_bar_timestamp,
    parse_cutoff,
    select_completed_bars,
)


def validate_bar_evidence(
    bars: Iterable[Mapping[str, Any]] | None,
    *,
    trade_date: str,
    evidence_cutoff: str | datetime,
    session_start: str,
    expected_bar_count: int,
    timezone: str = "America/New_York",
) -> dict[str, Any]:
    """Validate raw bars before deterministic opening calculations."""

    raw = [dict(item) for item in bars or []]
    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    lower = parse_cutoff(trade_date, session_start, timezone)
    reason_codes: list[str] = []
    previous: datetime | None = None
    seen: set[datetime] = set()
    valid_timestamp_count = 0
    for bar in raw:
        stamp = parse_bar_timestamp(bar, trade_date, timezone)
        if stamp is None:
            reason_codes.append("MISSING_BAR_TIMESTAMP")
            continue
        valid_timestamp_count += 1
        if stamp in seen:
            reason_codes.append("DUPLICATE_BARS")
        seen.add(stamp)
        if previous is not None and stamp <= previous:
            reason_codes.append("NON_MONOTONIC_TIMESTAMPS")
        previous = stamp
        if stamp.date().isoformat() != trade_date or stamp < lower:
            reason_codes.append("INCORRECT_SESSION_BARS")
        interval = bar_interval_seconds(bar)
        if stamp >= cutoff or stamp + timedelta(seconds=interval) > cutoff:
            reason_codes.append("FUTURE_OR_POST_CUTOFF_BARS")
        if bar.get("is_complete") is False or bar.get("complete") is False:
            reason_codes.append("INCOMPLETE_BARS")

        open_price = finite_float(bar.get("open"))
        high = finite_float(bar.get("high"))
        low = finite_float(bar.get("low"))
        close = finite_float(bar.get("close"))
        volume = finite_float(bar.get("volume"))
        if any(value is None or value <= 0 for value in (open_price, high, low, close)):
            reason_codes.append("INVALID_PRICES")
        elif high < max(open_price, close) or low > min(open_price, close) or low > high:
            reason_codes.append("INVALID_OHLC_RELATIONSHIP")
        if volume is None or volume < 0:
            reason_codes.append("INVALID_VOLUME")

    accepted = select_completed_bars(
        raw,
        trade_date=trade_date,
        evidence_cutoff=evidence_cutoff,
        session_start=session_start,
        timezone=timezone,
    )
    if len(accepted) < expected_bar_count:
        reason_codes.append("MISSING_BARS")
    zero_count = sum(1 for bar in accepted if finite_float(bar.get("volume")) == 0)
    if zero_count >= max(2, expected_bar_count // 3):
        reason_codes.append("SUSPICIOUS_ZERO_VOLUME_BARS")

    hard_failures = {
        "MISSING_BAR_TIMESTAMP",
        "DUPLICATE_BARS",
        "NON_MONOTONIC_TIMESTAMPS",
        "INCORRECT_SESSION_BARS",
        "FUTURE_OR_POST_CUTOFF_BARS",
        "INCOMPLETE_BARS",
        "INVALID_PRICES",
        "INVALID_OHLC_RELATIONSHIP",
        "INVALID_VOLUME",
        "MISSING_BARS",
        "SUSPICIOUS_ZERO_VOLUME_BARS",
    }
    unique = sorted(set(reason_codes))
    return {
        "passed": not any(code in hard_failures for code in unique),
        "reason_codes": unique,
        "raw_bar_count": len(raw),
        "valid_timestamp_count": valid_timestamp_count,
        "accepted_bar_count": len(accepted),
        "expected_bar_count": expected_bar_count,
        "accepted_bars": accepted,
        "evidence_cutoff": cutoff.isoformat(),
    }


def validate_snapshot_timestamp(
    snapshot: Mapping[str, Any],
    *,
    as_of: datetime,
    maximum_age_seconds: int,
) -> list[str]:
    value = snapshot.get("update_time", snapshot.get("timestamp", snapshot.get("as_of")))
    if not value:
        return ["MISSING_QUOTE_TIMESTAMP"]
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ["INVALID_QUOTE_TIMESTAMP"]
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=as_of.tzinfo)
    age = (as_of - stamp.astimezone(as_of.tzinfo)).total_seconds()
    if age < 0:
        return ["FUTURE_QUOTE_TIMESTAMP"]
    if age > maximum_age_seconds:
        return ["STALE_MARKET_DATA"]
    return []
