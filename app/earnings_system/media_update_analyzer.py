from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time, timedelta
import re
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
    earnings_relevance_reason: str | None = None
    low_earnings_relevance: bool = False

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

CORE_EARNINGS_TERMS = {
    "earnings",
    "results",
    "report",
    "preview",
    "q1",
    "q2",
    "q3",
    "q4",
    "eps",
    "revenue",
    "guidance",
    "outlook",
    "consensus",
    "estimate",
    "estimates",
    "analyst",
    "beat",
    "miss",
}

FINANCIAL_METRIC_TERMS = {
    "margin",
    "margins",
    "billings",
    "arr",
    "free cash flow",
    "fcf",
    "operating income",
}

REACTION_TERMS = {
    "stock reaction",
    "post-earnings",
    "post earnings",
    "after earnings",
    "shares jump",
    "shares rise",
    "shares surge",
    "shares fall",
    "shares slump",
    "gap",
}

WEAK_NEWS_PHRASES = {
    "insider selling",
    "insider sale",
    "director transaction",
    "director transactions",
    "board vote",
    "board votes",
    "shareholder proposal",
    "shareholder proposals",
    "annual meeting",
    "annual meeting results",
    "product announcement",
    "product launch",
    "partnership announcement",
    "partnership",
    "unrelated ai",
    "ai product",
    "artificial intelligence product",
    "sector list",
    "stocks to watch",
    "best stocks",
}

AGGREGATOR_SOURCES = {
    "msn",
    "tradingview",
    "yahoo",
    "yahoo finance",
    "marketbeat",
    "zacks",
}

ORIGINAL_SOURCES = {
    "reuters",
    "associated press",
    "ap",
    "bloomberg",
    "cnbc",
    "marketwatch",
    "wall street journal",
    "wsj",
    "barron's",
    "seeking alpha",
}

STRICT_EARNINGS_RELEVANCE_THRESHOLD = 0.55
LOW_TICKER_RELEVANCE_THRESHOLD = 0.25


def select_relevant_media_updates(updates: list[MediaUpdate], *, max_items: int) -> list[MediaUpdate]:
    ensure_earnings_relevance_scores(updates)
    deduped = dedupe_media_updates(updates)
    ticker_filtered = [
        update for update in deduped
        if (update.ticker_relevance_score if update.ticker_relevance_score is not None else update.relevance_score or 1.0)
        >= LOW_TICKER_RELEVANCE_THRESHOLD
    ]
    strict = [
        update for update in ticker_filtered
        if (update.earnings_relevance_score or 0.0) >= STRICT_EARNINGS_RELEVANCE_THRESHOLD
    ]
    if strict:
        return sorted(strict, key=_ranking_key)[:max(0, max_items)]

    fallback = [
        update for update in ticker_filtered
        if (update.earnings_relevance_score or 0.0) > 0.0
    ]
    for update in fallback:
        update.low_earnings_relevance = True
        update.earnings_relevance_reason = "low relevance fallback"
    return sorted(fallback, key=_ranking_key)[:max(0, min(max_items, 2))]


def score_media_update(update: MediaUpdate) -> int:
    return int(round(compute_earnings_relevance_score(update) * 100))


def ensure_earnings_relevance_scores(updates: list[MediaUpdate]) -> None:
    for update in updates:
        if update.earnings_relevance_score is None:
            score, reason = compute_earnings_relevance(update)
            update.earnings_relevance_score = score
            update.earnings_relevance_reason = reason


def compute_earnings_relevance_score(update: MediaUpdate) -> float:
    return compute_earnings_relevance(update)[0]


def compute_earnings_relevance(update: MediaUpdate) -> tuple[float, str]:
    text = _combined_text(update)
    title_text = normalize_title(update.title)
    score = 0.0
    reasons: list[str] = []

    if "earnings preview" in text or ("earnings" in text and "preview" in text):
        score += 0.34
        reasons.append("earnings preview")
    if "earnings results" in text or ("earnings" in text and "results" in text):
        score += 0.34
        reasons.append("earnings results")
    if any(term in text for term in ["eps", "revenue", "consensus", "estimate", "estimates", "analyst"]):
        score += 0.24
        reasons.append("analyst estimates")
    if any(term in text for term in ["guidance", "outlook"]):
        score += 0.18
        reasons.append("guidance/outlook")
    if any(term in text for term in ["beat", "miss"]):
        score += 0.18
        reasons.append("beat/miss")
    if any(term in text for term in FINANCIAL_METRIC_TERMS):
        score += 0.16
        reasons.append("earnings financial metrics")
    if any(term in text for term in REACTION_TERMS):
        score += 0.16
        reasons.append("post-earnings reaction")

    explicit_terms = sum(1 for term in CORE_EARNINGS_TERMS if term in text)
    title_terms = sum(1 for term in CORE_EARNINGS_TERMS if term in title_text)
    score += min(0.26, explicit_terms * 0.035)
    score += min(0.16, title_terms * 0.04)

    weak_hits = [phrase for phrase in WEAK_NEWS_PHRASES if phrase in text]
    if weak_hits:
        score -= 0.28 + min(0.18, 0.04 * len(weak_hits))

    if explicit_terms == 0 and not reasons:
        score -= 0.20
    score = min(0.86, score)

    timing_adjustment = _timing_adjustment(update, reasons)
    score += timing_adjustment
    score += _source_adjustment(update.source)

    if weak_hits and explicit_terms <= 1:
        score = min(score, 0.30)

    score = round(max(0.0, min(0.98, score)), 2)
    reason = _primary_reason(reasons, score)
    return score, reason


