from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from app.reporting.etf_metadata import collect_unknown_etfs, format_etf_display_name, get_etf_metadata
from app.reporting.report_types import PremarketReportResult


PRIMARY_INDEX_SYMBOLS = ('SPY', 'QQQ', 'IWM', 'DIA')
SECTOR_CONFIRMATION_BY_SYMBOL = {
    'NVDA': ('SMH', 'SOXX'),
    'AMD': ('SMH', 'SOXX'),
    'AVGO': ('SMH', 'SOXX'),
    'QCOM': ('SMH', 'SOXX'),
    'INTC': ('SMH', 'SOXX'),
    'ARM': ('SMH', 'SOXX'),
    'MU': ('SMH', 'SOXX'),
    'TSM': ('SMH', 'SOXX'),
    'ASML': ('SMH', 'SOXX'),
    'MSFT': ('XLK', 'QQQ'),
    'AAPL': ('XLK', 'QQQ'),
    'AMZN': ('XLY', 'QQQ'),
    'GOOGL': ('XLC', 'QQQ'),
    'META': ('XLC', 'QQQ'),
    'TSLA': ('XLY', 'QQQ'),
    'CRM': ('XLK', 'QQQ'),
    'ORCL': ('XLK', 'QQQ'),
    'NOW': ('XLK', 'QQQ'),
    'PLTR': ('XLK', 'QQQ'),
    'SNOW': ('XLK', 'QQQ'),
    'XOM': ('XLE',),
    'CVX': ('XLE',),
    'COP': ('XLE',),
    'SLB': ('XLE',),
    'JNJ': ('XLV',),
    'PEP': ('XLP',),
    'KO': ('XLP',),
    'COST': ('XLP', 'XRT'),
    'WMT': ('XLP', 'XRT'),
}


@dataclass(frozen=True)
class SafeModeAssessment:
    safe_mode: bool
    reasons: List[str]
    missing_items: List[str]


@dataclass(frozen=True)
class CandidateExecutionPlan:
    symbol: str
    tier: str
    direction: str
    score: int | None
    trigger: str
    stop: str
    target: str
    status: str
    reason: str
    sector_confirmation: str


class RuleBasedPremarketReportGenerator:
    def generate(
        self,
        evidence_pack: Dict[str, Any],
        evidence_errors: List[str] | None = None,
        fallback_reason: str | None = None,
    ) -> PremarketReportResult:
        errors = list(evidence_errors or [])
        warnings = build_metadata_warnings(evidence_pack)
        assessment = assess_safe_mode(evidence_pack, errors)
        if assessment.safe_mode:
            report = render_safe_mode_report(
                evidence_pack=evidence_pack,
                assessment=assessment,
                evidence_errors=errors,
                fallback_reason=fallback_reason,
            )
            return PremarketReportResult(
                report=report,
                mode='safe_mode',
                fallback_reason=fallback_reason or ';'.join(assessment.reasons),
                warnings=warnings + assessment.reasons,
                safe_mode=True,
            )

        report = render_rule_based_report(
            evidence_pack=evidence_pack,
            evidence_errors=errors,
            metadata_warnings=warnings,
            fallback_reason=fallback_reason,
        )
        return PremarketReportResult(
            report=report,
            mode='rule_based',
            fallback_reason=fallback_reason,
            warnings=warnings,
            safe_mode=False,
        )

    def generate_safe_mode(
        self,
        evidence_pack: Dict[str, Any],
        reasons: List[str],
        fallback_reason: str | None = None,
    ) -> PremarketReportResult:
        assessment = SafeModeAssessment(
            safe_mode=True,
            reasons=list(reasons) or ['safe_mode_requested'],
            missing_items=list(reasons) or ['核心行情数据不足'],
        )
        report = render_safe_mode_report(
            evidence_pack=evidence_pack,
            assessment=assessment,
            evidence_errors=list(reasons),
            fallback_reason=fallback_reason,
        )
        return PremarketReportResult(
            report=report,
            mode='safe_mode',
            fallback_reason=fallback_reason or ';'.join(assessment.reasons),
            warnings=list(reasons),
            safe_mode=True,
        )


