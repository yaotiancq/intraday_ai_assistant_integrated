from __future__ import annotations

from types import SimpleNamespace

from app.data_sources.futu_client import MockQuoteClient
from app.reporting.etf_metadata import format_etf_display_name, get_etf_metadata
from app.reporting.premarket_rule_report import RuleBasedPremarketReportGenerator
from app.reporting.report_router import PremarketReportRouter
from app.reporting.report_types import PremarketReportResult
from scripts import run_premarket


VALID_LLM_REPORT = """
截至时间：2026-07-06 08:30 ET
今日市场结论：市场风险偏好偏强，观察指数和行业确认。
交易风格建议：只在触发条件、成交量和风控价位同步确认后观察。
市场环境：QQQ（纳斯达克100）与 SMH（半导体）相对较强。
盘前候选：NVDA 条件化观察。
风险：若指数转弱、候选跌破支撑或新闻证据不足，则不交易。
本报告仅用于研究观察，不构成投资建议。
""".strip()


class FakeOpenAIClient:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response
        self.exc = exc
        self.calls = 0

    def generate_premarket_report(self, evidence_pack):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.response


class SpyRuleGenerator(RuleBasedPremarketReportGenerator):
    def __init__(self):
        self.called = False
        super().__init__()

    def generate(self, *args, **kwargs):
        self.called = True
        return super().generate(*args, **kwargs)


def sample_pack() -> dict:
    nvda = {
        'symbol': 'NVDA',
        'priority_score': 86,
        'tier': 'A',
        'reasons': ['near_breakout_zone', 'sector_strength_confirmation', 'market_regime_supportive'],
        'risk_flags': [],
        'news_count': 0,
        'snapshot': {'symbol': 'NVDA', 'last': 125.4, 'prev_close': 123.0, 'change_pct': 1.95, 'volume': 2500000},
        'technical': {
            'symbol': 'NVDA',
            'vwap': 124.8,
            'support': [122.5, 120.0],
            'resistance': [126.0, 130.0],
            'recent_high_20d': 130.0,
            'recent_high_60d': 136.0,
            'position': 'near_range_high',
        },
        'news': [],
        'sector_confirmation_etf': 'SMH',
    }
    amd = {
        'symbol': 'AMD',
        'priority_score': 70,
        'tier': 'B',
        'reasons': ['moderate_premarket_move'],
        'risk_flags': [],
        'news_count': 0,
        'snapshot': {'symbol': 'AMD', 'last': 166.2, 'prev_close': 164.0, 'change_pct': 1.34, 'volume': 900000},
        'technical': {
            'symbol': 'AMD',
            'vwap': 165.0,
            'support': [162.0],
            'resistance': [168.0, 172.0],
            'recent_high_20d': 172.0,
            'position': 'middle_of_range',
        },
        'news': [],
    }
    return {
        'as_of': '2026-07-06 08:30 ET',
        'date': '2026-07-06',
        'timezone': 'America/New_York',
        'session': 'premarket',
        'is_trading_day': True,
        'data_status': {
            'market_data_status': 'available',
            'market_data_source': 'futu',
            'market_data_errors': [],
            'mock_data': False,
            'non_trading_day_test': False,
            'trading_day_gate_reason': 'scheduled_us_trading_day',
        },
        'market_regime': {
            'score': 38.2,
            'label': 'mild_risk_on',
            'summary': 'raw summary should not be rendered',
            'index_changes': {'SPY': 0.31, 'QQQ': 0.62, 'IWM': -0.12, 'DIA': 0.08},
            'strong_sectors': ['SMH', 'XLK'],
            'weak_sectors': ['XLF'],
        },
        'index_snapshot': {
            'SPY': {'symbol': 'SPY', 'last': 520.2, 'prev_close': 518.8, 'change_pct': 0.31, 'update_time': '08:30'},
            'QQQ': {'symbol': 'QQQ', 'last': 445.8, 'prev_close': 443.0, 'change_pct': 0.62, 'update_time': '08:30'},
        },
        'sector_snapshot': {
            'SMH': {'symbol': 'SMH', 'last': 230.4, 'prev_close': 228.1, 'change_pct': 1.01, 'update_time': '08:30'},
            'XLK': {'symbol': 'XLK', 'last': 220.4, 'prev_close': 219.0, 'change_pct': 0.64, 'update_time': '08:30'},
            'XLF': {'symbol': 'XLF', 'last': 41.5, 'prev_close': 42.1, 'change_pct': -1.43, 'update_time': '08:30'},
        },
        'news_summary': {'total_items': 0, 'errors': []},
        'candidates': {'A': [nvda], 'B': [amd], 'C': [], 'D': [], 'all': [nvda, amd]},
        'analysis_constraints': {'must_include_as_of_time': True},
    }


