from __future__ import annotations

from typing import Any, Dict, List
import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> float | None:
    if len(series.dropna()) < window:
        return None
    return float(series.rolling(window).mean().iloc[-1])


# def rsi(series: pd.Series, window: int = 14) -> float | None:
#     s = pd.to_numeric(series, errors='coerce').dropna()
#     if len(s) <= window:
#         return None
#     delta = s.diff()
#     gain = delta.where(delta > 0, 0.0).rolling(window).mean()
#     loss = (-delta.where(delta < 0, 0.0)).rolling(window).mean()
#     last_loss = loss.iloc[-1]
#     last_gain = gain.iloc[-1]
#     if pd.isna(last_gain) or pd.isna(last_loss):
#         return None
#     if last_loss == 0:
#         return 100.0 if last_gain > 0 else 50.0
#     rs = last_gain / last_loss
#     val = 100 - (100 / (1 + rs))
#     if np.isnan(val):
#         return None
#     return float(val)

def rsi(series: pd.Series, window: int = 14) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < window + 1:
        return None
    delta = s.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.iloc[:window].mean()
    avg_loss = loss.iloc[:window].mean()
    for i in range(window, len(delta)):
        avg_gain = (avg_gain * (window - 1) + gain.iloc[i]) / window
        avg_loss = (avg_loss * (window - 1) + loss.iloc[i]) / window
    if pd.isna(avg_gain) or pd.isna(avg_loss):
        return None
    if np.isclose(avg_loss, 0):
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    val = 100 - (100 / (1 + rs))
    if np.isnan(val):
        return None
    return float(val)


def atr(df: pd.DataFrame, window: int = 14) -> float | None:
    if df is None or len(df) <= window:
        return None
    high = pd.to_numeric(df['high'], errors='coerce')
    low = pd.to_numeric(df['low'], errors='coerce')
    close = pd.to_numeric(df['close'], errors='coerce')
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(window).mean().iloc[-1]
    return None if pd.isna(val) else float(val)


# def vwap(df: pd.DataFrame) -> float | None:
#     if df is None or df.empty or 'volume' not in df or 'close' not in df:
#         return None
#     price = (df['high'] + df['low'] + df['close']) / 3 if {'high', 'low', 'close'}.issubset(df.columns) else df['close']
#     vol = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
#     denom = vol.sum()
#     if denom <= 0:
#         return None
#     return float((price * vol).sum() / denom)

def vwap(df: pd.DataFrame) -> float | None:
    """
    Calculate VWAP for the rows passed in.

    Preferred formula:
        VWAP = sum(turnover) / sum(volume)

    Fallback formula:
        VWAP ≈ sum(typical_price * volume) / sum(volume)
        typical_price = (high + low + close) / 3

    Important:
        This function only calculates VWAP over the provided dataframe.
        The caller must make sure df contains the desired session/window.
    """
    if df is None or df.empty or "volume" not in df:
        return None

    data = df.copy()

    vol = pd.to_numeric(data["volume"], errors="coerce").fillna(0)
    valid_vol = vol > 0

    if not valid_vol.any():
        return None

    # More accurate when Futu provides turnover.
    if "turnover" in data.columns:
        turnover = pd.to_numeric(data["turnover"], errors="coerce")
        valid = valid_vol & turnover.notna() & (turnover > 0)
        if valid.any():
            denom = vol[valid].sum()
            if denom > 0:
                return float(turnover[valid].sum() / denom)

    # Fallback: typical-price approximation.
    if {"high", "low", "close"}.issubset(data.columns):
        high = pd.to_numeric(data["high"], errors="coerce")
        low = pd.to_numeric(data["low"], errors="coerce")
        close = pd.to_numeric(data["close"], errors="coerce")
        price = (high + low + close) / 3
    elif "close" in data.columns:
        price = pd.to_numeric(data["close"], errors="coerce")
    else:
        return None

    valid = valid_vol & price.notna()
    if not valid.any():
        return None

    denom = vol[valid].sum()
    if denom <= 0:
        return None

    return float((price[valid] * vol[valid]).sum() / denom)


