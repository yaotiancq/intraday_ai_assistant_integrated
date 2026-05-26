from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.utils.console import configure_utf8_stdio
from app.config import load_settings
from app.data_sources.futu_client import FutuQuoteClient, MockQuoteClient
from app.data_sources.news_rss_client import NewsRSSClient
from app.indicators.technical_indicators import build_technical_levels
from app.llm.openai_client import OpenAIReportClient
from app.utils.file_io import write_json, write_text
from app.utils.time_utils import trading_context


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description='Generate single stock analysis.')
    parser.add_argument('--symbol', required=True, help='Plain ticker, e.g. SATS')
    parser.add_argument('--env-file', default=None)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    settings = load_settings(args.env_file)
    symbol = args.symbol.upper()
    warnings = []
    ctx = trading_context(settings.timezone)

    if settings.demo_mode:
        qc = MockQuoteClient()
        warnings.append('DEMO_MODE=true，使用模拟行情。')
    else:
        try:
            qc = FutuQuoteClient(settings.futu_host, settings.futu_port, settings.futu_market_prefix)
        except Exception as exc:
            qc = MockQuoteClient()
            warnings.append(f'Futu 初始化失败，使用模拟行情：{exc}')

    with qc:
        snap = qc.get_market_snapshot([symbol])[0]
        end = datetime.now().date().isoformat()
        start = (datetime.now().date() - timedelta(days=120)).isoformat()
        daily = qc.request_history_kline(symbol, start=start, end=end, ktype='K_DAY')
        intraday = qc.get_realtime_kline(symbol, ktype='K_1M', num=240)
        tech = build_technical_levels(symbol, daily, intraday, snap.get('last'), snap.get('volume'))

    news = NewsRSSClient(settings.news_rss_urls).fetch(allowed_symbols=[symbol])
    pack = {
        'symbol': symbol,
        'as_of': ctx['as_of'],
        'session': ctx['session'],
        'snapshot': snap,
        'technical': tech,
        'news': news,
        'warnings': warnings,
        'analysis_constraints': {
            'do_not_give_investment_advice': True,
            'condition_based_language_only': True,
            'distinguish_fact_from_rumor': True,
        },
    }
    write_json(settings.data_dir / f'evidence_pack_{symbol}.json', pack)
    report = OpenAIReportClient(settings.openai_api_key, settings.openai_model).generate_single_stock_report(pack)
    if warnings:
        report += '\n\n系统警告：\n' + '\n'.join(f'- {w}' for w in warnings)
    write_text(settings.data_dir / f'stock_report_{symbol}.md', report)
    print(report)


if __name__ == '__main__':
    main()
