from __future__ import annotations

from datetime import datetime

from .market_reaction_analyzer import MarketReactionAnalysis
from .media_update_analyzer import MediaUpdate, aggregate_news_sentiment, ensure_earnings_relevance_scores
from .models import EarningsCalendarEvent, PostEarningsAnalysis, PreEarningsPreview


def format_calendar_summary(events: list[EarningsCalendarEvent], *, title: str = "Earnings calendar") -> str:
    lines = [f"**{title}**"]
    if not events:
        lines.append("No earnings events matched the configured universe.")
        return "\n".join(lines)
    for event in events:
        lines.append(
            f"- `{event.symbol}` `{event.report_date}` | `{_clean_label(event.timing_bucket)}` | "
            f"notify `{_fmt_datetime(event.notification_time_pt)}`"
        )
    return "\n".join(lines)


def format_pre_earnings_preview(preview: PreEarningsPreview, media: list[MediaUpdate] | None = None) -> str:
    lines = [
        f"**Pre-earnings consensus: {preview.symbol}**",
        f"`{preview.report_date}` | `{_clean_label(preview.timing_bucket)}` | notify `{_fmt_datetime(preview.notification_time_pt)}`",
        f"Confidence: `{_clean_label(preview.timing_confidence)}`",
        "",
        "**Consensus**",
        f"EPS `{_fmt_num(preview.eps_estimate)}`",
        f"Revenue `{_fmt_money(preview.revenue_estimate)}`",
        f"Analysts `{preview.analyst_count if preview.analyst_count is not None else 'n/a'}`",
        "",
        "**Setup**",
        f"Beat rate `{_fmt_pct_ratio(preview.historical_beat_rate)}`",
        f"Prior EPS surprise `{_fmt_pct(preview.prior_quarter_eps_surprise_pct)}`",
        f"Price target `{_fmt_num(preview.price_target_low)}` / `{_fmt_num(preview.price_target_mean)}` / `{_fmt_num(preview.price_target_high)}`",
        f"Rating `{_clean_label(preview.rating_consensus or 'unavailable')}`",
        f"Risk `{_clean_label(preview.expectation_risk_level)}`",
        "",
        "**Trading Context**",
        "Continuation only if price holds VWAP and the opening range.",
        "Sell-the-news risk if the gap fades below VWAP.",
    ]
    if media:
        lines.extend(["", "**Media**"])
        for item in media[:3]:
            lines.append(f"- {_article_link(item)}")
    if preview.warnings:
        lines.extend(["", "**Warnings**"])
        lines.extend(f"- {w}" for w in preview.warnings[:10])
    return "\n".join(lines)


def format_earnings_reminder(event: EarningsCalendarEvent) -> str:
    return "\n".join([
        f"**Scheduled earnings reminder: {event.symbol}**",
        f"`{event.report_date}` | `{_clean_label(event.timing_bucket)}`",
        f"Notify PT: `{_fmt_datetime(event.notification_time_pt)}`",
        f"Confidence: `{_clean_label(event.timing_confidence)}`",
        "",
        "Reminder only. Do not treat an inferred notification time as the company's exact release time.",
    ])


def format_media_digest(symbol: str, report_date: str, updates: list[MediaUpdate]) -> str:
    ensure_earnings_relevance_scores(updates)
    sentiment = aggregate_news_sentiment(updates)
    lines = [
        f"**Earnings media digest: {symbol}**",
        f"`{report_date}` | `{len(updates)}` selected articles",
        "",
        "**Earnings News Sentiment**",
        f"Tone `{sentiment['label']}` | score `{_fmt_num(sentiment['score'])}`",
        f"Coverage `{sentiment['sentiment_item_count']}/{sentiment['article_count']}` articles with sentiment",
        _clip(sentiment["interpretation"], 180),
        "",
        "**Top Articles**",
    ]
    for index, update in enumerate(updates, start=1):
        lines.append(f"**{index}. {_clip(update.title, 100)}**")
        meta = " | ".join(x for x in [update.source or "", _fmt_datetime(update.published_at)] if x)
        if meta:
            lines.append(meta)
        lines.append(f"Sentiment `{update.ticker_sentiment_label or 'n/a'}` | score `{_fmt_num(update.ticker_sentiment_score)}`")
        lines.append(
            f"Relevance earnings `{_fmt_num(update.earnings_relevance_score)}` | "
            f"ticker `{_fmt_num(update.ticker_relevance_score or update.relevance_score)}`"
        )
        lines.append(f"Reason `{_clean_label(update.earnings_relevance_reason or 'earnings-related')}`")
        if update.description:
            lines.append(_clip(update.description, 170))
        if update.url:
            lines.append(_markdown_link("Open article", update.url))
        if index != len(updates):
            lines.append("")
    return "\n".join(lines)


