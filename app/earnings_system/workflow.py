from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.delivery.discord import DiscordWebhookClient

from .alpha_vantage_news import AlphaVantageNewsProvider
from .candidate_filter import filter_candidates
from .config import EarningsConfig
from .earnings_calendar_scanner import scan_earnings_calendar
from .fmp_client import FMPClient
from .market_reaction_analyzer import (
    build_market_reaction_analysis,
    has_meaningful_reaction_change,
    reaction_publish_payload,
)
from .media_update_analyzer import fetch_media_updates, media_publish_payload
from .models import EarningsCalendarEvent, MarketReactionAnalysis, PostEarningsAnalysis, PreEarningsPreview
from .notification_formatter import (
    format_calendar_summary,
    format_earnings_reminder,
    format_market_reaction,
    format_media_update,
    format_post_earnings_analysis,
    format_pre_earnings_preview,
)
from .persistence import ensure_earnings_dirs, write_partitioned_json, write_partitioned_markdown
from .post_earnings_analyzer import build_post_earnings_analysis, post_publish_payload
from .pre_earnings_analyzer import (
    build_pre_earnings_preview,
    has_meaningful_consensus_change,
    preview_publish_payload,
)
from .publish_state import (
    build_publish_key,
    cleanup_expired_items,
    compute_content_hash,
    load_publish_state,
    make_publish_item,
    mark_published,
    previous_payload,
    save_publish_state,
    should_publish,
)


@dataclass
class EarningsWorkflowResult:
    command: str
    run_date: str
    calendar_events: list[EarningsCalendarEvent] = field(default_factory=list)
    candidates: list[EarningsCalendarEvent] = field(default_factory=list)
    previews: list[PreEarningsPreview] = field(default_factory=list)
    post_release: list[PostEarningsAnalysis] = field(default_factory=list)
    market_reactions: list[MarketReactionAnalysis] = field(default_factory=list)
    published_messages: list[str] = field(default_factory=list)
    skipped_messages: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "run_date": self.run_date,
            "calendar_events": [x.to_dict() for x in self.calendar_events],
            "candidates": [x.to_dict() for x in self.candidates],
            "previews": [x.to_dict() for x in self.previews],
            "post_release": [x.to_dict() for x in self.post_release],
            "market_reactions": [x.to_dict() for x in self.market_reactions],
            "published_count": len(self.published_messages),
            "skipped_messages": self.skipped_messages,
            "warnings": self.warnings,
        }


def make_fmp_client(config: EarningsConfig) -> FMPClient:
    return FMPClient(
        api_key=config.fmp_api_key,
        timeout_seconds=config.request_timeout_seconds,
        retry_count=config.request_retry_count,
        throttle_seconds=config.request_throttle_seconds,
    )


def make_news_provider(config: EarningsConfig) -> AlphaVantageNewsProvider | None:
    if not config.alphavantage_api_key:
        return None
    return AlphaVantageNewsProvider(
        api_key=config.alphavantage_api_key,
        timeout_seconds=config.request_timeout_seconds,
        retry_count=config.request_retry_count,
        throttle_seconds=config.request_throttle_seconds,
        limit=config.news_limit,
    )


