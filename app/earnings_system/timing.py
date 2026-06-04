from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from .normalization import safe_str


@dataclass(frozen=True)
class NormalizedTiming:
    timing_bucket: str
    exact_release_time_et: str | None
    notification_time_pt: str | None
    timing_confidence: str


def parse_hhmm(value: str) -> time:
    h, m = value.strip().split(":", 1)
    return time(int(h), int(m))


def normalize_timing(
    *,
    report_date: str,
    raw_time: object,
    timezone_market: str = "America/New_York",
    timezone_user: str = "America/Los_Angeles",
    bmo_notification_time_pt: str = "04:00",
    amc_notification_time_pt: str = "12:45",
) -> NormalizedTiming:
    raw = (safe_str(raw_time) or "").strip()
    bucket = _normalize_bucket(raw)
    if _looks_like_datetime(raw):
        parsed = _parse_datetime_et(raw, timezone_market)
        if parsed is not None:
            notification = parsed.astimezone(ZoneInfo(timezone_user)).isoformat()
            return NormalizedTiming(
                timing_bucket=bucket if bucket != "unknown" else _bucket_from_exact_time(parsed),
                exact_release_time_et=parsed.isoformat(),
                notification_time_pt=notification,
                timing_confidence="exact",
            )

    if bucket == "bmo":
        return NormalizedTiming(
            timing_bucket="bmo",
            exact_release_time_et=None,
            notification_time_pt=_notification_iso(report_date, bmo_notification_time_pt, timezone_user),
            timing_confidence="inferred_bucket",
        )
    if bucket == "amc":
        return NormalizedTiming(
            timing_bucket="amc",
            exact_release_time_et=None,
            notification_time_pt=_notification_iso(report_date, amc_notification_time_pt, timezone_user),
            timing_confidence="inferred_bucket",
        )
    if bucket == "dmh":
        return NormalizedTiming(
            timing_bucket="dmh",
            exact_release_time_et=None,
            notification_time_pt=None,
            timing_confidence="inferred_bucket",
        )
    return NormalizedTiming("unknown", None, None, "unknown")


def _normalize_bucket(raw: str) -> str:
    text = raw.strip().lower().replace(" ", "_")
    if text in {"bmo", "before_market_open", "before open", "before_open", "before-market-open"}:
        return "bmo"
    if text in {"amc", "after_market_close", "after close", "after_close", "after-market-close"}:
        return "amc"
    if text in {"dmh", "during_market_hours", "during_market", "during-market-hours"}:
        return "dmh"
    return "unknown"


def _looks_like_datetime(raw: str) -> bool:
    return any(sep in raw for sep in ["T", " "]) and any(ch.isdigit() for ch in raw)


def _parse_datetime_et(raw: str, timezone_market: str) -> datetime | None:
    text = raw.strip().replace("Z", "+00:00")
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.replace(tzinfo=ZoneInfo(timezone_market))
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ZoneInfo(timezone_market))
        return dt.astimezone(ZoneInfo(timezone_market))
    except Exception:
        return None


def _bucket_from_exact_time(dt: datetime) -> str:
    local = dt.timetz()
    if local < time(9, 30, tzinfo=local.tzinfo):
        return "bmo"
    if local >= time(16, 0, tzinfo=local.tzinfo):
        return "amc"
    return "dmh"


def _notification_iso(report_date: str, hhmm: str, timezone_user: str) -> str:
    t = parse_hhmm(hhmm)
    dt = datetime.fromisoformat(report_date[:10]).replace(
        hour=t.hour,
        minute=t.minute,
        second=0,
        microsecond=0,
        tzinfo=ZoneInfo(timezone_user),
    )
    return dt.isoformat()