def test_router_uses_valid_llm_without_rule_fallback():
    client = FakeOpenAIClient(response=VALID_LLM_REPORT)
    spy_rule = SpyRuleGenerator()
    router = PremarketReportRouter(
        api_key='key',
        model='model',
        openai_client_factory=lambda api_key, model: client,
        rule_generator=spy_rule,
        max_retries=0,
        retry_delay_seconds=0,
    )

    result = router.generate(sample_pack())

    assert result.mode == 'llm'
    assert result.llm_attempted is True
    assert result.llm_succeeded is True
    assert spy_rule.called is False
    assert client.calls == 1


def test_router_falls_back_when_openai_raises():
    client = FakeOpenAIClient(exc=TimeoutError('request timeout'))
    router = PremarketReportRouter(
        api_key='key',
        model='model',
        openai_client_factory=lambda api_key, model: client,
        max_retries=0,
        retry_delay_seconds=0,
    )

    result = router.generate(sample_pack())

    assert result.mode == 'rule_based'
    assert result.fallback_reason == 'openai_timeout'
    assert result.llm_attempted is True
    assert '报告模式：规则兜底' in result.report


def test_router_missing_api_key_does_not_call_openai():
    called = False

    def factory(api_key, model):
        nonlocal called
        called = True
        return FakeOpenAIClient(response=VALID_LLM_REPORT)

    result = PremarketReportRouter(api_key='', model='model', openai_client_factory=factory).generate(sample_pack())

    assert result.mode == 'rule_based'
    assert result.fallback_reason == 'missing_openai_api_key'
    assert result.llm_attempted is False
    assert called is False


def test_router_empty_openai_response_falls_back():
    client = FakeOpenAIClient(response='   ')
    result = PremarketReportRouter(
        api_key='key',
        model='model',
        openai_client_factory=lambda api_key, model: client,
        max_retries=0,
        retry_delay_seconds=0,
    ).generate(sample_pack())

    assert result.mode == 'rule_based'
    assert result.fallback_reason == 'openai_blank_response'


def test_router_validation_failure_falls_back_and_records_errors():
    invalid = '截至时间：x\n今日市场结论：x\n' + ('内容' * 80)
    client = FakeOpenAIClient(response=invalid)
    result = PremarketReportRouter(
        api_key='key',
        model='model',
        openai_client_factory=lambda api_key, model: client,
        max_retries=0,
        retry_delay_seconds=0,
    ).generate(sample_pack())

    assert result.mode == 'rule_based'
    assert result.fallback_reason == 'openai_report_validation_failed'
    assert any(error.startswith('missing_section') for error in result.llm_validation_errors)


def test_router_disable_llm_does_not_initialize_openai():
    called = False

    def factory(api_key, model):
        nonlocal called
        called = True
        return FakeOpenAIClient(response=VALID_LLM_REPORT)

    result = PremarketReportRouter(
        api_key='key',
        model='model',
        llm_enabled=False,
        openai_client_factory=factory,
    ).generate(sample_pack())

    assert result.mode == 'rule_based'
    assert result.fallback_reason == 'llm_disabled'
    assert result.llm_attempted is False
    assert called is False


def test_rule_based_report_is_deterministic_and_uses_levels():
    generator = RuleBasedPremarketReportGenerator()
    first = generator.generate(sample_pack(), fallback_reason='llm_disabled')
    second = generator.generate(sample_pack(), fallback_reason='llm_disabled')

    assert first.report == second.report
    assert '| NVDA | A | 条件观察 | 86 | 突破 126.00 并维持在 VWAP 124.80 上方 | 跌破 122.50 | 130.00 |' in first.report
    assert 'SMH（半导体）' in first.report
    assert 'XLF（金融）' in first.report


def test_rule_based_report_handles_missing_news():
    result = RuleBasedPremarketReportGenerator().generate(sample_pack(), fallback_reason='llm_disabled')

    assert '新闻数据不可用，本次结论仅基于行情、技术位和已有评分。' in result.report


def test_candidate_missing_technical_levels_is_observation_only():
    pack = sample_pack()
    pack['candidates']['A'][0]['technical'] = {'symbol': 'NVDA', 'support': [], 'resistance': [], 'position': 'unknown'}
    result = RuleBasedPremarketReportGenerator().generate(pack, fallback_reason='llm_disabled')

    assert result.mode == 'rule_based'
    assert 'NVDA' in result.report
    assert '仅观察' in result.report
    assert '数据不足' in result.report


