from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys
from typing import Any, Dict, List, Tuple

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
from app.reporting.report_router import PremarketReportRouter
from app.validators.evidence_validator import validate_evidence_pack
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
    parser.add_argument('--disable-llm', action='store_true', help='Generate the deterministic rule-based report without OpenAI')
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    ctx = trading_context(settings.timezone)
    warnings: List[str] = []
    market_data_errors: List[str] = []

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
    news_query_symbols = settings.news_query_symbols or settings.core_symbols
    news_urls = build_news_rss_urls(settings.news_rss_urls, news_query_symbols)
    news_items = _fetch_news_items(news_urls, all_watch_symbols, warnings)
    write_json(settings.data_dir / 'news_snapshot.json', news_items)

    quote_client = _make_quote_client(settings, warnings, market_data_errors)
    index_snapshots: List[Dict[str, Any]] = []
    sector_snapshots: List[Dict[str, Any]] = []
    core_snapshots: List[Dict[str, Any]] = []
    candidate_snapshots: List[Dict[str, Any]] = []
    technicals: Dict[str, Dict[str, Any]] = {}

    if quote_client is not None:
        with quote_client as qc:
            index_snapshots = _safe_snapshots(qc, settings.index_symbols, warnings, market_data_errors, allow_mock=settings.demo_mode)
            sector_snapshots = _safe_snapshots(qc, settings.sector_etfs, warnings, market_data_errors, allow_mock=settings.demo_mode)
            core_snapshots = _safe_snapshots(qc, settings.core_symbols, warnings, market_data_errors, allow_mock=settings.demo_mode)

            market_regime = compute_market_regime(index_snapshots, sector_snapshots)

            candidate_obj = build_candidates(settings.core_symbols, news_items, core_snapshots + index_snapshots + sector_snapshots)
            candidate_symbols = candidate_obj['symbols']
            write_json(settings.data_dir / 'candidate_symbols.json', candidate_obj)

            candidate_snapshots = _safe_snapshots(qc, candidate_symbols, warnings, market_data_errors, allow_mock=settings.demo_mode)
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
    else:
        market_regime = compute_market_regime([], [])
        candidate_obj = build_candidates(settings.core_symbols, news_items, [])
        candidate_symbols = candidate_obj['symbols']
        write_json(settings.data_dir / 'candidate_symbols.json', candidate_obj)
        technicals = {symbol: _empty_technical(symbol) for symbol in candidate_symbols}

    ctx['market_data_source'] = 'mock' if settings.demo_mode else 'futu'
    ctx['market_data_status'] = _market_data_status(
        settings_demo_mode=settings.demo_mode,
        market_data_errors=market_data_errors,
        snapshots=index_snapshots + sector_snapshots + candidate_snapshots,
    )
    ctx['market_data_errors'] = market_data_errors

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

    report_router = PremarketReportRouter(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        llm_enabled=settings.premarket_llm_enabled and not args.disable_llm,
        rule_fallback_enabled=settings.premarket_rule_fallback_enabled,
        max_retries=settings.openai_max_retries,
    )
    report_result = report_router.generate(
        evidence_pack=pack,
        evidence_errors=evidence_errors,
    )
    warnings.extend(report_result.warnings)

    report = report_result.report
    if warnings:
        report += '\n\n---\n系统警告：\n' + '\n'.join(f'- {w}' for w in warnings[:50])

    status = report_result.to_status_dict()
    status.update({
        'market_data_status': ctx['market_data_status'],
        'market_data_source': ctx['market_data_source'],
        'evidence_errors': evidence_errors,
        'warnings_count': len(warnings),
    })
    write_text(settings.data_dir / 'premarket_report.md', report)
    write_json(settings.data_dir / 'premarket_report_status.json', status)
    write_json(settings.data_dir / 'warnings.json', warnings)
    print(f"[premarket_report] mode={report_result.mode}" + (f" reason={report_result.fallback_reason}" if report_result.fallback_reason else ""))
    print(report)

    should_send = args.send_discord and not args.dry_run
    if should_send:
        try:
            DiscordWebhookClient(settings.discord_premarket_webhook_url).send_message(report)
            status['discord_delivery'] = 'sent'
        except Exception as exc:
            warning = f'Discord delivery failed: {exc}'
            warnings.append(warning)
            status['discord_delivery'] = 'failed'
            status['discord_error'] = str(exc)
            print('\n[discord_error] ' + warning)

    monitor_requested = args.send_to_monitor and not args.skip_monitor_update and settings.monitor_update_enabled
    can_update_monitor, monitor_skip_reason = _can_publish_to_monitor(
        report_result=report_result,
        evidence_pack=pack,
        settings=settings,
        trading_gate_reason=trading_decision.reason,
        evidence_errors=evidence_errors,
    )
    if monitor_requested and can_update_monitor:
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
            status['monitor_update'] = monitor_result
        except Exception as exc:
            warning = f'Monitor watchlist update failed: {exc}'
            warnings.append(warning)
            write_json(settings.data_dir / 'monitor_update_error.json', {'error': str(exc)})
            status['monitor_update'] = {'status': 'failed', 'error': str(exc)}
            print('\n[monitor_update_error] ' + warning)
    elif monitor_requested:
        monitor_result = {'status': 'skipped', 'reason': monitor_skip_reason}
        write_json(settings.data_dir / 'monitor_update_result.json', monitor_result)
        status['monitor_update'] = monitor_result
        print('\n[monitor_update] ' + str(monitor_result))

    status['warnings_count'] = len(warnings)
    status['warnings'] = warnings
    write_json(settings.data_dir / 'premarket_report_status.json', status)
    write_json(settings.data_dir / 'warnings.json', warnings)