def assess_safe_mode(evidence_pack: Dict[str, Any], evidence_errors: List[str] | None = None) -> SafeModeAssessment:
    """Minimum execution requirements for a non-safe-mode premarket report.

    The report can still be produced with partial data, but execution candidates
    require: timestamp/context, a usable market-regime object, at least one valid
    primary index snapshot, no explicit market-data outage, and at least one valid
    candidate snapshot.
    """
    errors = list(evidence_errors or [])
    reasons: List[str] = []
    missing: List[str] = []

    if any(is_fatal_evidence_error(error) for error in errors):
        reasons.append('fatal_evidence_pack_errors')
        missing.extend(errors)

    if not evidence_pack.get('as_of'):
        reasons.append('missing_context_timestamp')
        missing.append('生成时间 / as_of')

    market_regime = evidence_pack.get('market_regime')
    if not isinstance(market_regime, dict) or not market_regime.get('label'):
        reasons.append('missing_market_regime')
        missing.append('市场环境计算结果')

    data_status = evidence_pack.get('data_status') or {}
    market_data_status = str(data_status.get('market_data_status') or '').lower()
    if market_data_status in {'unavailable', 'failed'}:
        reasons.append('missing_core_market_data')
        missing.extend(data_status.get('market_data_errors') or ['核心行情数据不可用'])

    index_snapshot = evidence_pack.get('index_snapshot')
    if not isinstance(index_snapshot, dict) or not has_valid_primary_index(index_snapshot):
        reasons.append('missing_primary_index_snapshot')
        missing.append('SPY（标普500）/ QQQ（纳斯达克100）等主要指数有效行情')

    if not has_any_valid_candidate_snapshot(evidence_pack):
        reasons.append('missing_candidate_snapshot')
        missing.append('候选股有效行情')

    unique_reasons = stable_unique(reasons)
    return SafeModeAssessment(
        safe_mode=bool(unique_reasons),
        reasons=unique_reasons,
        missing_items=stable_unique(missing),
    )


def is_fatal_evidence_error(error: str) -> bool:
    return (
        error.startswith('missing_required_field:')
        or error == 'candidates_must_be_object'
        or error.startswith('missing_candidate_tier:')
    )


def has_valid_primary_index(index_snapshot: Dict[str, Any]) -> bool:
    for symbol in PRIMARY_INDEX_SYMBOLS:
        snapshot = index_snapshot.get(symbol)
        if is_valid_snapshot(snapshot):
            return True
    return any(is_valid_snapshot(snapshot) for snapshot in index_snapshot.values())


def has_any_valid_candidate_snapshot(evidence_pack: Dict[str, Any]) -> bool:
    candidates = evidence_pack.get('candidates') or {}
    if not isinstance(candidates, dict):
        return False
    for row in candidates.get('all', []) or []:
        if isinstance(row, dict) and is_valid_snapshot(row.get('snapshot')):
            return True
    for tier in ('A', 'B', 'C', 'D'):
        for row in candidates.get(tier, []) or []:
            if isinstance(row, dict) and is_valid_snapshot(row.get('snapshot')):
                return True
    return False


def is_valid_snapshot(snapshot: Any) -> bool:
    if not isinstance(snapshot, dict) or not snapshot.get('symbol'):
        return False
    return any(to_float(snapshot.get(key)) is not None for key in ('last', 'effective_price', 'change_pct'))


