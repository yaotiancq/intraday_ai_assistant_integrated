from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.console import configure_utf8_stdio
from app.config import load_settings
from app.data_sources.futu_client import FutuQuoteClient, MockQuoteClient
from app.data_sources.news_rss_client import NewsRSSClient
from app.indicators.technical_indicators import build_technical_levels
from app.scoring.market_regime import compute_market_regime
from app.pipeline.candidate_builder import build_candidates
from app.pipeline.evidence_builder import build_premarket_evidence_pack
from app.llm.openai_client import OpenAIReportClient
from app.validators.evidence_validator import validate_evidence_pack
from app.validators.output_validator import validate_report
from app.delivery.discord import DiscordWebhookClient
from app.utils.file_io import write_json, write_text
from app.utils.time_utils import trading_context
from app.integration.trading_calendar import should_run_trading_day_task
from app.integration.monitor_bridge import publish_recommendations_to_monitor


def build_news_rss_urls(base_urls: list[str], query_symbols: list[str]) -> list[str]:
    """Build RSS URLs without hard-coding one ticker such as NVDA.

    The original configuration can still provide general feeds. For symbol-specific
    Yahoo Finance RSS, generate one feed per configured query symbol.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        url = (url or '').strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)

    for url in base_urls:
        add(url)

    for symbol in query_symbols:
        sym = symbol.strip().upper()
        if not sym:
            continue
        add(f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US')

    return urls


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description='Generate premarket intraday AI assistant report.')
    parser.add_argument('--env-file', default=None, help='Path to .env file')
    parser.add_argument('--dry-run', action='store_true', help='Do not send Discord message')
    parser.add_argument('--send-discord', action='store_true', help='Send report to Discord webhook')
    parser.add_argument('--send-to-monitor', action='store_true', help='Publish A/B-tier recommendations to realtime monitor watchlist')
    parser.add_argument('--skip-monitor-update', action='store_true', help='Do not update realtime monitor even if env enables it')
    parser.add_argument('--force-run', action='store_true', help='Run even when today is not a U.S. trading day')
    parser.add_argument('--allow-non-trading-day-test', action='store_true', help='Run for manual testing on non-trading days')
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    ctx = trading_context(settings.timezone)
    warnings: List[str] = []

    trading_decision = should_run_trading_day_task(
        tz_name=settings.timezone,
        force_run=args.force_run or settings.premarket_force_run,
        allow_non_trading_day_test=args.allow_non_trading_day_test or settings.allow_non_trading_day_test,
    )
    ctx['is_trading_day'] = trading_decision.is_trading_day
    ctx['trading_day_gate_reason'] = trading_decision.reason
    if not trading_decision.should_run:
        message = (
            f"跳过盘前助手：{trading_decision.date} 不是美股交易日，"
            f"原因={trading_decision.reason}。如需夜间/非交易日测试，"
            f"使用 --force-run 或设置 ALLOW_NON_TRADING_DAY_TEST=true。"
        )
        print(message)
        write_text(settings.data_dir / 'premarket_skip.txt', message)
        return
    if trading_decision.reason != 'scheduled_us_trading_day':
        warnings.append(f"交易日判断被测试/强制开关覆盖：{trading_decision.reason}")

    all_watch_symbols = sorted(set(settings.core_symbols + settings.index_symbols + settings.sector_etfs))
    quote_client = _make_quote_client(settings, warnings)

    with quote_client as qc:
        index_snapshots = _safe_snapshots(qc, settings.index_symbols, warnings)
        sector_snapshots = _safe_snapshots(qc, settings.sector_etfs, warnings)
        core_snapshots = _safe_snapshots(qc, settings.core_symbols, warnings)

        market_regime = compute_market_regime(index_snapshots, sector_snapshots)

        news_query_symbols = settings.news_query_symbols or settings.core_symbols
        news_urls = build_news_rss_urls(settings.news_rss_urls, news_query_symbols)
        news_client = NewsRSSClient(news_urls)
        news_items = news_client.fetch(allowed_symbols=all_watch_symbols)
        write_json(settings.data_dir / 'news_snapshot.json', news_items)

        candidate_obj = build_candidates(settings.core_symbols, news_items, core_snapshots + index_snapshots + sector_snapshots)
        candidate_symbols = candidate_obj['symbols']
        write_json(settings.data_dir / 'candidate_symbols.json', candidate_obj)

        candidate_snapshots = _safe_snapshots(qc, candidate_symbols, warnings)
        technicals: Dict[str, Dict] = {}
        end = datetime.now().date().isoformat()
        start = (datetime.now().date() - timedelta(days=120)).isoformat()
        snapshot_by_symbol = {s.get('symbol'): s for s in candidate_snapshots}
        for symbol in candidate_symbols:
            snap = snapshot_by_symbol.get(symbol, {})
            try:
                daily = qc.request_history_kline(symbol, start=start, end=end, ktype='K_DAY')
            except Exception as exc:
                warnings.append(f'{symbol}: history kline failed: {exc}')
                daily = None
            try:
                intraday = qc.get_realtime_kline(symbol, ktype='K_1M', num=240)
            except Exception as exc:
                warnings.append(f'{symbol}: intraday kline failed: {exc}')
                intraday = None
            technicals[symbol] = build_technical_levels(
                symbol=symbol,
                daily_df=daily,
                intraday_df=intraday,
                current_price=snap.get('last'),
                current_volume=snap.get('volume'),
            )

    write_json(settings.data_dir / 'market_regime.json', market_regime)
    write_json(settings.data_dir / 'technical_levels.json', technicals)

    pack = build_premarket_evidence_pack(
        context=ctx,
        market_regime=market_regime,
        index_snapshots=index_snapshots,
        sector_snapshots=sector_snapshots,
        candidate_symbols=candidate_symbols,
        snapshots=candidate_snapshots,
        technicals=technicals,
        news_items=news_items,
        max_a_tier=settings.max_a_tier,
        max_b_tier=settings.max_b_tier,
    )
    evidence_errors = validate_evidence_pack(pack)
    warnings.extend(evidence_errors)
    write_json(settings.data_dir / 'evidence_pack_premarket.json', pack)

    llm = OpenAIReportClient(settings.openai_api_key, settings.openai_model)
    report = llm.generate_premarket_report(pack)
    report_errors = validate_report(report)
    warnings.extend(report_errors)

    if warnings:
        report += '\n\n---\n系统警告：\n' + '\n'.join(f'- {w}' for w in warnings[:50])
        write_json(settings.data_dir / 'warnings.json', warnings)

    write_text(settings.data_dir / 'premarket_report.md', report)
    print(report)

    should_send = args.send_discord and not args.dry_run
    if should_send:
        DiscordWebhookClient(settings.discord_premarket_webhook_url).send_message(report)

    should_update_monitor = (
        args.send_to_monitor
        and not args.skip_monitor_update
        and settings.monitor_update_enabled
    )
    if should_update_monitor:
        try:
            monitor_result = publish_recommendations_to_monitor(
                evidence_pack=pack,
                admin_url=settings.monitor_admin_url,
                admin_token=settings.monitor_admin_token,
                update_mode=settings.monitor_update_mode,
                tiers=settings.monitor_update_tiers,
                max_symbols=settings.monitor_update_max_symbols,
                market_prefix=settings.futu_market_prefix,
            )
            write_json(settings.data_dir / 'monitor_update_result.json', monitor_result)
            print('\n[monitor_update] ' + str(monitor_result))
        except Exception as exc:
            warning = f'Monitor watchlist update failed: {exc}'
            warnings.append(warning)
            write_json(settings.data_dir / 'monitor_update_error.json', {'error': str(exc)})
            print('\n[monitor_update_error] ' + warning)


def _make_quote_client(settings, warnings: List[str]):
    if settings.demo_mode:
        warnings.append('DEMO_MODE=true，当前使用模拟行情；接入实盘前请改为 false 并启动 Futu/Moomoo OpenD。')
        return MockQuoteClient()
    try:
        return FutuQuoteClient(
            host=settings.futu_host,
            port=settings.futu_port,
            market_prefix=settings.futu_market_prefix,
            extended_time=settings.futu_extended_time,
        )
    except Exception as exc:
        warnings.append(f'FutuQuoteClient initialization failed, fallback to mock: {exc}')
        return MockQuoteClient()


def _safe_snapshots(qc, symbols: List[str], warnings: List[str]) -> List[Dict]:
    if not symbols:
        return []
    try:
        return qc.get_market_snapshot(symbols)
    except Exception as exc:
        warnings.append(f'get_market_snapshot failed for {symbols[:5]}...: {exc}')
        return MockQuoteClient().get_market_snapshot(symbols)


if __name__ == '__main__':
    main()