def _make_quote_client(settings, warnings: List[str], market_data_errors: List[str]):
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
        message = f'FutuQuoteClient initialization failed: {exc}'
        warnings.append(message)
        market_data_errors.append(message)
        return None


def _safe_snapshots(
    qc,
    symbols: List[str],
    warnings: List[str],
    market_data_errors: List[str],
    allow_mock: bool = False,
) -> List[Dict]:
    if not symbols:
        return []
    try:
        return qc.get_market_snapshot(symbols)
    except Exception as exc:
        message = f'get_market_snapshot failed for {symbols[:5]}...: {exc}'
        warnings.append(message)
        market_data_errors.append(message)
        if allow_mock:
            return MockQuoteClient().get_market_snapshot(symbols)
        return []


def _fetch_news_items(news_urls: List[str], all_watch_symbols: List[str], warnings: List[str]) -> List[Dict[str, Any]]:
    try:
        return NewsRSSClient(news_urls).fetch(allowed_symbols=all_watch_symbols)
    except Exception as exc:
        warnings.append(f'News RSS fetch failed: {exc}')
        return [{
            'title': 'RSS fetch failed',
            'source': 'configured_rss_feeds',
            'url': '',
            'published_at': None,
            'summary': str(exc),
            'related_symbols': [],
            'event_type': 'warning',
            'sentiment': 'neutral',
            'news_score': 0,
            'is_error': True,
        }]


def _empty_technical(symbol: str) -> Dict[str, Any]:
    return {
        'symbol': symbol.upper(),
        'support': [],
        'resistance': [],
        'position': 'unknown',
    }


def _market_data_status(settings_demo_mode: bool, market_data_errors: List[str], snapshots: List[Dict[str, Any]]) -> str:
    if settings_demo_mode:
        return 'mock'
    if not snapshots:
        return 'unavailable'
    if market_data_errors:
        return 'partial'
    return 'available'


def _can_publish_to_monitor(
    report_result,
    evidence_pack: Dict[str, Any],
    settings,
    trading_gate_reason: str,
    evidence_errors: List[str],
) -> Tuple[bool, str]:
    if report_result.safe_mode:
        return False, 'safe_mode'
    data_status = evidence_pack.get('data_status') or {}
    market_data_status = data_status.get('market_data_status')
    if market_data_status in {'unavailable', 'failed'}:
        return False, 'market_data_unavailable'
    if data_status.get('mock_data') and not settings.monitor_test_mode:
        return False, 'mock_data_monitor_update_disabled'
    if trading_gate_reason != 'scheduled_us_trading_day' and not settings.monitor_test_mode:
        return False, 'non_trading_day_test_monitor_update_disabled'
    if any(_is_fatal_evidence_error(error) for error in evidence_errors):
        return False, 'fatal_evidence_errors'
    if not _has_reliable_candidates(evidence_pack):
        return False, 'no_reliable_candidates'
    return True, 'ok'


def _is_fatal_evidence_error(error: str) -> bool:
    return (
        error.startswith('missing_required_field:')
        or error == 'candidates_must_be_object'
        or error.startswith('missing_candidate_tier:')
    )


def _has_reliable_candidates(evidence_pack: Dict[str, Any]) -> bool:
    candidates = evidence_pack.get('candidates') or {}
    if not isinstance(candidates, dict):
        return False
    return bool((candidates.get('A') or []) or (candidates.get('B') or []))


if __name__ == '__main__':
    main()
