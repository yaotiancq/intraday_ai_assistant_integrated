from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set


def build_candidates(
    core_symbols: Iterable[str],
    news_items: List[Dict[str, Any]],
    snapshots: List[Dict[str, Any]],
    min_abs_move_pct: float = 1.5,
) -> Dict[str, Any]:
    core = {s.upper() for s in core_symbols}
    news_symbols: Set[str] = set()
    for n in news_items:
        if n.get('is_error'):
            continue
        for s in n.get('related_symbols', []):
            news_symbols.add(s.upper())

    movers: Set[str] = set()
    for s in snapshots:
        try:
            pct = float(s.get('change_pct') or 0)
        except Exception:
            pct = 0
        if abs(pct) >= min_abs_move_pct:
            movers.add(str(s.get('symbol', '')).upper())

    symbols = sorted(core | news_symbols | movers)
    reasons = {s: [] for s in symbols}
    for s in core:
        if s in reasons:
            reasons[s].append('core_watchlist')
    for s in news_symbols:
        reasons.setdefault(s, []).append('news_catalyst')
    for s in movers:
        reasons.setdefault(s, []).append('premarket_mover')

    return {
        'symbols': symbols,
        'reason': reasons,
        'counts': {
            'core': len(core),
            'news': len(news_symbols),
            'movers': len(movers),
            'total': len(symbols),
        },
    }
