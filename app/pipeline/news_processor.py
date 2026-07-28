from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse


NO_CATALYST = "NO_CONFIRMED_CATALYST"


def classify_news_catalysts(
    news_items: Iterable[Mapping[str, Any]],
    *,
    allowed_symbols: Iterable[str],
    as_of: datetime,
    config: Mapping[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    """Classify point-in-time catalysts with deterministic keyword rules."""

    allowed = {str(symbol).strip().upper() for symbol in allowed_symbols}
    result: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in sorted(allowed)}
    seen: set[tuple[str, str, str]] = set()
    maximum_age_hours = float(config.get("maximum_age_hours", 48))
    credible_domains = {str(domain).lower().removeprefix("www.") for domain in config.get("credible_domains", [])}
    keyword_rules = config.get("catalyst_keywords", {})
    as_of_utc = _as_utc(as_of)

    ordered = sorted(news_items, key=lambda item: (_timestamp(item) or datetime.min.replace(tzinfo=timezone.utc), str(item.get("title", ""))))
    for item in ordered:
        if item.get("is_error"):
            continue
        published = _timestamp(item)
        if published is None:
            continue
        published_utc = _as_utc(published)
        age_hours = (as_of_utc - published_utc).total_seconds() / 3600.0
        if age_hours < 0 or age_hours > maximum_age_hours:
            continue

        title = str(item.get("title", "")).strip()
        summary = str(item.get("summary", "")).strip()
        url = str(item.get("url", "")).strip()
        source_domain = _source_domain(item, url)
        symbols = sorted({str(value).strip().upper() for value in item.get("related_symbols", [])} & allowed)
        if not symbols:
            continue
        catalyst_type = _classify_text(f"{title} {summary}", keyword_rules)
        source_is_credible = source_domain in credible_domains
        credibility = 1.0 if source_is_credible else 0.35
        recency = max(0.0, 1.0 - age_hours / maximum_age_hours)
        quality_score = round(100.0 * credibility * (0.55 + 0.45 * recency), 2)
        sentiment = str(item.get("sentiment", "neutral")).strip().lower()
        catalyst_direction = "BULLISH" if sentiment == "positive" else "BEARISH" if sentiment == "negative" else "NEUTRAL"
        for symbol in symbols:
            key = (symbol, _normalize(title), url.lower())
            if key in seen:
                continue
            seen.add(key)
            result[symbol].append({
                "symbol": symbol,
                "title": title,
                "url": url,
                "source_domain": source_domain,
                "published_at": published_utc.isoformat(),
                "age_hours": round(age_hours, 4),
                "catalyst_type": catalyst_type,
                "quality_score": quality_score,
                "sentiment": sentiment,
                "catalyst_direction": catalyst_direction,
                "confirmed": catalyst_type != NO_CATALYST and source_is_credible,
                "reason_codes": [
                    f"CATALYST_{catalyst_type}",
                    "CREDIBLE_SOURCE" if source_is_credible else "UNVERIFIED_SOURCE",
                    "RECENT_CATALYST" if age_hours <= 12 else "OLDER_CATALYST",
                ],
            })
    for values in result.values():
        values.sort(key=lambda item: (-float(item["quality_score"]), item["published_at"], item["title"]))
    return result


def best_catalyst(items: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not items:
        return {
            "catalyst_type": NO_CATALYST,
            "confirmed": False,
            "quality_score": 0.0,
            "reason_codes": ["NO_CONFIRMED_CATALYST"],
        }
    return dict(items[0])


def _classify_text(text: str, rules: Mapping[str, Any]) -> str:
    normalized = text.lower()
    for catalyst_type, keywords in rules.items():
        if any(str(keyword).lower() in normalized for keyword in keywords):
            return str(catalyst_type).upper()
    return NO_CATALYST


def _timestamp(item: Mapping[str, Any]) -> datetime | None:
    value = item.get("published_at", item.get("timestamp"))
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _source_domain(item: Mapping[str, Any], url: str) -> str:
    domain = urlparse(url).netloc.lower().removeprefix("www.")
    if domain:
        return domain
    return str(item.get("source_domain", item.get("source", ""))).lower().removeprefix("www.")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", value.lower())).strip()
