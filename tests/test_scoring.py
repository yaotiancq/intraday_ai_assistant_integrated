from app.scoring.market_regime import compute_market_regime, label_regime
from app.scoring.priority import score_candidate, tier_from_score


def test_label_regime():
    assert label_regime(60) == 'strong_risk_on'
    assert label_regime(0) == 'neutral_choppy'
    assert label_regime(-70) == 'strong_risk_off'


def test_market_regime_positive():
    idx = [{'symbol': 'SPY', 'change_pct': 0.5}, {'symbol': 'QQQ', 'change_pct': 0.8}, {'symbol': 'IWM', 'change_pct': 0.2}]
    sectors = [{'symbol': 'SMH', 'change_pct': 1.2}, {'symbol': 'XLK', 'change_pct': 0.7}]
    out = compute_market_regime(idx, sectors)
    assert out['score'] > 0
    assert out['label'] in {'mild_risk_on', 'strong_risk_on'}


def test_tier_from_score():
    assert tier_from_score(85) == 'A'
    assert tier_from_score(70) == 'B'
    assert tier_from_score(55) == 'C'
    assert tier_from_score(20) == 'D'


def test_score_candidate_news_volume_breakout():
    out = score_candidate(
        symbol='NVDA',
        snapshot={'symbol': 'NVDA', 'change_pct': 3.2, 'volume': 5_000_000},
        technical={'volume_ratio': 3.1, 'position': 'near_range_high'},
        news_items=[{'related_symbols': ['NVDA'], 'news_score': 25, 'event_type': 'product'}],
        market_regime={'score': 35},
        sector_is_strong=True,
    )
    assert out['priority_score'] >= 80
    assert out['tier'] == 'A'
