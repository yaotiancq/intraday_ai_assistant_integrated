from __future__ import annotations

from typing import List

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