def run_earnings_workflow(
    *,
    config: EarningsConfig,
    command: str,
    days: int | None = None,
    as_of: date | None = None,
    client: FMPClient | None = None,
    news_provider: AlphaVantageNewsProvider | None = None,
    send_discord: bool = True,
) -> EarningsWorkflowResult:
    ensure_earnings_dirs(config.output_dir)
    local_today = as_of or datetime.now(ZoneInfo(config.timezone_user)).date()
    days = days if days is not None else config.earnings_lookahead_days
    client = client or make_fmp_client(config)
    news_provider = news_provider or make_news_provider(config)
    result = EarningsWorkflowResult(command=command, run_date=local_today.isoformat())

    start_date = _start_date_for_command(command, local_today)
    scan_days = _days_for_command(command, days, start_date, local_today)
    state_path = config.output_dir / "publish_state.json"
    state = cleanup_expired_items(load_publish_state(state_path))

    try:
        events = scan_earnings_calendar(
            client,
            start_date=start_date,
            days=scan_days,
            timezone_market=config.timezone_market,
            timezone_user=config.timezone_user,
            bmo_notification_time_pt=config.bmo_notification_time_pt,
            amc_notification_time_pt=config.amc_notification_time_pt,
        )
    except Exception as exc:
        result.warnings.append(f"earnings calendar scan failed: {exc}")
        events = []

    result.calendar_events = events
    write_partitioned_json(config.output_dir, "calendar", result.run_date, [x.to_dict() for x in events])

    candidates = filter_candidates(
        events,
        universe_mode=config.universe_mode,
        watchlist_symbols=config.watchlist_symbols,
        max_candidates=config.max_deep_analysis_candidates,
    )
    candidates = _filter_by_command(candidates, command=command, today=local_today)
    result.candidates = candidates

    if command == "scan-earnings-calendar":
        if candidates:
            message = format_calendar_summary(candidates, title=f"Earnings watchlist: next {days} days")
            _publish_incremental(
                state=state,
                result=result,
                config=config,
                key=build_publish_key("CALENDAR", result.run_date, "earnings_calendar_scan", f"{days}d"),
                symbol="CALENDAR",
                report_date=result.run_date,
                content_type="earnings_calendar_scan",
                content_scope=f"{days}d",
                payload={"events": [x.symbol + "|" + x.report_date + "|" + x.timing_bucket for x in candidates]},
                summary=f"{len(candidates)} configured earnings events",
                message=message,
                send_discord=send_discord,
            )
        _finalize(config.output_dir, state_path, state, result)
        return result

    media_by_key: dict[tuple[str, str], list[Any]] = {}
    for event in candidates:
        media, media_warnings = fetch_media_updates(news_provider, event)
        result.warnings.extend(media_warnings)
        media_by_key[(event.symbol, event.report_date)] = media
        write_partitioned_json(
            config.output_dir,
            "media",
            f"{event.report_date}_{event.symbol}",
            [x.to_dict() for x in media],
        )
        for update in media:
            payload = media_publish_payload(update)
            title_hash = compute_content_hash(payload)[:12]
            _publish_incremental(
                state=state,
                result=result,
                config=config,
                key=build_publish_key(event.symbol, event.report_date, "earnings_media", title_hash),
                symbol=event.symbol,
                report_date=event.report_date,
                content_type="earnings_media",
                content_scope=title_hash,
                payload=payload,
                summary=update.summary,
                message=format_media_update(update),
                send_discord=send_discord,
            )

        if command in {"run-morning-earnings-report", "run-pre-close-amc-report", "run-daily-earnings-workflow"}:
            _run_pre_earnings(
                client=client,
                event=event,
                state=state,
                result=result,
                config=config,
                send_discord=send_discord,
                media=media_by_key.get((event.symbol, event.report_date), []),
            )

        if command in {"run-pre-close-amc-report", "run-daily-earnings-workflow"} and event.timing_bucket == "amc":
            _publish_incremental(
                state=state,
                result=result,
                config=config,
                key=build_publish_key(event.symbol, event.report_date, "earnings_reminder", command),
                symbol=event.symbol,
                report_date=event.report_date,
                content_type="earnings_reminder",
                content_scope=command,
                payload={
                    "symbol": event.symbol,
                    "report_date": event.report_date,
                    "timing_bucket": event.timing_bucket,
                    "notification_time_pt": event.notification_time_pt,
                    "timing_confidence": event.timing_confidence,
                },
                summary=f"{event.symbol} AMC earnings reminder",
                message=format_earnings_reminder(event),
                send_discord=send_discord,
            )

        if command in {"run-morning-earnings-report", "run-post-market-earnings-report", "run-daily-earnings-workflow"}:
            _run_post_earnings(
                client=client,
                event=event,
                state=state,
                result=result,
                config=config,
                send_discord=send_discord,
            )

    _finalize(config.output_dir, state_path, state, result)
    return result


def _run_pre_earnings(
    *,
    client: FMPClient,
    event: EarningsCalendarEvent,
    state: dict[str, Any],
    result: EarningsWorkflowResult,
    config: EarningsConfig,
    send_discord: bool,
    media: list[Any],
) -> None:
    try:
        preview = build_pre_earnings_preview(client, event)
        result.previews.append(preview)
        payload = preview_publish_payload(preview)
        key = build_publish_key(event.symbol, event.report_date, "pre_earnings_consensus", event.timing_bucket)
        prev = previous_payload(state, key)
        if not has_meaningful_consensus_change(prev, payload):
            result.skipped_messages += 1
            return
        _publish_incremental(
            state=state,
            result=result,
            config=config,
            key=key,
            symbol=event.symbol,
            report_date=event.report_date,
            content_type="pre_earnings_consensus",
            content_scope=event.timing_bucket,
            payload=payload,
            summary=preview.summary,
            message=format_pre_earnings_preview(preview, media=media),
            send_discord=send_discord,
        )
    except Exception as exc:
        result.warnings.append(f"{event.symbol}: pre-earnings analysis failed: {exc}")


