from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time
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
from app.scheduling import MarketScheduler, TradingCalendar
from app.universe import build_fixed_universe
from app.utils.console import configure_utf8_stdio


def main() -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Run the timezone-aware four-stage market scheduler.")
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--data-source", choices=["mock", "futu"], default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--poll-seconds", type=float, default=float(os.getenv("MARKET_SCHEDULER_POLL_SECONDS", "20")))
    parser.add_argument("--once", action="store_true", help="Run one scheduler tick and exit")
    parser.add_argument("--dry-run", action="store_true", help="Persist outputs but skip notifications")
    args = parser.parse_args()
    settings = load_settings(args.env_file, args.config)
    config = settings.market_config
    timezone = ZoneInfo(config["scheduler"]["timezone"])
    today = datetime.now(timezone).date().isoformat()
    universe = build_fixed_universe(today, config)
    source_kind = args.data_source or ("mock" if settings.demo_mode else "futu")
    kwargs = {}
    if source_kind == "futu":
        kwargs = {
            "host": settings.futu_host,
            "port": settings.futu_port,
            "market_prefix": settings.futu_market_prefix,
            "extended_time": settings.futu_extended_time,
            "news_client": NewsRSSClient(settings.news_rss_urls) if settings.news_rss_urls else None,
        }
    source = create_market_data_source(source_kind, universe, **kwargs)
    repository = RunRepository(args.output_dir or settings.output_dir, config["strategy_version"])
    calendar = TradingCalendar(config["scheduler"])

    def notify(stage: AnalysisStage, payload: object, markdown: str, persisted_path: str) -> None:
        if args.dry_run:
            return
        webhook = (
            settings.discord_premarket_webhook_url
            if stage in {AnalysisStage.UNIVERSE_VALIDATION, AnalysisStage.PREMARKET}
            else settings.discord_open_confirmation_webhook_url
        )
        if webhook:
            DiscordWebhookClient(webhook).send_message(markdown)

    service = MarketAnalysisService(
        config=config,
        data_source=source,
        repository=repository,
        calendar=calendar,
        notification_hook=notify,
    )
    scheduler = MarketScheduler(config, repository, dispatcher=service.scheduler_dispatch, calendar=calendar)
    poll_seconds = max(1.0, args.poll_seconds)
    try:
        while True:
            decisions = scheduler.tick(datetime.now(timezone))
            for decision in decisions:
                print(json.dumps(decision.to_dict(), ensure_ascii=False, sort_keys=True), flush=True)
            if args.once:
                return 0
            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())

