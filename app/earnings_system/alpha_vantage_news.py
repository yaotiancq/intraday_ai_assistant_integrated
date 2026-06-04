from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Any

import requests

from .media_update_analyzer import MediaUpdate, compute_earnings_relevance_score
from .normalization import normalize_title, safe_float, safe_str


@dataclass
class AlphaVantageNewsProvider:
    api_key: str
    timeout_seconds: int = 20
    retry_count: int = 1
    throttle_seconds: float = 0.2
    limit: int = 20
    base_url: str = "https://www.alphavantage.co/query"
    session: requests.Session = field(default_factory=requests.Session)

    def fetch_news(
        self,
        *,
        symbol: str,
        report_date: str,
        time_from: datetime,
        time_to: datetime,
        topics: str | None = "earnings",
    ) -> list[MediaUpdate]:
        if not self.api_key:
            raise RuntimeError("ALPHAVANTAGE_API_KEY is empty")

        params: dict[str, Any] = {
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "time_from": _format_alpha_time(time_from),
            "time_to": _format_alpha_time(time_to),
            "sort": "LATEST",
            "limit": self.limit,
            "apikey": self.api_key,
        }
        if topics:
            params["topics"] = topics

        payload = self._get(params)
        feed = payload.get("feed") if isinstance(payload, dict) else None
        rows = [x for x in feed if isinstance(x, dict)] if isinstance(feed, list) else []
        return normalize_alpha_vantage_news(rows, symbol=symbol, report_date=report_date)

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retry_count + 1):
            if self.throttle_seconds > 0:
                time.sleep(self.throttle_seconds)
            try:
                resp = self.session.get(self.base_url, params=params, timeout=self.timeout_seconds)
                if resp.status_code != 200:
                    raise RuntimeError(f"Alpha Vantage error {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                if isinstance(data, dict) and data.get("Note"):
                    raise RuntimeError(f"Alpha Vantage rate limit: {data['Note']}")
                if isinstance(data, dict) and data.get("Information"):
                    raise RuntimeError(f"Alpha Vantage information: {data['Information']}")
                if not isinstance(data, dict):
                    raise RuntimeError("Alpha Vantage returned non-object JSON")
                return data
            except Exception as exc:
                last_error = exc
                if attempt >= self.retry_count:
                    break
        raise RuntimeError(f"Alpha Vantage NEWS_SENTIMENT failed: {last_error}")


def normalize_alpha_vantage_news(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    report_date: str,
) -> list[MediaUpdate]:
    updates: list[MediaUpdate] = []
    seen: set[str] = set()
    for row in rows:
        title = safe_str(row.get("title")) or ""
        if not title:
            continue
        source = safe_str(row.get("source"))
        url = safe_str(row.get("url"))
        ticker_sentiment = _find_ticker_sentiment(row.get("ticker_sentiment"), symbol)
        key = f"{normalize_title(title)}|{source or ''}|{url or ''}"
        if key in seen:
            continue
        seen.add(key)
        update = MediaUpdate(
            symbol=symbol.upper(),
            report_date=report_date,
            title=title,
            source=source,
            url=url,
            published_at=_normalize_alpha_time(safe_str(row.get("time_published"))),
            summary=f"{symbol.upper()}: {title}",
            description=safe_str(row.get("summary")),
            overall_sentiment_score=safe_float(row.get("overall_sentiment_score")),
            overall_sentiment_label=safe_str(row.get("overall_sentiment_label")),
            ticker_sentiment_score=safe_float(ticker_sentiment.get("ticker_sentiment_score")),
            ticker_sentiment_label=safe_str(ticker_sentiment.get("ticker_sentiment_label")),
            relevance_score=safe_float(ticker_sentiment.get("relevance_score")),
            ticker_relevance_score=safe_float(ticker_sentiment.get("relevance_score")),
        )
        update.earnings_relevance_score = compute_earnings_relevance_score(update)
        updates.append(update)
    return updates


def _find_ticker_sentiment(value: Any, symbol: str) -> dict[str, Any]:
    target = symbol.upper()
    if not isinstance(value, list):
        return {}
    for item in value:
        if not isinstance(item, dict):
            continue
        ticker = safe_str(item.get("ticker"))
        if ticker and ticker.upper() == target:
            return item
    return {}


def _format_alpha_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M")


def _normalize_alpha_time(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
        return dt.isoformat()
    except Exception:
        return value