def render_rule_based_report(
    evidence_pack: Dict[str, Any],
    evidence_errors: List[str],
    metadata_warnings: List[str],
    fallback_reason: str | None,
) -> str:
    market_regime = evidence_pack.get('market_regime') or {}
    as_of = safe_text(evidence_pack.get('as_of'), '不可用')
    data_status = evidence_pack.get('data_status') or {}
    news_status = describe_news_status(evidence_pack)
    candidate_plans = select_report_candidates(evidence_pack)
    risk_items = render_risk_items(evidence_pack, evidence_errors, metadata_warnings, candidate_plans)
    conclusion = market_conclusion_text(market_regime)
    style = trading_style_for_regime(market_regime)

    lines = [
        '# 盘前分析报告',
        '',
        '- 报告模式：规则兜底',
        f'- 截至时间：{as_of}',
        f'- 生成时间：{as_of}',
        f'- 大模型状态：{llm_status_text(fallback_reason)}',
        f'- 降级原因：{safe_text(fallback_reason, "未触发降级，使用规则生成")}',
        f'- 市场数据状态：{describe_market_data_status(data_status)}',
        f'- 新闻状态：{news_status}',
        '',
        '## 今日市场结论',
        '',
        f'今日市场结论：{conclusion}',
        f'交易风格建议：{style}',
        '',
        render_market_regime_section(market_regime),
        '',
        render_etf_overview(evidence_pack),
        '',
        render_candidate_section(candidate_plans),
        '',
        render_news_section(evidence_pack),
        '',
        '## 风险与不交易条件',
        '',
    ]
    lines.extend(f'- {item}' for item in risk_items)
    lines.extend([
        '',
        '## 最终结论',
        '',
        '本报告仅用于盘前研究观察，不构成投资建议；所有交易都必须等待价格、成交量与风险条件同步确认。',
    ])
    return '\n'.join(lines)


def render_safe_mode_report(
    evidence_pack: Dict[str, Any],
    assessment: SafeModeAssessment,
    evidence_errors: List[str],
    fallback_reason: str | None,
) -> str:
    as_of = safe_text(evidence_pack.get('as_of'), '不可用')
    data_status = evidence_pack.get('data_status') or {}
    missing = assessment.missing_items or ['核心行情数据不足']
    lines = [
        '# 盘前分析报告',
        '',
        '- 报告模式：SAFE_MODE',
        '- 执行结论：NO_TRADE',
        f'- 截至时间：{as_of}',
        f'- 生成时间：{as_of}',
        f'- 今日市场结论：NO_TRADE，核心行情数据不足，无法可靠计算市场方向、触发价、止损位和目标位。',
        '- 交易风格建议：不交易；等待真实行情数据恢复后重新生成报告。',
        f'- 大模型状态：{llm_status_text(fallback_reason)}',
        f'- 降级原因：{safe_text(fallback_reason, "核心行情数据不足")}',
        f'- 市场数据状态：{describe_market_data_status(data_status)}',
        f'- 新闻状态：{describe_news_status(evidence_pack)}',
        '',
        '## 市场环境',
        '',
        '市场环境：SAFE_MODE，核心数据不足。',
        '',
        '## 缺失内容',
        '',
    ]
    lines.extend(f'- {safe_text(item)}' for item in missing)
    if evidence_errors:
        lines.extend(['', '## Evidence 校验', ''])
        lines.extend(f'- {safe_text(item)}' for item in evidence_errors)
    lines.extend([
        '',
        '## 风险与不交易条件',
        '',
        '- 核心行情数据不可用或不完整，本次不会生成可执行候选。',
        '- 本次不会向实时监控系统发布交易候选。',
        '',
        '## 最终结论',
        '',
        'NO_TRADE。本报告仅用于系统状态记录，不构成投资建议。',
    ])
    return '\n'.join(lines)


def render_market_regime_section(market_regime: Dict[str, Any]) -> str:
    label = safe_text(market_regime.get('label'), 'unknown')
    score = format_number(market_regime.get('score'), digits=1)
    strong = format_etf_list(market_regime.get('strong_sectors') or [])
    weak = format_etf_list(market_regime.get('weak_sectors') or [])
    index_changes = market_regime.get('index_changes') or {}
    reason_lines = []
    for symbol in ordered_symbols(index_changes.keys(), PRIMARY_INDEX_SYMBOLS):
        change = format_pct(index_changes.get(symbol))
        reason_lines.append(f'- {format_etf_display_name(symbol)} 盘前涨跌：{change}。')
    if strong != '暂无明显':
        reason_lines.append(f'- 强势板块/行业：{strong}。')
    if weak != '暂无明显':
        reason_lines.append(f'- 弱势板块/行业：{weak}。')
    if not reason_lines:
        reason_lines.append('- 指数和行业 ETF 数据不足，市场环境确认度较低。')

    lines = [
        '## 市场环境',
        '',
        f'市场状态：{label}',
        f'市场环境评分：{score}',
        f'风险姿态：{risk_posture_for_regime(label)}',
        f'敞口建议：{exposure_for_regime(label)}',
        f'强势板块：{strong}',
        f'弱势板块：{weak}',
        '',
        '主要依据：',
        '',
    ]
    lines.extend(reason_lines)
    return '\n'.join(lines)


