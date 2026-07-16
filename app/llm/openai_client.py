from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict
from app.llm.prompts import build_premarket_prompt, build_single_stock_prompt


@dataclass
class OpenAIReportClient:
    api_key: str
    model: str = 'gpt-5-mini'
    fallback_on_error: bool = True

    def generate_premarket_report(self, evidence_pack: Dict[str, Any]) -> str:
        if not self.api_key:
            if not self.fallback_on_error:
                raise ValueError('OPENAI_API_KEY is empty')
            return fallback_premarket_report(evidence_pack, reason='OPENAI_API_KEY is empty')
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            resp = client.responses.create(
                model=self.model,
                input=build_premarket_prompt(evidence_pack),
            )
            return getattr(resp, 'output_text', '') or str(resp)
        except Exception as exc:
            if not self.fallback_on_error:
                raise
            return fallback_premarket_report(evidence_pack, reason=f'OpenAI call failed: {exc}')

    def generate_single_stock_report(self, evidence_pack: Dict[str, Any]) -> str:
        if not self.api_key:
            return fallback_single_stock_report(evidence_pack, reason='OPENAI_API_KEY is empty')
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            resp = client.responses.create(
                model=self.model,
                input=build_single_stock_prompt(evidence_pack),
            )
            return getattr(resp, 'output_text', '') or str(resp)
        except Exception as exc:
            return fallback_single_stock_report(evidence_pack, reason=f'OpenAI call failed: {exc}')


def fallback_premarket_report(pack: Dict[str, Any], reason: str = '') -> str:
    mr = pack.get('market_regime', {})
    a = pack.get('candidates', {}).get('A', [])
    b = pack.get('candidates', {}).get('B', [])
    lines = [
        f"截至时间：{pack.get('as_of')}",
        f"今日市场结论：{mr.get('summary', '市场状态未知')}",
        "交易风格建议：以观察为主，只在价格行为确认后考虑，不追无量跳空。",
        "",
        "一、市场环境",
        f"- 市场状态：{mr.get('label')}，评分 {mr.get('score')}",
        f"- 指数观察：{mr.get('index_changes', {})}",
        f"- 强势板块：{', '.join(mr.get('strong_sectors', [])) or '暂无明显'}",
        f"- 弱势板块：{', '.join(mr.get('weak_sectors', [])) or '暂无明显'}",
        f"- 今日主要风险：LLM 未调用或失败，使用规则兜底报告。{reason}",
        "",
        "二、A 级重点观察",
    ]
    if not a:
        lines.append("暂无 A 级股票。")
    for item in a:
        sym = item['symbol']
        tech = item.get('technical', {})
        snap = item.get('snapshot', {})
        lines += [
            f"1. {sym}：",
            f"   - 优先级分数：{item.get('priority_score')}",
            f"   - 核心催化：{', '.join(item.get('reasons', [])) or '无明显催化'}",
            f"   - 盘前/当前表现：价格 {snap.get('last')}，涨跌幅 {snap.get('change_pct')}%",
            f"   - 技术位置：{tech.get('position')}，支撑 {tech.get('support')}，阻力 {tech.get('resistance')}",
            f"   - 偏强触发：若站上最近阻力 {first_or_na(tech.get('resistance'))} 且维持在 VWAP 附近上方，再观察延续。",
            f"   - 回踩观察：若回踩支撑 {first_or_na(tech.get('support'))} 附近缩量企稳，可继续观察。",
            f"   - 失效条件：跌破关键支撑且大盘同步转弱。",
            f"   - 风险：{', '.join(item.get('risk_flags', [])) or '常规波动风险'}",
        ]
    lines += ["", "三、B 级次级观察"]
    if not b:
        lines.append("暂无 B 级股票。")
    for item in b[:8]:
        lines.append(f"- {item['symbol']}：分数 {item.get('priority_score')}，原因 {', '.join(item.get('reasons', []))}；只在触发关键价位后观察。")
    lines += [
        "",
        "四、C/D 级与暂不优先",
        "优先级不足、催化不强、流动性弱或市场环境不匹配的股票暂不优先。",
        "",
        "五、最终结论",
        "本报告仅用于盘前交易观察，不构成投资建议。",
    ]
    return '\n'.join(lines)


def fallback_single_stock_report(pack: Dict[str, Any], reason: str = '') -> str:
    sym = pack.get('symbol', 'UNKNOWN')
    tech = pack.get('technical', {})
    snap = pack.get('snapshot', {})
    return '\n'.join([
        f"截至时间：{pack.get('as_of')}",
        f"结论：{sym} 当前以条件化观察为主，规则兜底报告。",
        f"近期走势：当前价 {snap.get('last')}，涨跌幅 {snap.get('change_pct')}%。",
        f"技术位：支撑 {tech.get('support')}，阻力 {tech.get('resistance')}，VWAP {tech.get('vwap')}。",
        f"风险：LLM 未调用或失败：{reason}",
        "短线观察条件：突破阻力且维持在 VWAP 上方偏强；跌破关键支撑则观察失效。",
        "综合判断：仅供研究观察，不构成投资建议。",
    ])


def first_or_na(values: Any) -> str:
    if isinstance(values, list) and values:
        return str(values[0])
    return 'N/A'
