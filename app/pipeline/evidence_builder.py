from __future__ import annotations

from typing import Any, Dict, List
from app.scoring.priority import score_candidate


def build_premarket_evidence_pack(
    context: Dict[str, Any],
    market_regime: Dict[str, Any],
    index_snapshots: List[Dict[str, Any]],
    sector_snapshots: List[Dict[str, Any]],
    candidate_symbols: List[str],
    snapshots: List[Dict[str, Any]],
    technicals: Dict[str, Dict[str, Any]],
    news_items: List[Dict[str, Any]],
    max_a_tier: int = 5,
    max_b_tier: int = 8,
) -> Dict[str, Any]:
    snapshot_by_symbol = {s.get('symbol'): s for s in snapshots}
    strong_sector_set = set(market_regime.get('strong_sectors', []))

    scored = []
    for symbol in candidate_symbols:
        tech = technicals.get(symbol, {})
        # Simple sector strength approximation: mega/semis get boost when SMH/XLK strong.
        sector_strong = False
        if symbol in {'NVDA', 'AMD', 'AVGO', 'QCOM', 'INTC', 'ARM', 'MU', 'TSM', 'ASML'}:
            sector_strong = bool({'SMH', 'SOXX'} & strong_sector_set)
        if symbol in {'MSFT', 'AAPL', 'AMZN', 'GOOGL', 'META', 'TSLA', 'CRM', 'ORCL', 'NOW', 'PLTR', 'SNOW'}:
            sector_strong = 'XLK' in strong_sector_set or 'QQQ' in strong_sector_set
        row = score_candidate(
            symbol=symbol,
            snapshot=snapshot_by_symbol.get(symbol),
            technical=tech,
            news_items=news_items,
            market_regime=market_regime,
            sector_is_strong=sector_strong,
        )
        row['snapshot'] = snapshot_by_symbol.get(symbol, {})
        row['technical'] = tech
        row['news'] = [n for n in news_items if symbol in n.get('related_symbols', [])][:5]
        scored.append(row)

    scored.sort(key=lambda x: x['priority_score'], reverse=True)
    a = [x for x in scored if x['tier'] == 'A'][:max_a_tier]
    b = [x for x in scored if x['tier'] == 'B'][:max_b_tier]
    c = [x for x in scored if x['tier'] == 'C'][:10]
    d = [x for x in scored if x['tier'] == 'D'][:10]

    return {
        'as_of': context.get('as_of'),
        'date': context.get('date'),
        'timezone': context.get('timezone'),
        'session': context.get('session'),
        'is_trading_day': context.get('is_trading_day'),
        'market_regime': market_regime,
        'index_snapshot': {s['symbol']: s for s in index_snapshots},
        'sector_snapshot': {s['symbol']: s for s in sector_snapshots},
        'news_summary': {
            'total_items': len([n for n in news_items if not n.get('is_error')]),
            'errors': [n for n in news_items if n.get('is_error')],
        },
        'candidates': {
            'A': a,
            'B': b,
            'C': c,
            'D': d,
            'all': scored,
        },
        'analysis_constraints': {
            'do_not_give_investment_advice': True,
            'condition_based_language_only': True,
            'distinguish_fact_from_rumor': True,
            'must_include_as_of_time': True,
            'must_include_risk_warning': True,
        },
    }