def render_etf_overview(evidence_pack: Dict[str, Any]) -> str:
    rows = []
    index_snapshot = evidence_pack.get('index_snapshot') or {}
    sector_snapshot = evidence_pack.get('sector_snapshot') or {}
    for symbol in ordered_symbols(index_snapshot.keys(), PRIMARY_INDEX_SYMBOLS):
        rows.append(render_etf_row(symbol, index_snapshot.get(symbol)))
    for symbol in ordered_symbols(sector_snapshot.keys(), ()):
        rows.append(render_etf_row(symbol, sector_snapshot.get(symbol)))
    if not rows:
        return '\n'.join([
            '## 指数与行业 ETF 概览',
            '',
            '指数与行业 ETF 行情不可用。',
        ])
    return '\n'.join([
        '## 指数与行业 ETF 概览',
        '',
        '| ETF | 中文含义 | 最新价 | 盘前涨跌 | 相对强弱 | 更新时间 |',
        '|---|---|---:|---:|---|---|',
        *rows,
    ])


def render_etf_row(symbol: str, snapshot: Any) -> str:
    metadata = get_etf_metadata(symbol)
    latest = format_price(snapshot_price(snapshot) if isinstance(snapshot, dict) else None)
    pct = snapshot_pct(snapshot) if isinstance(snapshot, dict) else None
    update_time = safe_text(snapshot.get('update_time') if isinstance(snapshot, dict) else None, '不可用')
    return (
        f'| {format_etf_display_name(symbol)} | {metadata.display_name_zh} | '
        f'{latest} | {format_pct(pct)} | {relative_strength_label(pct)} | {update_time} |'
    )