def dedupe_media_updates(updates: list[MediaUpdate]) -> list[MediaUpdate]:
    best_by_url: dict[str, MediaUpdate] = {}
    for update in updates:
        url = _normalize_url(update.url)
        if not url:
            url = f"id:{id(update)}"
        existing = best_by_url.get(url)
        if existing is None or _dedupe_preference(update) > _dedupe_preference(existing):
            best_by_url[url] = update

    best_by_title: dict[str, MediaUpdate] = {}
    for update in best_by_url.values():
        title_key = _title_fingerprint(update) or f"id:{id(update)}"
        existing = best_by_title.get(title_key)
        if existing is None or _dedupe_preference(update) > _dedupe_preference(existing):
            best_by_title[title_key] = update

    return list(best_by_title.values())


def _ranking_key(update: MediaUpdate) -> tuple[float, float, float]:
    published = _published_timestamp(update)
    ticker_relevance = update.ticker_relevance_score
    if ticker_relevance is None:
        ticker_relevance = update.relevance_score or 0.0
    return (-(update.earnings_relevance_score or 0.0), -ticker_relevance, -published)


def _timing_adjustment(update: MediaUpdate, reasons: list[str]) -> float:
    report_date = _parse_date(update.report_date)
    published_date = _parse_date(update.published_at)
    if report_date is None or published_date is None:
        return 0.0

    delta_days = (published_date - report_date).days
    is_preview = any(reason in reasons for reason in ["earnings preview", "analyst estimates", "guidance/outlook"])
    is_result = any(reason in reasons for reason in ["earnings results", "beat/miss", "post-earnings reaction"])

    if is_preview and -7 <= delta_days <= 0:
        return 0.12
    if is_result and 0 <= delta_days <= 3:
        return 0.12
    if abs(delta_days) <= 2 and reasons:
        return 0.05
    if delta_days < -14 or delta_days > 7:
        return -0.22
    if delta_days < -7 or delta_days > 3:
        return -0.10
    return 0.0


def _source_adjustment(source: str | None) -> float:
    text = normalize_title(source or "")
    if not text:
        return 0.0
    if any(name in text for name in ORIGINAL_SOURCES):
        return 0.03
    if any(name in text for name in AGGREGATOR_SOURCES):
        return -0.08
    return 0.0


def _primary_reason(reasons: list[str], score: float) -> str:
    if score < STRICT_EARNINGS_RELEVANCE_THRESHOLD:
        return reasons[0] if reasons else "low relevance fallback"
    preferred = [
        "earnings results",
        "earnings preview",
        "analyst estimates",
        "guidance/outlook",
        "beat/miss",
        "post-earnings reaction",
        "earnings financial metrics",
    ]
    for reason in preferred:
        if reason in reasons:
            return reason
    return "earnings-related"


def _dedupe_preference(update: MediaUpdate) -> tuple[float, float, float, float]:
    ticker_relevance = update.ticker_relevance_score
    if ticker_relevance is None:
        ticker_relevance = update.relevance_score or 0.0
    return (
        _source_quality(update.source),
        update.earnings_relevance_score or 0.0,
        ticker_relevance,
        _published_timestamp(update),
    )


def _source_quality(source: str | None) -> float:
    text = normalize_title(source or "")
    if any(name in text for name in ORIGINAL_SOURCES):
        return 1.0
    if any(name in text for name in AGGREGATOR_SOURCES):
        return -1.0
    return 0.0


def _normalize_url(url: str | None) -> str:
    if not url:
        return ""
    text = url.strip().lower()
    text = re.sub(r"^https?://", "", text)
    text = text.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    text = re.sub(r"^www\.", "", text)
    return text


def _title_fingerprint(update: MediaUpdate) -> str:
    text = normalize_title(update.title)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    tokens = [token for token in text.split() if token not in _title_stop_words(update.symbol)]
    if len(tokens) < 3:
        return ""
    return " ".join(sorted(set(tokens))[:12])


def _title_stop_words(symbol: str) -> set[str]:
    return {
        symbol.lower(),
        "stock",
        "stocks",
        "shares",
        "share",
        "inc",
        "corp",
        "corporation",
        "company",
        "the",
        "a",
        "an",
        "to",
        "of",
        "on",
        "for",
        "and",
        "with",
        "in",
        "after",
        "before",
    }


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except Exception:
        return None


def _published_timestamp(update: MediaUpdate) -> float:
    if not update.published_at:
        return 0.0
    try:
        return datetime.fromisoformat(update.published_at[:19]).timestamp()
    except Exception:
        return 0.0


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
