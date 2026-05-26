from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional
import random
import pandas as pd


class FutuUnavailableError(RuntimeError):
    pass


def _import_futu_module():
    """Import futu or moomoo SDK lazily.

    Different accounts/environments use either `futu` or `moomoo` package.
    We support both and expose the module object.
    """
    try:
        import futu  # type: ignore
        return futu
    except Exception:
        try:
            import moomoo  # type: ignore
            return moomoo
        except Exception as exc:
            raise FutuUnavailableError(
                'Neither futu nor moomoo Python SDK is installed. Install futu-api or moomoo-api.'
            ) from exc


def normalize_us_symbol(symbol: str, prefix: str = 'US') -> str:
    symbol = symbol.strip().upper()
    if '.' in symbol:
        return symbol
    return f'{prefix}.{symbol}'


def strip_prefix(code: str) -> str:
    return code.split('.')[-1].upper()


@dataclass
class FutuQuoteClient:
    host: str = '127.0.0.1'
    port: int = 11111
    market_prefix: str = 'US'
    extended_time: bool = True

    def __post_init__(self) -> None:
        self.futu = _import_futu_module()
        self.ctx = self.futu.OpenQuoteContext(host=self.host, port=self.port)

    def close(self) -> None:
        try:
            self.ctx.close()
        except Exception:
            pass

    def __enter__(self) -> 'FutuQuoteClient':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_market_snapshot(self, symbols: Iterable[str]) -> List[Dict[str, Any]]:
        codes = [normalize_us_symbol(s, self.market_prefix) for s in symbols]
        if not codes:
            return []
        ret, data = self.ctx.get_market_snapshot(codes)
        if ret != self.futu.RET_OK:
            raise RuntimeError(f'get_market_snapshot failed: {data}')
        rows = data.to_dict(orient='records') if hasattr(data, 'to_dict') else []
        return [self._normalize_snapshot_row(row) for row in rows]

    def get_realtime_kline(self, symbol: str, ktype: str = 'K_1M', num: int = 240) -> pd.DataFrame:
        # Futu 文档要求 get_cur_kline 之前先 subscribe；num 上限为 1000。
        code = normalize_us_symbol(symbol, self.market_prefix)
        subtype = self._subtype(ktype)
        kltype = self._kltype(ktype)
        kwargs = {'subscribe_push': False, 'extended_time': self.extended_time}
        session = self._session()
        if session is not None:
            kwargs['session'] = session
        ret, msg = self.ctx.subscribe([code], [subtype], **kwargs)
        if ret != self.futu.RET_OK:
            raise RuntimeError(f'subscribe failed for {code}: {msg}')
        ret, data = self.ctx.get_cur_kline(code, min(num, 1000), kltype)
        if ret != self.futu.RET_OK:
            raise RuntimeError(f'get_cur_kline failed for {code}: {data}')
        return self._normalize_kline_df(data, symbol)

    def request_history_kline(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        ktype: str = 'K_DAY',
        max_count: int = 500,
    ) -> pd.DataFrame:
        code = normalize_us_symbol(symbol, self.market_prefix)
        subtype = self._kltype(ktype)
        kwargs = dict(code=code, start=start, end=end, ktype=subtype, max_count=max_count)
        session = self._session()
        if session is not None:
            kwargs['session'] = session
        ret, data, page_req_key = self.ctx.request_history_kline(**kwargs)
        if ret != self.futu.RET_OK:
            raise RuntimeError(f'request_history_kline failed for {code}: {data}')
        frames = [data]
        while page_req_key is not None:
            kwargs['page_req_key'] = page_req_key
            ret, data, page_req_key = self.ctx.request_history_kline(**kwargs)
            if ret != self.futu.RET_OK:
                raise RuntimeError(f'request_history_kline page failed for {code}: {data}')
            frames.append(data)
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return self._normalize_kline_df(df, symbol)

    def _subtype(self, name: str) -> Any:
        name = name.upper()
        return getattr(self.futu.SubType, name)

    def _kltype(self, name: str) -> Any:
        name = name.upper()
        if hasattr(self.futu, 'KLType') and hasattr(self.futu.KLType, name):
            return getattr(self.futu.KLType, name)
        return getattr(self.futu.SubType, name)

    def _session(self) -> Any:
        if not hasattr(self.futu, 'Session'):
            return None
        session_name = 'ALL' if self.extended_time else 'RTH'
        if hasattr(self.futu.Session, session_name):
            return getattr(self.futu.Session, session_name)
        return None

    @staticmethod
    def _first(row: Dict[str, Any], candidates: List[str], default: Any = None) -> Any:
        for c in candidates:
            if c in row and pd.notna(row[c]) and str(row[c]) != 'N/A':
                return row[c]
        return default

    def _normalize_snapshot_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        code = self._first(row, ['code', 'stock_code'], '')
        last = _to_float(self._first(row, ['last_price', 'cur_price', 'price'], None))
        prev_close = _to_float(self._first(row, ['prev_close_price', 'last_close_price', 'prev_close'], None))
        pre_price = _to_float(self._first(row, ['pre_price'], None))
        after_price = _to_float(self._first(row, ['after_price'], None))
        overnight_price = _to_float(self._first(row, ['overnight_price'], None))
        effective_price = pre_price or after_price or overnight_price or last
        change_pct = _to_float(self._first(row, ['change_rate', 'change_pct', 'pre_change_rate', 'after_change_rate', 'overnight_change_rate'], None))
        if change_pct is None and last is not None and prev_close:
            change_pct = (last / prev_close - 1) * 100
        effective_change_pct = None
        if effective_price is not None and prev_close:
            effective_change_pct = (effective_price / prev_close - 1) * 100
        volume = self._first(row, ['volume', 'vol'], None)
        turnover = self._first(row, ['turnover', 'turnover_value'], None)
        high = self._first(row, ['high_price', 'high'], None)
        low = self._first(row, ['low_price', 'low'], None)
        open_price = self._first(row, ['open_price', 'open'], None)
        return {
            'symbol': strip_prefix(str(code)) if code else '',
            'code': code,
            'name': self._first(row, ['name', 'stock_name'], ''),
            'update_time': self._first(row, ['update_time', 'data_time'], ''),
            'last': last,
            'effective_price': effective_price,
            'prev_close': prev_close,
            'open': _to_float(open_price),
            'high': _to_float(high),
            'low': _to_float(low),
            'change_pct': _round(change_pct),
            'effective_change_pct': _round(effective_change_pct),
            'volume': _to_float(volume),
            'turnover': _to_float(turnover),
            'volume_ratio': _to_float(self._first(row, ['volume_ratio'], None)),
            'turnover_rate': _to_float(self._first(row, ['turnover_rate'], None)),
            'bid_price': _to_float(self._first(row, ['bid_price'], None)),
            'ask_price': _to_float(self._first(row, ['ask_price'], None)),
            'pre_price': pre_price,
            'pre_high_price': _to_float(self._first(row, ['pre_high_price'], None)),
            'pre_low_price': _to_float(self._first(row, ['pre_low_price'], None)),
            'pre_volume': _to_float(self._first(row, ['pre_volume'], None)),
            'pre_change_rate': _to_float(self._first(row, ['pre_change_rate'], None)),
            'after_price': after_price,
            'after_change_rate': _to_float(self._first(row, ['after_change_rate'], None)),
            'overnight_price': overnight_price,
            'overnight_change_rate': _to_float(self._first(row, ['overnight_change_rate'], None)),
            'highest52weeks_price': _to_float(self._first(row, ['highest52weeks_price'], None)),
            'lowest52weeks_price': _to_float(self._first(row, ['lowest52weeks_price'], None)),
            'total_market_val': _to_float(self._first(row, ['total_market_val'], None)),
            'raw': _json_safe(row),
        }

    def _normalize_kline_df(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame(columns=['time_key', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
        out = df.copy()
        rename_map = {
            'time_key': 'time_key',
            'open': 'open',
            'open_price': 'open',
            'high': 'high',
            'high_price': 'high',
            'low': 'low',
            'low_price': 'low',
            'close': 'close',
            'close_price': 'close',
            'volume': 'volume',
            'turnover': 'turnover',
        }
        out = out.rename(columns={k: v for k, v in rename_map.items() if k in out.columns})
        for col in ['open', 'high', 'low', 'close', 'volume', 'turnover']:
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors='coerce')
        if 'time_key' not in out.columns:
            out['time_key'] = pd.NaT
        out['symbol'] = symbol.upper()
        keep = ['symbol', 'time_key', 'open', 'high', 'low', 'close', 'volume', 'turnover']
        return out[[c for c in keep if c in out.columns]]


class MockQuoteClient:
    """Deterministic-enough demo client for tests and local dry-runs."""

    def __init__(self, seed: int = 42) -> None:
        self.rng = random.Random(seed)

    def close(self) -> None:
        pass

    def __enter__(self) -> 'MockQuoteClient':
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_market_snapshot(self, symbols: Iterable[str]) -> List[Dict[str, Any]]:
        base_prices = {
            'SPY': 520.0, 'QQQ': 445.0, 'IWM': 205.0, 'DIA': 390.0,
            'SMH': 230.0, 'XLK': 220.0, 'XLE': 92.0, 'XLF': 42.0,
            'NVDA': 126.0, 'AMD': 165.0, 'MU': 128.0, 'MSFT': 430.0,
            'AAPL': 195.0, 'TSLA': 185.0, 'SATS': 127.0,
        }
        out = []
        for s in symbols:
            sym = strip_prefix(str(s))
            base = base_prices.get(sym, 100 + self.rng.random() * 50)
            pct = self.rng.uniform(-1.8, 2.8)
            last = base * (1 + pct / 100)
            out.append({
                'symbol': sym,
                'code': f'US.{sym}',
                'last': round(last, 2),
                'prev_close': round(base, 2),
                'open': round(base * (1 + self.rng.uniform(-0.01, 0.01)), 2),
                'high': round(last * (1 + self.rng.uniform(0.001, 0.015)), 2),
                'low': round(last * (1 - self.rng.uniform(0.001, 0.015)), 2),
                'change_pct': round(pct, 2),
                'volume': int(self.rng.uniform(200_000, 9_000_000)),
                'turnover': round(last * self.rng.uniform(200_000, 9_000_000), 2),
                'raw': {},
            })
        return out

    def get_realtime_kline(self, symbol: str, ktype: str = 'K_1M', num: int = 240) -> pd.DataFrame:
        return self._fake_kline(symbol, num=num, freq='1min')

    def request_history_kline(
        self,
        symbol: str,
        start: str | None = None,
        end: str | None = None,
        ktype: str = 'K_DAY',
        max_count: int = 500,
    ) -> pd.DataFrame:
        return self._fake_kline(symbol, num=90, freq='1D')

    def _fake_kline(self, symbol: str, num: int, freq: str) -> pd.DataFrame:
        end = datetime.now().replace(second=0, microsecond=0)
        idx = pd.date_range(end=end, periods=num, freq=freq)
        price = 100 + self.rng.random() * 80
        rows = []
        for ts in idx:
            move = self.rng.uniform(-0.8, 0.8)
            open_p = price
            close_p = max(1, price + move)
            high = max(open_p, close_p) + self.rng.uniform(0, 0.6)
            low = min(open_p, close_p) - self.rng.uniform(0, 0.6)
            vol = int(self.rng.uniform(10_000, 900_000))
            rows.append({
                'symbol': symbol.upper(),
                'time_key': ts.strftime('%Y-%m-%d %H:%M:%S'),
                'open': round(open_p, 2),
                'high': round(high, 2),
                'low': round(low, 2),
                'close': round(close_p, 2),
                'volume': vol,
                'turnover': round(vol * close_p, 2),
            })
            price = close_p
        return pd.DataFrame(rows)


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _round(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(float(value), digits)


def _json_safe(row: Dict[str, Any]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for k, v in row.items():
        if pd.isna(v):
            safe[k] = None
        elif hasattr(v, 'item'):
            safe[k] = v.item()
        else:
            safe[k] = v
    return safe