def _run_post_earnings(
    *,
    client: FMPClient,
    event: EarningsCalendarEvent,
    state: dict[str, Any],
    result: EarningsWorkflowResult,
    config: EarningsConfig,
    send_discord: bool,
) -> None:
    try:
        post = build_post_earnings_analysis(client, event)
        result.post_release.append(post)
        reaction = build_market_reaction_analysis(
            client,
            event,
            threshold_pct=config.market_reaction_update_threshold_pct,
        )
        result.market_reactions.append(reaction)
        if post.result_classification != "unavailable":
            payload = post_publish_payload(post)
            _publish_incremental(
                state=state,
                result=result,
                config=config,
                key=build_publish_key(event.symbol, event.report_date, "post_earnings_actuals", "actual_vs_estimate"),
                symbol=event.symbol,
                report_date=event.report_date,
                content_type="post_earnings_actuals",
                content_scope="actual_vs_estimate",
                payload=payload,
                summary=post.summary,
                message=format_post_earnings_analysis(post, reaction=reaction),
                send_discord=send_discord,
            )

        reaction_payload = reaction_publish_payload(reaction)
        reaction_key = build_publish_key(event.symbol, event.report_date, "market_reaction", "price_snapshot")
        prev_reaction = previous_payload(state, reaction_key)
        if reaction.reaction_classification != "unavailable" and has_meaningful_reaction_change(
            prev_reaction,
            reaction_payload,
            config.market_reaction_update_threshold_pct,
        ):
            _publish_incremental(
                state=state,
                result=result,
                config=config,
                key=reaction_key,
                symbol=event.symbol,
                report_date=event.report_date,
                content_type="market_reaction",
                content_scope="price_snapshot",
                payload=reaction_payload,
                summary=reaction.summary,
                message=format_market_reaction(reaction),
                send_discord=send_discord,
            )
    except Exception as exc:
        result.warnings.append(f"{event.symbol}: post-earnings analysis failed: {exc}")


def _publish_incremental(
    *,
    state: dict[str, Any],
    result: EarningsWorkflowResult,
    config: EarningsConfig,
    key: str,
    symbol: str,
    report_date: str,
    content_type: str,
    content_scope: str,
    payload: dict[str, Any],
    summary: str,
    message: str,
    send_discord: bool,
) -> bool:
    content_hash = compute_content_hash(payload)
    if not should_publish(state, key, content_hash):
        result.skipped_messages += 1
        return False

    if send_discord and config.discord_webhook_url and not config.dry_run:
        DiscordWebhookClient(config.discord_webhook_url).send_message(message)
    result.published_messages.append(message)
    mark_published(
        state,
        make_publish_item(
            key=key,
            symbol=symbol,
            report_date=report_date,
            content_type=content_type,
            content_scope=content_scope,
            content_hash=content_hash,
            summary=summary,
            ttl_days=config.publish_state_ttl_days,
            payload=payload,
        ),
    )
    return True


def _finalize(output_dir: Path, state_path: Path, state: dict[str, Any], result: EarningsWorkflowResult) -> None:
    save_publish_state(state_path, state)
    date_key = result.run_date
    write_partitioned_json(output_dir, "logs", date_key, result.to_dict())
    write_partitioned_json(output_dir, "previews", date_key, [x.to_dict() for x in result.previews])
    write_partitioned_json(output_dir, "post_release", date_key, [x.to_dict() for x in result.post_release])
    write_partitioned_json(output_dir, "market_reaction", date_key, [x.to_dict() for x in result.market_reactions])
    write_partitioned_markdown(
        output_dir,
        "notifications",
        date_key,
        "\n\n---\n\n".join(result.published_messages) or "No incremental earnings updates.",
    )


def _start_date_for_command(command: str, today: date) -> date:
    if command in {"run-morning-earnings-report", "run-daily-earnings-workflow"}:
        return today - timedelta(days=1)
    return today


def _days_for_command(command: str, days: int, start_date: date, today: date) -> int:
    if command in {"run-pre-close-amc-report", "run-post-market-earnings-report"}:
        return 1
    if start_date < today:
        return days + (today - start_date).days
    return days


def _filter_by_command(events: list[EarningsCalendarEvent], *, command: str, today: date) -> list[EarningsCalendarEvent]:
    if command == "run-pre-close-amc-report":
        return [e for e in events if e.report_date == today.isoformat() and e.timing_bucket == "amc"]
    if command == "run-post-market-earnings-report":
        return [e for e in events if e.report_date == today.isoformat() and e.timing_bucket == "amc"]
    return events
