from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from .fmp_client import FMPClient
from .models import EarningsCalendarEvent
from .normalization import normalize_report_date, normalize_symbol, pick_value, safe_float, safe_str
from .timing import normalize_timing


def scan_earnings_calendar(
    client: FMPClient,
    *,
    start_date: date,
    days: int,
    timezone_market: str = "America/New_York",
    timezone_user: str = "America/Los_Angeles",
    bmo_notification_time_pt: str = "04:00",
    amc_notification_time_pt: str = "12:45",
) -> list[EarningsCalendarEvent]:
    end_date = start_date + timedelta(days=max(days - 1, 0))
    data = client.get(
        "earnings-calendar",
        params={"from": start_date.isoformat(), "to": end_date.isoformat()},
    )
    if not isinstance(data, list):
        return []
    return [
        normalize_earnings_calendar_event(
            item,
            timezone_market=timezone_market,
            timezone_user=timezone_user,
            bmo_notification_time_pt=bmo_notification_time_pt,
            amc_notification_time_pt=amc_notification_time_pt,
        )
        for item in data
        if isinstance(item, dict)
    ]


def normalize_earnings_calendar_event(
    raw: dict[str, Any],
    *,
    timezone_market: str = "America/New_York",
    timezone_user: str = "America/Los_Angeles",
    bmo_notification_time_pt: str = "04:00",
    amc_notification_time_pt: str = "12:45",
) -> EarningsCalendarEvent:
    warnings: list[str] = []
    symbol = normalize_symbol(pick_value(raw, ["symbol", "ticker"]))
    if not symbol:
        warnings.append("missing symbol")
        symbol = "UNKNOWN"

    report_date = normalize_report_date(raw)
    if not report_date:
        warnings.append("missing report date")
        report_date = "unknown"

    raw_time = pick_value(raw, ["time", "timing", "releaseTime", "release_time", "dateTime", "datetime"])
    timing = normalize_timing(
        report_date=report_date if report_date != "unknown" else date.today().isoformat(),
        raw_time=raw_time,
        timezone_market=timezone_market,
        timezone_user=timezone_user,
        bmo_notification_time_pt=bmo_notification_time_pt,
        amc_notification_time_pt=amc_notification_time_pt,
    )

    fiscal_date = safe_str(pick_value(raw, ["fiscalDateEnding", "fiscal_date_ending", "periodEnding"]))
    if fiscal_date:
        fiscal_date = fiscal_date[:10]

    return EarningsCalendarEvent(
        symbol=symbol,
        report_date=report_date,
        fiscal_date_ending=fiscal_date,
        timing_bucket=timing.timing_bucket,
        exact_release_time_et=timing.exact_release_time_et,
        notification_time_pt=timing.notification_time_pt,
        timing_confidence=timing.timing_confidence,
        eps_estimate=safe_float(pick_value(raw, ["epsEstimated", "estimatedEps", "eps_estimate", "epsEstimate"])),
        eps_actual=safe_float(pick_value(raw, ["epsActual", "actualEps", "eps_actual"])),
        revenue_estimate=safe_float(pick_value(raw, ["revenueEstimated", "estimatedRevenue", "revenue_estimate"])),
        revenue_actual=safe_float(pick_value(raw, ["revenueActual", "actualRevenue", "revenue_actual"])),
        raw=dict(raw),
        warnings=warnings,
    )

