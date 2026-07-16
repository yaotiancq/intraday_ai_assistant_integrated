from __future__ import annotations

import re
from typing import List

from app.reporting.etf_metadata import ETF_METADATA

BANNED_PHRASES = ['稳赚', '必涨', '必跌', '保证上涨', '保证盈利', '满仓', '梭哈']
REQUIRED_SECTIONS = ['截至时间', '今日市场结论', '交易风格建议', '市场环境', '风险']


def validate_report(text: str) -> List[str]:
    errors: List[str] = []
    if not text or len(text.strip()) < 100:
        errors.append('report_too_short')
    for phrase in BANNED_PHRASES:
        if phrase in text:
            errors.append(f'banned_phrase:{phrase}')
    for section in REQUIRED_SECTIONS:
        if section not in text:
            errors.append(f'missing_section:{section}')
    return errors


def validate_rule_based_report(text: str, safe_mode: bool = False) -> List[str]:
    errors = validate_report(text)
    if '报告模式' not in text:
        errors.append('missing_section:报告模式')
    if safe_mode:
        if 'NO_TRADE' not in text:
            errors.append('safe_mode_missing_no_trade')
    else:
        if '盘前候选' not in text:
            errors.append('missing_section:盘前候选')
    if 'None' in text:
        errors.append('literal_none_rendered')
    if "{'" in text or "'}" in text or '": ' in text and text.count('{') > 0:
        errors.append('raw_python_object_rendered')
    for symbol, metadata in ETF_METADATA.items():
        if symbol in text and f'{symbol}（{metadata.display_name_zh}）' not in text:
            if re.search(rf'(?<![A-Z0-9]){re.escape(symbol)}(?![A-Z0-9（])', text):
                errors.append(f'etf_missing_chinese_name:{symbol}')
    return sorted(set(errors))
