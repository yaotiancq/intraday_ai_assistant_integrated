from __future__ import annotations

from typing import Any, Dict, List


def score_candidate(
    symbol: str,
    snapshot: Dict[str, Any] | None,
    technical: Dict[str, Any] | None,
    news_items: List[Dict[str, Any]],
    market_regime: Dict[str, Any],
    sector_is_strong: bool = False,
) -> Dict[str, Any]:
    score = 0
    reasons: List[str] = []
    risk_flags: List[str] = []

    # News catalyst.
    symbol_news = [n for n in news_items if symbol.upper() in n.get('related_symbols', [])]
    if symbol_news:
        news_score = min(30, max(int(n.get('news_score', 0)) for n in symbol_news))
        score += news_score
        reasons.append(f'news_catalyst_{news_score}')
        if any(n.get('event_type') == 'rumor' for n in symbol_news):
            risk_flags.append('rumor_driven')
            score -= 6

    # Price move.
    change_pct = _float(snapshot.get('change_pct') if snapshot else None)
    if change_pct is not None:
        abs_chg = abs(change_pct)
        if abs_chg >= 3:
            score += 15
            reasons.append('large_premarket_move')
        elif abs_chg >= 1:
            score += 8
            reasons.append('moderate_premarket_move')
        if change_pct > 4:
            risk_flags.append('gap_up_chasing_risk')
        if change_pct < -4:
            risk_flags.append('gap_down_high_risk')

    # Volume and liquidity.
    vol_ratio = _float((technical or {}).get('volume_ratio'))
    if vol_ratio is not None:
        if vol_ratio >= 3:
            score += 15
            reasons.append('volume_spike')
        elif vol_ratio >= 1.5:
            score += 8
            reasons.append('above_average_volume')
    volume = _float(snapshot.get('volume') if snapshot else None)
    if volume is not None and volume < 100_000:
        score -= 15
        risk_flags.append('low_liquidity')

    # Technical setup.
    position = (technical or {}).get('position')
    if position == 'near_range_high':
        score += 12
        reasons.append('near_breakout_zone')
    elif position == 'near_range_low':
        score += 5
        risk_flags.append('near_range_low_reversal_needed')
    else:
        score += 3

    if sector_is_strong:
        score += 10
        reasons.append('sector_strength_confirmation')

    # Market fit.
    market_score = _float(market_regime.get('score')) or 0
    if market_score >= 20:
        score += 8
        reasons.append('market_regime_supportive')
    elif market_score <= -20:
        score -= 8
        risk_flags.append('market_regime_headwind')

    score = max(0, min(100, int(round(score))))
    tier = tier_from_score(score)
    return {
        'symbol': symbol.upper(),
        'priority_score': score,
        'tier': tier,
        'reasons': sorted(set(reasons)),
        'risk_flags': sorted(set(risk_flags)),
        'news_count': len(symbol_news),
    }


def tier_from_score(score: int) -> str:
    if score >= 80:
        return 'A'
    if score >= 65:
        return 'B'
    if score >= 50:
        return 'C'
    return 'D'


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None
