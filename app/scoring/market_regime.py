from __future__ import annotations

from typing import Any, Dict, List


def compute_market_regime(index_snapshots: List[Dict[str, Any]], sector_snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_symbol = {x.get('symbol'): x for x in index_snapshots + sector_snapshots}

    spy = _pct(by_symbol.get('SPY'))
    qqq = _pct(by_symbol.get('QQQ'))
    iwm = _pct(by_symbol.get('IWM'))
    smh = _pct(by_symbol.get('SMH')) or _pct(by_symbol.get('SOXX'))
    xlk = _pct(by_symbol.get('XLK'))

    score = 0.0
    score += _scale(spy, 1.2) * 18
    score += _scale(qqq, 1.4) * 20
    score += _scale(iwm, 1.2) * 10
    score += _scale(smh, 1.8) * 18
    score += _scale(xlk, 1.4) * 12

    # Broad sector breadth: how many tracked ETFs are green.
    sector_pcts = [_pct(s) for s in sector_snapshots if _pct(s) is not None]
    if sector_pcts:
        breadth = sum(1 for x in sector_pcts if x > 0) / len(sector_pcts)
        score += (breadth - 0.5) * 24

    score = max(-100, min(100, round(score, 1)))
    label = label_regime(score)
    strong = sorted([s['symbol'] for s in sector_snapshots if (_pct(s) or 0) >= 0.6])[:6]
    weak = sorted([s['symbol'] for s in sector_snapshots if (_pct(s) or 0) <= -0.6])[:6]

    return {
        'score': score,
        'label': label,
        'summary': summarize(score, strong, weak),
        'index_changes': {k: _pct(v) for k, v in by_symbol.items() if k in {'SPY', 'QQQ', 'IWM', 'DIA'}},
        'strong_sectors': strong,
        'weak_sectors': weak,
    }


def label_regime(score: float) -> str:
    if score >= 50:
        return 'strong_risk_on'
    if score >= 20:
        return 'mild_risk_on'
    if score > -20:
        return 'neutral_choppy'
    if score > -50:
        return 'mild_risk_off'
    return 'strong_risk_off'


def summarize(score: float, strong: List[str], weak: List[str]) -> str:
    if score >= 20:
        tone = '市场风险偏好偏强'
    elif score <= -20:
        tone = '市场风险偏好偏弱'
    else:
        tone = '市场偏震荡，方向确认度一般'
    s = f'{tone}；强势板块：{", ".join(strong) if strong else "暂无明显"}；弱势板块：{", ".join(weak) if weak else "暂无明显"}。'
    return s


def _pct(snapshot: Dict[str, Any] | None) -> float | None:
    if not snapshot:
        return None
    val = snapshot.get('change_pct')
    if val is None:
        last, prev = snapshot.get('last'), snapshot.get('prev_close')
        if last is not None and prev:
            val = (last / prev - 1) * 100
    try:
        return None if val is None else float(val)
    except Exception:
        return None


def _scale(value: float | None, cap_abs: float) -> float:
    if value is None:
        return 0.0
    return max(-1.0, min(1.0, value / cap_abs))
