from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.data_sources import create_market_data_source
from app.data_sources.news_rss_client import NewsRSSClient
from app.delivery.discord import DiscordWebhookClient
from app.models.run_models import AnalysisStage
from app.persistence import RunRepository
from app.pipeline.scheduled_pipeline import MarketAnalysisService
from app.scheduling.market_scheduler import stage_context
from app.universe import build_fixed_universe
from app.utils.console import configure_utf8_stdio


STAGE_ORDER = [
    AnalysisStage.UNIVERSE_VALIDATION,
    AnalysisStage.PREMARKET,
    AnalysisStage.OPENING_5M,
    AnalysisStage.OPENING_15M,
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one deterministic fixed-universe market-analysis stage.")
    parser.add_argument("--stage", required=True, choices=["universe-validation", "premarket", "opening-5m", "opening-15m"])
    parser.add_argument("--date", default=None, help="Trading date (YYYY-MM-DD); defaults to current New York date")
    parser.add_argument("--data-source", choices=["mock", "futu", "replay"], default=None)
    parser.add_argument("--input-file", default=None, help="Replay fixture JSON")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--config", default=None, help="Strategy JSON path")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--actual-started-at", default=None, help="Optional timezone-aware operational start")
    parser.add_argument("--force", action="store_true", help="Explicitly rerun a completed logical stage")
    parser.add_argument("--run-prerequisites", action="store_true", help="Persist missing prior stages before the requested stage")
    parser.add_argument("--no-prerequisites", action="store_true", help="Disable replay's default prerequisite workflow")
    parser.add_argument("--allow-non-trading-day", action="store_true")
    parser.add_argument("--allow-non-trading-day-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--send-discord", action="store_true", help="Notify only after the stage is persisted")
    parser.add_argument("--dry-run", action="store_true", help="Do not send notifications")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    args = parse_args(argv)
    settings = load_settings(args.env_file, args.config)
    config = settings.market_config
    timezone = ZoneInfo(config["scheduler"]["timezone"])
    source_kind = args.data_source or ("mock" if settings.demo_mode else "futu")
    if source_kind == "replay" and not args.input_file:
        raise SystemExit("--input-file is required with --data-source replay")
    trade_date = _resolve_trade_date(args.date, source_kind, args.input_file, timezone)
    target = AnalysisStage.parse(args.stage)
    universe = build_fixed_universe(trade_date, config)
    source_kwargs: dict[str, Any] = {}
    if source_kind == "futu":
        source_kwargs = {
            "host": settings.futu_host,
            "port": settings.futu_port,
            "market_prefix": settings.futu_market_prefix,
            "extended_time": settings.futu_extended_time,
            "news_client": NewsRSSClient(settings.news_rss_urls) if settings.news_rss_urls else None,
        }
    source = create_market_data_source(
        source_kind,
        universe,
        replay_path=args.input_file,
        **source_kwargs,
    )
    output_dir = Path(args.output_dir) if args.output_dir else settings.output_dir
    repository = RunRepository(output_dir, config["strategy_version"])

    def notify(stage: AnalysisStage, payload: Any, markdown: str, persisted_path: str) -> None:
        if not args.send_discord or args.dry_run:
            return
        webhook = (
            settings.discord_premarket_webhook_url
            if stage in {AnalysisStage.UNIVERSE_VALIDATION, AnalysisStage.PREMARKET}
            else settings.discord_open_confirmation_webhook_url
        )
        DiscordWebhookClient(webhook).send_message(markdown)

    service = MarketAnalysisService(
        config=config,
        data_source=source,
        repository=repository,
        notification_hook=notify,
    )
    auto_prerequisites = (source_kind == "replay" and not args.no_prerequisites) or args.run_prerequisites
    stages = STAGE_ORDER[: STAGE_ORDER.index(target) + 1] if auto_prerequisites else [target]
    last: dict[str, Any] | None = None
    try:
        for stage in stages:
            if repository.is_complete(trade_date, stage, config["strategy_version"]) and not args.force:
                last = repository.load_stage(trade_date, stage)
                print(f"[{stage.value}] already complete; reused {repository.stage_path(trade_date, stage)}")
                continue
            operational_start = _operational_start(
                args.actual_started_at,
                config,
                stage,
                trade_date,
                timezone,
                deterministic=(source_kind == "replay"),
            )
            context = stage_context(config, stage, trade_date, operational_start)
            last = service.run_stage(
                stage,
                trade_date,
                context.scheduled_cutoff,
                operational_start,
                force=args.force,
                allow_non_trading_day=(
                    args.allow_non_trading_day
                    or args.allow_non_trading_day_test
                    or args.force_run
                    or settings.allow_non_trading_day_test
                    or settings.premarket_force_run
                ),
            )
            print(f"[{stage.value}] {last['status']} -> {repository.stage_path(trade_date, stage)}")
        if last is not None:
            markdown_path = repository.markdown_path(trade_date, target)
            if markdown_path and markdown_path.exists():
                print(markdown_path.read_text(encoding="utf-8"))
            else:
                print(json.dumps({
                    "stage": last.get("stage"),
                    "status": last.get("status"),
                    "reason_codes": last.get("reason_codes", []),
                }, ensure_ascii=False, sort_keys=True))
        return 0
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


def _operational_start(
    explicit: str | None,
    config: dict[str, Any],
    stage: AnalysisStage,
    trade_date: str,
    timezone: ZoneInfo,
    *,
    deterministic: bool,
) -> datetime:
    if explicit:
        value = datetime.fromisoformat(explicit.replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone) if value.tzinfo is None else value.astimezone(timezone)
    if deterministic:
        provisional = datetime.fromisoformat(f"{trade_date}T00:00:00").replace(tzinfo=timezone)
        return stage_context(config, stage, trade_date, provisional).scheduled_cutoff
    return datetime.now(timezone)


def _resolve_trade_date(
    requested: str | None,
    source_kind: str,
    replay_path: str | None,
    timezone: ZoneInfo,
) -> str:
    if source_kind != "replay":
        return requested or datetime.now(timezone).date().isoformat()
    assert replay_path is not None
    try:
        payload = json.loads(Path(replay_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"unable to read replay fixture metadata: {exc}") from exc
    declared = str(payload.get("trade_date", "")).strip() if isinstance(payload, dict) else ""
    if not declared:
        if requested:
            return requested
        raise SystemExit("replay fixture must declare trade_date when --date is omitted")
    if requested and requested != declared:
        raise SystemExit(f"--date {requested} does not match replay fixture trade_date {declared}")
    return declared


if __name__ == "__main__":
    raise SystemExit(main())