def recent_high(df: pd.DataFrame, window: int = 20) -> float | None:
    if df is None or df.empty or 'high' not in df:
        return None
    return float(pd.to_numeric(df['high'], errors='coerce').tail(window).max())


def recent_low(df: pd.DataFrame, window: int = 20) -> float | None:
    if df is None or df.empty or 'low' not in df:
        return None
    return float(pd.to_numeric(df['low'], errors='coerce').tail(window).min())


def volume_ratio(df: pd.DataFrame, recent_volume: float | None = None, window: int = 20) -> float | None:
    if df is None or df.empty or 'volume' not in df:
        return None
    vols = pd.to_numeric(df['volume'], errors='coerce').dropna()
    if vols.empty:
        return None
    current = recent_volume if recent_volume is not None else float(vols.iloc[-1])
    avg = vols.tail(window).mean()
    if avg <= 0:
        return None
    return float(current / avg)


def build_technical_levels(symbol: str, daily_df: pd.DataFrame, intraday_df: pd.DataFrame | None = None, current_price: float | None = None, current_volume: float | None = None) -> Dict[str, Any]:
    daily = daily_df.copy() if daily_df is not None else pd.DataFrame()
    if not daily.empty:
        daily['close'] = pd.to_numeric(daily['close'], errors='coerce')
        daily['high'] = pd.to_numeric(daily['high'], errors='coerce')
        daily['low'] = pd.to_numeric(daily['low'], errors='coerce')
        daily['volume'] = pd.to_numeric(daily['volume'], errors='coerce')

    close = daily['close'] if 'close' in daily else pd.Series(dtype=float)
    last_close = float(close.iloc[-1]) if len(close.dropna()) else current_price
    rh20 = recent_high(daily, 20)
    rl20 = recent_low(daily, 20)
    rh60 = recent_high(daily, 60)
    rl60 = recent_low(daily, 60)
    prev_high = float(daily['high'].iloc[-2]) if len(daily) >= 2 and pd.notna(daily['high'].iloc[-2]) else None
    prev_low = float(daily['low'].iloc[-2]) if len(daily) >= 2 and pd.notna(daily['low'].iloc[-2]) else None
    prev_close = float(daily['close'].iloc[-2]) if len(daily) >= 2 and pd.notna(daily['close'].iloc[-2]) else None

    intraday_vwap = vwap(intraday_df) if intraday_df is not None else None
    pre_high = recent_high(intraday_df, len(intraday_df)) if intraday_df is not None and not intraday_df.empty else None
    pre_low = recent_low(intraday_df, len(intraday_df)) if intraday_df is not None and not intraday_df.empty else None

    supports = _unique_sorted([x for x in [pre_low, prev_low, rl20, rl60, intraday_vwap] if x is not None], reverse=True)
    resistances = _unique_sorted([x for x in [pre_high, prev_high, rh20, rh60] if x is not None], reverse=False)

    return {
        'symbol': symbol.upper(),
        'ma5': sma(close, 5),
        'ma20': sma(close, 20),
        'ma50': sma(close, 50),
        'rsi14': rsi(close, 14),
        'atr14': atr(daily, 14),
        'vwap': intraday_vwap,
        'prev_close': prev_close,
        'prev_high': prev_high,
        'prev_low': prev_low,
        'recent_high_20d': rh20,
        'recent_low_20d': rl20,
        'recent_high_60d': rh60,
        'recent_low_60d': rl60,
        'volume_ratio': volume_ratio(daily, current_volume, 20),
        'support': supports[:4],
        'resistance': resistances[:4],
        'position': classify_position(current_price or last_close, rh20, rl20),
    }


def classify_position(price: float | None, high: float | None, low: float | None) -> str:
    if price is None or high is None or low is None or high <= low:
        return 'unknown'
    pct = (price - low) / (high - low)
    if pct >= 0.8:
        return 'near_range_high'
    if pct <= 0.2:
        return 'near_range_low'
    return 'middle_of_range'


def _unique_sorted(values: List[float], reverse: bool) -> List[float]:
    rounded = sorted({round(float(v), 2) for v in values if v is not None and np.isfinite(v)}, reverse=reverse)
    return rounded