def test_no_a_or_b_candidates_reports_no_trade_without_crashing():
    pack = sample_pack()
    pack['candidates']['A'] = []
    pack['candidates']['B'] = []
    pack['candidates']['D'] = pack['candidates']['all']

    result = RuleBasedPremarketReportGenerator().generate(pack, fallback_reason='llm_disabled')

    assert result.mode == 'rule_based'
    assert '暂无 A/B 级候选，执行结论：NO_TRADE。' in result.report


def test_missing_core_market_data_enters_safe_mode():
    pack = sample_pack()
    pack['index_snapshot'] = {}
    pack['data_status']['market_data_status'] = 'unavailable'
    pack['data_status']['market_data_errors'] = ['futu unavailable']

    result = RuleBasedPremarketReportGenerator().generate(pack, fallback_reason='openai_timeout')

    assert result.mode == 'safe_mode'
    assert result.safe_mode is True
    assert 'NO_TRADE' in result.report
    assert 'futu unavailable' in result.report


def test_known_and_unknown_etf_labels():
    assert format_etf_display_name('SMH') == 'SMH（半导体）'
    assert format_etf_display_name('IGV') == 'IGV（软件）'
    assert format_etf_display_name('XLF') == 'XLF（金融）'
    assert format_etf_display_name('abc') == 'ABC（行业未知）'


def test_unknown_etf_renders_warning_without_crash():
    pack = sample_pack()
    pack['sector_snapshot']['ABC'] = {'symbol': 'ABC', 'last': 10.0, 'prev_close': 9.9, 'change_pct': 1.0}
    pack['market_regime']['strong_sectors'] = ['ABC']

    result = RuleBasedPremarketReportGenerator().generate(pack, fallback_reason='llm_disabled')

    assert result.mode == 'rule_based'
    assert 'ABC（行业未知）' in result.report
    assert any('ETF metadata missing: ABC（行业未知）' in warning for warning in result.warnings)


def test_configured_etfs_have_metadata_entries():
    configured = ['SPY', 'QQQ', 'IWM', 'DIA', 'XLK', 'SMH', 'SOXX', 'XLC', 'XLY', 'XLF', 'XLE', 'XLV', 'XLP', 'XLU', 'XLI', 'XRT']

    assert [symbol for symbol in configured if get_etf_metadata(symbol).is_unknown] == []


def test_candidate_sector_confirmation_contains_chinese_etf_name():
    result = RuleBasedPremarketReportGenerator().generate(sample_pack(), fallback_reason='llm_disabled')

    assert '行业确认：SMH（半导体）保持强势' in result.report


def test_demo_mode_allows_mock_client():
    warnings = []
    errors = []
    settings = SimpleNamespace(demo_mode=True)

    client = run_premarket._make_quote_client(settings, warnings, errors)

    assert isinstance(client, MockQuoteClient)
    assert errors == []
    assert any('DEMO_MODE=true' in warning for warning in warnings)


def test_production_futu_initialization_failure_does_not_use_mock(monkeypatch):
    def fail_init(**kwargs):
        raise RuntimeError('opend unavailable')

    monkeypatch.setattr(run_premarket, 'FutuQuoteClient', fail_init)
    settings = SimpleNamespace(
        demo_mode=False,
        futu_host='127.0.0.1',
        futu_port=11111,
        futu_market_prefix='US',
        futu_extended_time=True,
    )
    warnings = []
    errors = []

    client = run_premarket._make_quote_client(settings, warnings, errors)

    assert client is None
    assert errors
    assert 'fallback to mock' not in warnings[0]


def test_production_snapshot_failure_does_not_return_mock():
    class BadClient:
        def get_market_snapshot(self, symbols):
            raise RuntimeError('snapshot failed')

    warnings = []
    errors = []

    snapshots = run_premarket._safe_snapshots(BadClient(), ['SPY'], warnings, errors, allow_mock=False)

    assert snapshots == []
    assert errors


def test_monitor_publication_allowed_for_valid_rule_based_evidence():
    result = PremarketReportResult(report='x', mode='rule_based', safe_mode=False)
    settings = SimpleNamespace(monitor_test_mode=False)

    allowed, reason = run_premarket._can_publish_to_monitor(
        report_result=result,
        evidence_pack=sample_pack(),
        settings=settings,
        trading_gate_reason='scheduled_us_trading_day',
        evidence_errors=[],
    )

    assert allowed is True
    assert reason == 'ok'


def test_monitor_publication_blocked_in_safe_mode():
    result = PremarketReportResult(report='x', mode='safe_mode', safe_mode=True)
    settings = SimpleNamespace(monitor_test_mode=False)

    allowed, reason = run_premarket._can_publish_to_monitor(
        report_result=result,
        evidence_pack=sample_pack(),
        settings=settings,
        trading_gate_reason='scheduled_us_trading_day',
        evidence_errors=[],
    )

    assert allowed is False
    assert reason == 'safe_mode'
