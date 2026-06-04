from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time, timedelta
from typing import Any

from .models import EarningsCalendarEvent
from .normalization import normalize_title


@dataclass
class MediaUpdate:
    symbol: str
    report_date: str
    title: str
    source: str | None
    url: str | None
    published_at: str | None
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_media_updates(
    provider: Any | None,
    event: EarningsCalendarEvent,
    *,
    window_days: int = 3,
) -> tuple[list[MediaUpdate], list[str]]:
    if provider is None:
        return [], [f"{event.symbol}: Alpha Vantage news unavailable because ALPHAVANTAGE_API_KEY is empty"]

    report_day = datetime.fromisoformat(event.report_date[:10]).date()
    start = datetime.combine(report_day - timedelta(days=window_days), time.min)
    end = datetime.combine(report_day + timedelta(days=window_days), time.max.replace(microsecond=0))
    try:
        updates = provider.fetch_news(
            symbol=event.symbol,
            report_date=event.report_date,
            time_from=start,
            time_to=end,
            topics="earnings",
        )
    except Exception as exc:
        return [], [f"{event.symbol}: Alpha Vantage news unavailable: {exc}"]

    if not updates:
        return [], [f"{event.symbol}: no Alpha Vantage earnings news found in the configured window"]
    return updates, []


def media_publish_payload(update: MediaUpdate) -> dict[str, Any]:
    return {
        "symbol": update.symbol,
        "report_date": update.report_date,
        "title": normalize_title(update.title),
        "source": update.source,
        "url": update.url,
    }
