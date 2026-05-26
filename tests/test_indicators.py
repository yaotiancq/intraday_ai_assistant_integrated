import pandas as pd
from app.indicators.technical_indicators import sma, rsi, atr, vwap, build_technical_levels


def sample_df(n=30):
    rows = []
    for i in range(n):
        rows.append({
            'open': 100 + i,
            'high': 101 + i,
            'low': 99 + i,
            'close': 100.5 + i,
            'volume': 1000 + i * 10,
            'turnover': (100.5 + i) * (1000 + i * 10),
        })
    return pd.DataFrame(rows)


def test_sma():
    df = sample_df(10)
    assert sma(df['close'], 5) == df['close'].tail(5).mean()


def test_rsi_returns_number():
    df = sample_df(30)
    val = rsi(df['close'], 14)
    assert val is not None
    assert 0 <= val <= 100


def test_atr_returns_number():
    df = sample_df(30)
    val = atr(df, 14)
    assert val is not None
    assert val > 0


def test_vwap():
    df = sample_df(5)
    val = vwap(df)
    assert val is not None
    assert df['low'].min() <= val <= df['high'].max()


def test_build_technical_levels():
    df = sample_df(70)
    levels = build_technical_levels('NVDA', df, df.tail(20), current_price=165, current_volume=3000)
    assert levels['symbol'] == 'NVDA'
    assert 'support' in levels
    assert 'resistance' in levels
    assert levels['ma20'] is not None
