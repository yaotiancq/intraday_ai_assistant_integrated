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
    description: str | None = None
    overall_sentiment_score: float | None = None
    overall_sentiment_label: str | None = None
    ticker_sentiment_score: float | None = None
    ticker_sentiment_label: str | None = None
    relevance_score: float | None = None
    ticker_relevance_score: float | None = None
    earnings_relevance_score: float | None = None

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
    if not any(has_sentiment_fields(update) for update in updates):
        return updates, [f"{event.symbol}: Alpha Vantage news returned no sentiment fields"]
    return updates, []


EARNINGS_KEYWORDS = {
    "earnings",
    "results",
    "report",
    "preview",
    "guidance",
    "revenue",
    "eps",
    "analyst",
    "consensus",
    "estimate",
    "estimates",
    "q1",
    "q2",
    "q3",
    "q4",
}

WEAK_NEWS_PHRASES = {
    "insider selling",
    "insider sale",
    "board vote",
    "board votes",
    "shareholder proposal",
    "shareholder proposals",
    "product announcement",
    "product launch",
    "unrelated ai",
    "ai product",
}


def select_relevant_media_updates(updates: list[MediaUpdate], *, max_items: int) -> list[MediaUpdate]:
    ensure_earnings_relevance_scores(updates)
    scored = [(score_media_update(update), index, update) for index, update in enumerate(updates)]
    positive = [(score, index, update) for score, index, update in scored if score > 0]
    pool = positive
    if not pool:
        # Allow a weak fallback only when the article still contains an earnings keyword.
        pool = [
            (score, index, update)
            for score, index, update in scored
            if score >= -1 and _has_earnings_keyword(update)
        ]
    pool.sort(key=lambda item: (-item[0], -(item[2].earnings_relevance_score or 0.0), -(item[2].relevance_score or 0.0), item[1]))
    return [update for _, _, update in pool[:max(0, max_items)]]


def score_media_update(update: MediaUpdate) -> int:
    text = _combined_text(update)
    score = 0
    for keyword in EARNINGS_KEYWORDS:
        if keyword in text:
            score += 3 if keyword in {"earnings", "results", "guidance", "revenue", "eps"} else 2
    for phrase in WEAK_NEWS_PHRASES:
        if phrase in text:
            score -= 4
    if "stock" in text and score == 0:
        score -= 1
    return score


def ensure_earnings_relevance_scores(updates: list[MediaUpdate]) -> None:
    for update in updates:
        if update.earnings_relevance_score is None:
            update.earnings_relevance_score = compute_earnings_relevance_score(update)


def compute_earnings_relevance_score(update: MediaUpdate) -> float:
    score = score_media_update(update)
    if score <= 0:
        return 0.0
    # Convert the keyword score into a 0-0.95 earnings relevance value. This is
    # intentionally separate from Alpha Vantage ticker relevance, which can be 1.0
    # for articles that mention the symbol but are not tightly earnings-related.
    return round(min(0.95, score / 16.0), 2)


def has_sentiment_fields(update: MediaUpdate) -> bool:
    return any([
        update.overall_sentiment_score is not None,
        update.overall_sentiment_label is not None,
        update.ticker_sentiment_score is not None,
        update.ticker_sentiment_label is not None,
        update.relevance_score is not None,
        update.ticker_relevance_score is not None,
    ])


def aggregate_news_sentiment(updates: list[MediaUpdate]) -> dict[str, Any]:
    weighted_total = 0.0
    weight_total = 0.0
    article_scores: list[float] = []
    missing_count = 0

    for update in updates:
        score = update.ticker_sentiment_score
        if score is None:
            score = update.overall_sentiment_score
        if score is None:
            missing_count += 1
            continue
        weight = update.earnings_relevance_score
        if weight is None:
            weight = compute_earnings_relevance_score(update)
        if weight <= 0:
            weight = update.relevance_score if update.relevance_score is not None and update.relevance_score > 0 else 1.0
        weighted_total += score * weight
        weight_total += weight
        article_scores.append(score)

    aggregate_score = weighted_total / weight_total if weight_total > 0 else None
    label = label_aggregate_sentiment(aggregate_score)
    has_positive = any(score >= 0.05 for score in article_scores)
    has_negative = any(score <= -0.05 for score in article_scores)
    return {
        "score": aggregate_score,
        "label": label,
        "article_count": len(updates),
        "sentiment_item_count": len(article_scores),
        "missing_sentiment_count": missing_count,
        "mixed": has_positive and has_negative,
        "interpretation": build_sentiment_interpretation(label, len(updates), has_positive and has_negative, aggregate_score),
    }


def label_aggregate_sentiment(score: float | None) -> str:
    if score is None:
        return "Unavailable"
    if score >= 0.25:
        return "Bullish"
    if score >= 0.05:
        return "Slightly Bullish"
    if score > -0.05:
        return "Neutral"
    if score > -0.25:
        return "Slightly Bearish"
    return "Bearish"


def build_sentiment_interpretation(label: str, article_count: int, mixed: bool, score: float | None) -> str:
    if score is None:
        return (
            f"Media tone is unavailable because Alpha Vantage did not return sentiment scores for the "
            f"{article_count} selected earnings-related articles."
        )
    mixed_text = "sentiment is mixed" if mixed else "sentiment is not a standalone signal"
    return (
        f"Media tone is {label.lower()} based on {article_count} earnings-related articles, "
        f"but {mixed_text} and should not be treated as a trading signal by itself."
    )


def media_publish_payload(update: MediaUpdate) -> dict[str, Any]:
    return {
        "symbol": update.symbol,
        "report_date": update.report_date,
        "title": normalize_title(update.title),
        "source": update.source,
        "url": update.url,
        "overall_sentiment_score": update.overall_sentiment_score,
        "overall_sentiment_label": update.overall_sentiment_label,
        "ticker_sentiment_score": update.ticker_sentiment_score,
        "ticker_sentiment_label": update.ticker_sentiment_label,
        "relevance_score": update.relevance_score,
        "ticker_relevance_score": update.ticker_relevance_score,
        "earnings_relevance_score": update.earnings_relevance_score,
    }


def media_digest_publish_payload(symbol: str, report_date: str, updates: list[MediaUpdate]) -> dict[str, Any]:
    ensure_earnings_relevance_scores(updates)
    return {
        "symbol": symbol,
        "report_date": report_date,
        "items": [media_publish_payload(update) for update in updates],
    }


def _has_earnings_keyword(update: MediaUpdate) -> bool:
    text = _combined_text(update)
    return any(keyword in text for keyword in EARNINGS_KEYWORDS)


def _combined_text(update: MediaUpdate) -> str:
    return " ".join([
        normalize_title(update.title),
        normalize_title(update.description or ""),
        normalize_title(update.summary or ""),
    ])