def select_report_candidates(evidence_pack: Dict[str, Any]) -> List[CandidateExecutionPlan]:
    candidates = evidence_pack.get('candidates') or {}
    if not isinstance(candidates, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for tier in ('A', 'B'):
        for row in candidates.get(tier, []) or []:
            if isinstance(row, dict):
                rows.append(row)
    tier_rank = {'A': 0, 'B': 1}
    rows.sort(key=lambda row: (tier_rank.get(str(row.get('tier')), 99), -safe_int(row.get('priority_score'), -1), str(row.get('symbol', ''))))
    return [build_candidate_execution_plan(row, evidence_pack) for row in rows]


def build_candidate_execution_plan(row: Dict[str, Any], evidence_pack: Dict[str, Any]) -> CandidateExecutionPlan:
    symbol = safe_text(row.get('symbol'), 'UNKNOWN').upper()
    tier = safe_text(row.get('tier'), 'N/A')
    score = safe_int(row.get('priority_score'), None)
    technical = row.get('technical') if isinstance(row.get('technical'), dict) else {}
    snapshot = row.get('snapshot') if isinstance(row.get('snapshot'), dict) else {}
    support = first_number(technical.get('support'))
    resistance = first_number(technical.get('resistance'))
    target = target_level(technical, resistance)
    vwap = to_float(technical.get('vwap'))

    direction = '条件观察'
    trigger = '数据不足'
    stop = '数据不足'
    target_text = '数据不足'
    status = '仅观察'
    reason = '缺少可靠的盘前区间、支撑/阻力或目标位数据'

    if resistance is not None:
        if vwap is not None:
            trigger = f'突破 {format_price(resistance)} 并维持在 VWAP {format_price(vwap)} 上方'
        else:
            trigger = f'突破 {format_price(resistance)} 后等待成交量确认'
    if support is not None:
        stop = f'跌破 {format_price(support)}'
    if target is not None:
        target_text = format_price(target)

    if resistance is not None and support is not None and target is not None:
        status = '可观察'
        reason = candidate_reason_text(row, evidence_pack)

    sector_confirmation = sector_confirmation_text(row, evidence_pack)
    if status == '仅观察' and not is_valid_snapshot(snapshot):
        reason = '候选股实时行情不可用，不能生成执行价位'

    return CandidateExecutionPlan(
        symbol=symbol,
        tier=tier,
        direction=direction,
        score=score,
        trigger=trigger,
        stop=stop,
        target=target_text,
        status=status,
        reason=reason,
        sector_confirmation=sector_confirmation,
    )


def render_candidate_section(plans: List[CandidateExecutionPlan]) -> str:
    lines = [
        '## 盘前候选',
        '',
    ]
    if not plans:
        lines.extend([
            '暂无 A/B 级候选，执行结论：NO_TRADE。',
            '',
            '| 股票 | 等级 | 方向 | 评分 | 触发条件 | 止损参考 | 目标参考 | 状态 |',
            '|---|---|---|---:|---|---|---|---|',
        ])
        return '\n'.join(lines)

    lines.extend([
        '| 股票 | 等级 | 方向 | 评分 | 触发条件 | 止损参考 | 目标参考 | 状态 |',
        '|---|---|---|---:|---|---|---|---|',
    ])
    for plan in plans:
        status = plan.status
        if plan.sector_confirmation:
            status = f'{status}；{plan.sector_confirmation}'
        elif plan.reason:
            status = f'{status}；{plan.reason}'
        lines.append(
            f'| {plan.symbol} | {plan.tier} | {plan.direction} | {format_score(plan.score)} | '
            f'{plan.trigger} | {plan.stop} | {plan.target} | {status} |'
        )
    return '\n'.join(lines)


def render_news_section(evidence_pack: Dict[str, Any]) -> str:
    rows = []
    candidates = evidence_pack.get('candidates') or {}
    if isinstance(candidates, dict):
        for tier in ('A', 'B'):
            for item in candidates.get(tier, []) or []:
                if not isinstance(item, dict):
                    continue
                symbol = safe_text(item.get('symbol'), 'UNKNOWN')
                for news in item.get('news') or []:
                    if isinstance(news, dict) and not news.get('is_error'):
                        rows.append((symbol, news))
    rows = rows[:8]
    if not rows:
        return '\n'.join([
            '## 新闻与催化',
            '',
            '新闻数据不可用，本次结论仅基于行情、技术位和已有评分。',
        ])

    lines = [
        '## 新闻与催化',
        '',
        '| 股票 | 标题 | 来源 | 发布时间 | 类别 | 情绪 |',
        '|---|---|---|---|---|---|',
    ]
    for symbol, news in rows:
        lines.append(
            f'| {symbol} | {safe_text(news.get("title"), "无标题")} | '
            f'{short_source(news.get("source"))} | {safe_text(news.get("published_at"), "不可用")} | '
            f'{safe_text(news.get("event_type"), "other")} | {safe_text(news.get("sentiment"), "neutral")} |'
        )
    return '\n'.join(lines)


def render_risk_items(
    evidence_pack: Dict[str, Any],
    evidence_errors: List[str],
    metadata_warnings: List[str],
    candidate_plans: List[CandidateExecutionPlan],
) -> List[str]:
    items: List[str] = []
    data_status = evidence_pack.get('data_status') or {}
    market_data_status = str(data_status.get('market_data_status') or '').lower()
    if market_data_status == 'mock':
        items.append('当前使用 MockQuoteClient 模拟行情，不能作为真实交易依据。')
    if market_data_status in {'partial', 'unavailable', 'failed'}:
        items.append('行情数据不完整；缺失指数、行业或候选股行情时不发布新交易候选。')
    if data_status.get('non_trading_day_test'):
        items.append('本次为非交易日或强制测试运行，不应更新生产实时监控。')
    if evidence_errors:
        items.append('Evidence pack 存在校验警告：' + '；'.join(evidence_errors[:5]) + '。')
    if metadata_warnings:
        items.extend(metadata_warnings[:5])
    if not candidate_plans:
        items.append('暂无 A/B 级候选，盘前不主动交易。')
    elif all(plan.status == '仅观察' for plan in candidate_plans):
        items.append('全部候选缺少可靠触发价、止损位或目标位，仅可观察，不可直接执行。')
    if sector_confirmation_unavailable(evidence_pack, candidate_plans):
        items.append('部分候选缺少可确认的行业 ETF 强弱证据。')
    news_summary = evidence_pack.get('news_summary') or {}
    if news_summary.get('errors'):
        items.append('部分 RSS 新闻源抓取失败，新闻催化覆盖可能不完整。')
    if not items:
        items.append('若指数 ETF 转弱、候选跌破关键支撑、成交量不足或新闻来源不可靠，则不交易。')
    return stable_unique(items)


def build_metadata_warnings(evidence_pack: Dict[str, Any]) -> List[str]:
    symbols: set[str] = set()
    symbols.update((evidence_pack.get('index_snapshot') or {}).keys())
    symbols.update((evidence_pack.get('sector_snapshot') or {}).keys())
    market_regime = evidence_pack.get('market_regime') or {}
    symbols.update(market_regime.get('strong_sectors') or [])
    symbols.update(market_regime.get('weak_sectors') or [])
    unknown = collect_unknown_etfs(symbols)
    return [f'ETF metadata missing: {symbol}（行业未知）' for symbol in unknown]


def candidate_reason_text(row: Dict[str, Any], evidence_pack: Dict[str, Any]) -> str:
    reasons = row.get('reasons') or []
    if not reasons:
        return '等待价格行为确认'
    translated = {
        'news_catalyst': '新闻催化',
        'large_premarket_move': '盘前大幅波动',
        'moderate_premarket_move': '盘前异动',
        'volume_spike': '成交量放大',
        'above_average_volume': '成交量高于均值',
        'near_breakout_zone': '接近突破区域',
        'sector_strength_confirmation': '行业确认',
        'market_regime_supportive': '市场环境支持',
    }
    out = []
    for reason in reasons:
        matched = next((label for key, label in translated.items() if str(reason).startswith(key)), None)
        out.append(matched or safe_text(reason))
    return '；'.join(stable_unique(out))


def sector_confirmation_text(row: Dict[str, Any], evidence_pack: Dict[str, Any]) -> str:
    explicit = row.get('sector_confirmation_etf')
    strong = set((evidence_pack.get('market_regime') or {}).get('strong_sectors') or [])
    symbol = safe_text(row.get('symbol'), '').upper()
    selected = None
    if explicit:
        selected = safe_text(explicit).upper()
    else:
        for candidate in SECTOR_CONFIRMATION_BY_SYMBOL.get(symbol, ()):
            if candidate in strong:
                selected = candidate
                break
    if selected:
        return f'行业确认：{format_etf_display_name(selected)}保持强势'
    return ''


def sector_confirmation_unavailable(evidence_pack: Dict[str, Any], candidate_plans: List[CandidateExecutionPlan]) -> bool:
    if not candidate_plans:
        return False
    return any(not plan.sector_confirmation for plan in candidate_plans)


def target_level(technical: Dict[str, Any], trigger: float | None) -> float | None:
    resistances = [value for value in numeric_list(technical.get('resistance')) if trigger is None or value > trigger]
    if resistances:
        return resistances[0]
    for key in ('recent_high_20d', 'recent_high_60d'):
        value = to_float(technical.get(key))
        if value is not None and (trigger is None or value > trigger):
            return value
    return None


def first_number(values: Any) -> float | None:
    vals = numeric_list(values)
    return vals[0] if vals else None


def numeric_list(values: Any) -> List[float]:
    if not isinstance(values, list):
        return []
    out = [to_float(value) for value in values]
    return [value for value in out if value is not None]


def describe_market_data_status(data_status: Dict[str, Any]) -> str:
    status = str(data_status.get('market_data_status') or '').lower()
    source = str(data_status.get('market_data_source') or '').lower()
    if status == 'mock' or source == 'mock':
        return '模拟行情（MockQuoteClient）'
    if status == 'available':
        return '可用'
    if status == 'partial':
        return '部分可用'
    if status in {'unavailable', 'failed'}:
        return '不可用'
    return '未知'


def describe_news_status(evidence_pack: Dict[str, Any]) -> str:
    news_summary = evidence_pack.get('news_summary') or {}
    total = safe_int(news_summary.get('total_items'), 0) or 0
    errors = news_summary.get('errors') or []
    if total > 0 and errors:
        return '部分可用'
    if total > 0:
        return '可用'
    if errors:
        return '不可用'
    return '不可用'


def llm_status_text(fallback_reason: str | None) -> str:
    if fallback_reason:
        return '调用失败或未启用，已自动降级'
    return '未调用，使用规则报告'


def trading_style_for_regime(market_regime: Dict[str, Any]) -> str:
    label = safe_text(market_regime.get('label'), 'unknown')
    if label in {'strong_risk_on', 'mild_risk_on'}:
        return '允许小仓位条件化做多，只在触发价、成交量和指数环境同时确认后行动。'
    if label == 'neutral_choppy':
        return '降低交易频率，以观察和等待确认突破为主。'
    if label in {'mild_risk_off', 'strong_risk_off'}:
        return '降低多头敞口；若无明确反转和风控价位，维持观望。'
    return '市场状态未知，以观察为主。'


def market_conclusion_text(market_regime: Dict[str, Any]) -> str:
    label = safe_text(market_regime.get('label'), 'unknown')
    score = format_number(market_regime.get('score'), digits=1)
    strong = format_etf_list(market_regime.get('strong_sectors') or [])
    weak = format_etf_list(market_regime.get('weak_sectors') or [])
    return f'市场状态 {label}，评分 {score}；强势板块：{strong}；弱势板块：{weak}。'


def risk_posture_for_regime(label: str) -> str:
    return {
        'strong_risk_on': '风险偏好强',
        'mild_risk_on': '风险偏好偏强',
        'neutral_choppy': '震荡中性',
        'mild_risk_off': '风险偏好偏弱',
        'strong_risk_off': '风险规避',
    }.get(label, '未知')


def exposure_for_regime(label: str) -> str:
    return {
        'strong_risk_on': '支持条件化多头敞口',
        'mild_risk_on': '支持有限多头敞口',
        'neutral_choppy': '减少交易，等待方向',
        'mild_risk_off': '降低交易或仅观察',
        'strong_risk_off': '不主动交易',
    }.get(label, '仅观察')


def format_etf_list(symbols: Iterable[str]) -> str:
    values = [format_etf_display_name(symbol) for symbol in symbols if safe_text(symbol, '')]
    return '、'.join(values) if values else '暂无明显'


def ordered_symbols(symbols: Iterable[str], preferred: Iterable[str]) -> List[str]:
    normalized = [str(symbol).upper() for symbol in symbols if str(symbol or '').strip()]
    preferred_list = [symbol for symbol in preferred if symbol in normalized]
    rest = sorted(symbol for symbol in normalized if symbol not in set(preferred_list))
    return preferred_list + rest


def snapshot_price(snapshot: Dict[str, Any]) -> float | None:
    return to_float(snapshot.get('effective_price')) or to_float(snapshot.get('last'))


def snapshot_pct(snapshot: Dict[str, Any]) -> float | None:
    return to_float(snapshot.get('effective_change_pct')) or to_float(snapshot.get('change_pct'))


def relative_strength_label(value: float | None) -> str:
    if value is None:
        return '不可用'
    if value >= 0.6:
        return '强'
    if value <= -0.6:
        return '弱'
    return '中性'


def format_price(value: Any) -> str:
    number = to_float(value)
    if number is None:
        return '不可用'
    return f'{number:.2f}'


def format_pct(value: Any) -> str:
    number = to_float(value)
    if number is None:
        return '不可用'
    sign = '+' if number > 0 else ''
    return f'{sign}{number:.2f}%'


def format_number(value: Any, digits: int = 2) -> str:
    number = to_float(value)
    if number is None:
        return '不可用'
    return f'{number:.{digits}f}'


def format_score(value: int | None) -> str:
    if value is None:
        return '不可用'
    return str(value)


def to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def safe_int(value: Any, default: int | None = 0) -> int | None:
    try:
        if value is None:
            return default
        return int(round(float(value)))
    except Exception:
        return default


def safe_text(value: Any, default: str = '') -> str:
    if value is None:
        return default
    text = str(value).replace('\n', ' ').replace('|', '/').strip()
    return text if text else default


def short_source(value: Any) -> str:
    text = safe_text(value, '不可用')
    if len(text) <= 54:
        return text
    return text[:51] + '...'


def stable_unique(values: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        text = safe_text(value)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