def format_post_earnings_analysis(
    analysis: PostEarningsAnalysis,
    reaction: MarketReactionAnalysis | None = None,
) -> str:
    lines = [
        f"**Post-earnings analysis: {analysis.symbol}**",
        f"`{analysis.report_date}`",
        "",
        "**Actuals**",
        f"EPS `{_fmt_num(analysis.actual_eps)}` vs est `{_fmt_num(analysis.estimated_eps)}`",
        f"EPS surprise `{_fmt_pct(analysis.eps_surprise_pct)}`",
        f"Revenue `{_fmt_money(analysis.actual_revenue)}` vs est `{_fmt_money(analysis.estimated_revenue)}`",
        f"Revenue surprise `{_fmt_pct(analysis.revenue_surprise_pct)}`",
        f"Result `{_clean_label(analysis.result_classification)}`",
    ]
    if reaction:
        lines.extend([
            "",
            "**Market Reaction**",
            f"`{_clean_label(reaction.reaction_classification)}` | `{_fmt_pct(reaction.reaction_pct)}`",
            f"Next day: {_clip(reaction.next_day_conditional_bias, 150)}",
            f"Medium term: {_clip(reaction.medium_term_conditional_bias, 150)}",
            f"Confirm: {_join_short(reaction.confirmation_conditions)}",
            f"Fail: {_join_short(reaction.failure_conditions)}",
        ])
    if analysis.warnings or (reaction and reaction.warnings):
        lines.extend(["", "**Warnings**"])
        for warning in [*analysis.warnings, *(reaction.warnings if reaction else [])][:10]:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def format_market_reaction(analysis: MarketReactionAnalysis) -> str:
    return "\n".join([
        f"**Earnings market reaction: {analysis.symbol}**",
        f"`{analysis.report_date}` | `{_clean_label(analysis.reaction_session)}`",
        f"Reference `{_fmt_num(analysis.reference_price)}` -> reaction `{_fmt_num(analysis.reaction_price)}`",
        f"Move `{_fmt_pct(analysis.reaction_pct)}` | `{_clean_label(analysis.reaction_classification)}`",
        "",
        "**Bias**",
        f"Next day: {_clip(analysis.next_day_conditional_bias, 150)}",
        f"Medium term: {_clip(analysis.medium_term_conditional_bias, 150)}",
        "",
        "**Levels To Watch**",
        f"Confirm: {_join_short(analysis.confirmation_conditions)}",
        f"Fail: {_join_short(analysis.failure_conditions)}",
    ])


def _fmt_num(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"${value:,.0f}"


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def _fmt_pct_ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _fmt_datetime(value: str | None) -> str:
    if not value:
        return "n/a"
    raw = value.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.strftime("%b %d %H:%M")
    except ValueError:
        pass
    if len(raw) == 15 and raw[8] == "T":
        try:
            dt = datetime.strptime(raw, "%Y%m%dT%H%M%S")
            return dt.strftime("%b %d %H:%M")
        except ValueError:
            pass
    return raw[:16]


def _clip(value: str, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip() + "..."


def _clean_label(value: str | None) -> str:
    if not value:
        return "n/a"
    return str(value).replace("_", " ").replace("-", " ")


def _join_short(values: list[str], limit: int = 170) -> str:
    if not values:
        return "n/a"
    return _clip("; ".join(values), limit)


def _article_link(item: MediaUpdate) -> str:
    title = _clip(item.title, 90)
    if item.url:
        title = _markdown_link(title, item.url)
    if item.source:
        return f"{title} - {item.source}"
    return title


def _markdown_link(label: str, url: str) -> str:
    safe_url = url.replace("(", "%28").replace(")", "%29")
    safe_label = label.replace("[", "(").replace("]", ")")
    return f"[{safe_label}]({safe_url})"
