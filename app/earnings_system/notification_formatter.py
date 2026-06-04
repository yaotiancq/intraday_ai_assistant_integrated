from __future__ import annotations

from .market_reaction_analyzer import MarketReactionAnalysis
from .media_update_analyzer import MediaUpdate
from .models import EarningsCalendarEvent, PostEarningsAnalysis, PreEarningsPreview


def format_calendar_summary(events: list[EarningsCalendarEvent], *, title: str = "Earnings calendar") -> str:
    lines = [title]
    if not events:
        lines.append("No earnings events matched the configured universe.")
        return "\n".join(lines)
    for event in events:
        lines.append(
            f"- `{event.symbol}` {event.report_date} timing `{event.timing_bucket}` "
            f"notification `{event.notification_time_pt or 'n/a'}`"
        )
    return "\n".join(lines)


def format_pre_earnings_preview(preview: PreEarningsPreview, media: list[MediaUpdate] | None = None) -> str:
    lines = [
        f"**Pre-earnings consensus: {preview.symbol}**",
        f"Report date: `{preview.report_date}`",
        f"Timing: `{preview.timing_bucket}` | notification PT: `{preview.notification_time_pt or 'n/a'}` | confidence: `{preview.timing_confidence}`",
        f"EPS estimate: `{_fmt_num(preview.eps_estimate)}`",
        f"Revenue estimate: `{_fmt_money(preview.revenue_estimate)}`",
        f"Analyst count: `{preview.analyst_count if preview.analyst_count is not None else 'n/a'}`",
        f"Historical beat rate: `{_fmt_pct_ratio(preview.historical_beat_rate)}`",
        f"Prior quarter EPS surprise: `{_fmt_pct(preview.prior_quarter_eps_surprise_pct)}`",
        (
            "Price target: "
            f"low `{_fmt_num(preview.price_target_low)}` / "
            f"mean `{_fmt_num(preview.price_target_mean)}` / "
            f"high `{_fmt_num(preview.price_target_high)}`"
        ),
        f"Rating consensus: `{preview.rating_consensus or 'unavailable'}`",
        f"Expectation risk: `{preview.expectation_risk_level}`",
        "Trading context: bullish continuation watch only if price holds VWAP and the opening range; "
        "sell-the-news risk if the gap fades below VWAP.",
    ]
    if media:
        lines.append("Media/news:")
        for item in media[:3]:
            source = f" ({item.source})" if item.source else ""
            url = f" - {item.url}" if item.url else ""
            lines.append(f"- {item.title}{source}{url}")
    if preview.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {w}" for w in preview.warnings[:10])
    return "\n".join(lines)


def format_earnings_reminder(event: EarningsCalendarEvent) -> str:
    return "\n".join([
        f"**Scheduled earnings reminder: {event.symbol}**",
        f"Report date: `{event.report_date}`",
        f"Timing bucket: `{event.timing_bucket}`",
        f"Notification PT: `{event.notification_time_pt or 'n/a'}`",
        f"Timing confidence: `{event.timing_confidence}`",
        "Reminder only. Do not treat an inferred notification time as the company's exact release time.",
    ])


def format_media_update(update: MediaUpdate) -> str:
    return "\n".join([
        f"**Earnings media update: {update.symbol}**",
        f"Report date: `{update.report_date}`",
        f"Title: {update.title}",
        f"Source: `{update.source or 'unknown'}`",
        f"Published: `{update.published_at or 'unknown'}`",
        f"URL: {update.url or 'n/a'}",
    ])


def format_post_earnings_analysis(
    analysis: PostEarningsAnalysis,
    reaction: MarketReactionAnalysis | None = None,
) -> str:
    lines = [
        f"**Post-earnings analysis: {analysis.symbol}**",
        f"Report date: `{analysis.report_date}`",
        f"EPS actual vs estimate: `{_fmt_num(analysis.actual_eps)}` vs `{_fmt_num(analysis.estimated_eps)}`",
        f"EPS surprise: `{_fmt_pct(analysis.eps_surprise_pct)}`",
        f"Revenue actual vs estimate: `{_fmt_money(analysis.actual_revenue)}` vs `{_fmt_money(analysis.estimated_revenue)}`",
        f"Revenue surprise: `{_fmt_pct(analysis.revenue_surprise_pct)}`",
        f"Result classification: `{analysis.result_classification}`",
    ]
    if reaction:
        lines.extend([
            f"Market reaction: `{reaction.reaction_classification}` / `{_fmt_pct(reaction.reaction_pct)}`",
            f"Next-day conditional bias: {reaction.next_day_conditional_bias}",
            f"Medium-term conditional bias: {reaction.medium_term_conditional_bias}",
            "Confirmation conditions: " + "; ".join(reaction.confirmation_conditions),
            "Failure conditions: " + "; ".join(reaction.failure_conditions),
        ])
    if analysis.warnings or (reaction and reaction.warnings):
        lines.append("Warnings:")
        for warning in [*analysis.warnings, *(reaction.warnings if reaction else [])][:10]:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def format_market_reaction(analysis: MarketReactionAnalysis) -> str:
    return "\n".join([
        f"**Earnings market reaction: {analysis.symbol}**",
        f"Report date: `{analysis.report_date}`",
        f"Reference price: `{_fmt_num(analysis.reference_price)}`",
        f"Reaction price: `{_fmt_num(analysis.reaction_price)}`",
        f"Reaction: `{_fmt_pct(analysis.reaction_pct)}` / `{analysis.reaction_classification}`",
        f"Next-day conditional bias: {analysis.next_day_conditional_bias}",
        f"Medium-term conditional bias: {analysis.medium_term_conditional_bias}",
        "Confirmation conditions: " + "; ".join(analysis.confirmation_conditions),
        "Failure conditions: " + "; ".join(analysis.failure_conditions),
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

