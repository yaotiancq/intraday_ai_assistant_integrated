from __future__ import annotations

from abc import ABC, abstractmethod
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.data_sources.futu_client import FutuQuoteClient
from app.data_sources.news_rss_client import NewsRSSClient


MARKET_TIMEZONE = "America/New_York"
_SYMBOL_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_TIMESTAMP_KEYS = ("timestamp", "time_key", "datetime", "as_of", "update_time", "data_time")


class MarketDataError(RuntimeError):
    """Base error raised by deterministic market-data adapters."""


class SymbolNotAllowedError(MarketDataError, ValueError):
    """Raised before a data source is asked for an unconfigured symbol."""


class ReplayDataError(MarketDataError, ValueError):
    """Raised when a replay payload is malformed or violates the allowlist."""


def normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if symbol.startswith("US."):
        symbol = symbol.split(".", 1)[1]
    if not symbol or symbol[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" or any(ch not in _SYMBOL_CHARS for ch in symbol):
        raise ValueError(f"invalid symbol: {value!r}")
    return symbol


def _coerce_allowed_symbols(values: Iterable[str] | Any | None) -> frozenset[str]:
    if values is None:
        # Import lazily to avoid a module cycle: fixed_universe imports the
        # abstract data-source type only for annotations.
        from app.universe.fixed_universe import build_fixed_universe

        values = build_fixed_universe("2000-01-03").allowed_symbols
    elif hasattr(values, "allowed_symbols"):
        values = values.allowed_symbols
    normalized = frozenset(normalize_symbol(value) for value in values)
    if not normalized:
        raise ValueError("allowed_symbols must not be empty")
    return normalized


class MarketDataSource(ABC):
    """Point-in-time, allowlisted data used by every strategy stage.

    Records are deliberately JSON-serializable so the same values can be
    persisted and replayed without translating a provider-specific object.
    Intraday bar timestamps denote bar *start* unless ``bar_end`` is present.
    Only completed bars at or before the supplied cutoff are returned.
    """

    source_name = "abstract"

    def __init__(self, allowed_symbols: Iterable[str] | Any | None) -> None:
        self.allowed_symbols = _coerce_allowed_symbols(allowed_symbols)

    def _require_symbols(self, symbols: Iterable[str]) -> tuple[str, ...]:
        normalized = tuple(normalize_symbol(symbol) for symbol in symbols)
        unknown = sorted(set(normalized) - self.allowed_symbols)
        if unknown:
            raise SymbolNotAllowedError("symbols outside configured universe: " + ",".join(unknown))
        return normalized

    def _require_symbol(self, symbol: str) -> str:
        return self._require_symbols((symbol,))[0]

    @abstractmethod
    def snapshots(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def daily(
        self,
        symbol: str,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        as_of: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def premarket(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def minute(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def news(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def review(self, symbols: Iterable[str], as_of: datetime | str) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    # Compatibility names make the boundary easy to consume from both the old
    # quote pipeline and the new staged pipeline.
    def get_snapshots(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        return self.snapshots(symbols, as_of)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        as_of: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        return self.daily(symbol, start_date=start_date, end_date=end_date, as_of=as_of)

    def get_premarket_bars(
        self, symbol: str, trade_date: date | str, cutoff: datetime | str
    ) -> list[dict[str, Any]]:
        return self.premarket(symbol, trade_date, cutoff)

    def get_minute_bars(
        self, symbol: str, trade_date: date | str, cutoff: datetime | str
    ) -> list[dict[str, Any]]:
        return self.minute(symbol, trade_date, cutoff)

    def get_news(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        return self.news(symbols, as_of)

    def get_review_metrics(
        self, symbols: Iterable[str], as_of: datetime | str
    ) -> dict[str, dict[str, Any]]:
        return self.review(symbols, as_of)


class ReplayMarketDataSource(MarketDataSource):
    """Read an immutable point-in-time market-data fixture from JSON."""

    source_name = "replay"

    def __init__(
        self,
        payload_or_path: Mapping[str, Any] | str | Path,
        allowed_symbols: Iterable[str] | Any | None,
        timezone_name: str = MARKET_TIMEZONE,
    ) -> None:
        super().__init__(allowed_symbols)
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)
        if isinstance(payload_or_path, Mapping):
            payload = deepcopy(dict(payload_or_path))
            self.input_path: Path | None = None
        else:
            self.input_path = Path(payload_or_path)
            try:
                payload = json.loads(self.input_path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ReplayDataError(f"replay input not found: {self.input_path}") from exc
            except json.JSONDecodeError as exc:
                raise ReplayDataError(f"invalid replay JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ReplayDataError("replay root must be an object")
        self.payload = deepcopy(dict(payload))
        market_data = self.payload.get("market_data", self.payload)
        if not isinstance(market_data, Mapping):
            raise ReplayDataError("market_data must be an object")
        self.data = deepcopy(dict(market_data))
        self._validate_fixture_allowlist()
        self.synthetic_fallback = (
            DeterministicMarketDataSource(self.allowed_symbols, timezone_name=timezone_name)
            if bool(self.payload.get("synthetic_defaults"))
            else None
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        allowed_symbols: Iterable[str] | Any | None,
        timezone_name: str = MARKET_TIMEZONE,
    ) -> "ReplayMarketDataSource":
        return cls(path, allowed_symbols=allowed_symbols, timezone_name=timezone_name)

    def snapshots(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        if self.synthetic_fallback is not None:
            return self.synthetic_fallback.snapshots(symbols, as_of)
        requested = self._require_symbols(symbols)
        cutoff = _as_datetime(as_of, self.timezone)
        rows: list[dict[str, Any]] = []
        for symbol in requested:
            candidates = self._symbol_records(("snapshots", "snapshot"), symbol)
            valid = _filter_records(candidates, cutoff, self.timezone, require_timestamp=True)
            if valid:
                rows.append(_canonical_record(valid[-1], symbol, self.timezone))
        return rows

    def daily(
        self,
        symbol: str,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        as_of: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        if self.synthetic_fallback is not None:
            return self.synthetic_fallback.daily(symbol, start_date=start_date, end_date=end_date, as_of=as_of)
        normalized = self._require_symbol(symbol)
        cutoff = _resolve_daily_cutoff(as_of, end_date, self.timezone)
        start = _as_date(start_date) if start_date is not None else None
        end = _as_date(end_date) if end_date is not None else cutoff.date()
        rows = _filter_records(
            self._symbol_records(("daily_bars", "daily"), normalized),
            cutoff,
            self.timezone,
            daily=True,
            require_timestamp=True,
        )
        return [
            _canonical_record(row, normalized, self.timezone, daily=True)
            for row in rows
            if (start is None or _record_datetime(row, self.timezone, daily=True).date() >= start)
            and _record_datetime(row, self.timezone, daily=True).date() <= end
        ]

    def premarket(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        if self.synthetic_fallback is not None:
            return self.synthetic_fallback.premarket(symbol, trade_date, cutoff)
        normalized = self._require_symbol(symbol)
        target_date = _as_date(trade_date)
        cutoff_dt = _as_datetime(cutoff, self.timezone)
        rows = _filter_completed_bars(
            self._symbol_records(("premarket_bars", "premarket"), normalized), cutoff_dt, self.timezone
        )
        return [
            _canonical_record(row, normalized, self.timezone)
            for row in rows
            if _record_datetime(row, self.timezone).date() == target_date
            and time(4, 0) <= _record_datetime(row, self.timezone).timetz().replace(tzinfo=None) < time(9, 30)
        ]

    def minute(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        if self.synthetic_fallback is not None:
            return self.synthetic_fallback.minute(symbol, trade_date, cutoff)
        normalized = self._require_symbol(symbol)
        target_date = _as_date(trade_date)
        cutoff_dt = _as_datetime(cutoff, self.timezone)
        rows = _filter_completed_bars(
            self._symbol_records(("minute_bars", "minutes", "minute"), normalized), cutoff_dt, self.timezone
        )
        return [
            _canonical_record(row, normalized, self.timezone)
            for row in rows
            if _record_datetime(row, self.timezone).date() == target_date
            and time(9, 30) <= _record_datetime(row, self.timezone).timetz().replace(tzinfo=None) < time(16, 0)
        ]

    def news(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        if self.synthetic_fallback is not None:
            return self.synthetic_fallback.news(symbols, as_of)
        requested = frozenset(self._require_symbols(symbols))
        cutoff = _as_datetime(as_of, self.timezone)
        values = self.data.get("news", [])
        rows: list[Mapping[str, Any]] = []
        if isinstance(values, list):
            rows.extend(item for item in values if isinstance(item, Mapping))
        elif isinstance(values, Mapping):
            for symbol in requested:
                item = values.get(symbol) or values.get(f"US.{symbol}") or []
                rows.extend(_as_record_list(item))
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            published = _news_datetime(row, self.timezone)
            related = frozenset(_related_symbols(row))
            if published is None or published > cutoff or not (related & requested):
                continue
            key = str(row.get("url") or row.get("id") or json.dumps(dict(row), sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            value = deepcopy(dict(row))
            value["related_symbols"] = sorted(related)
            value["published_at"] = published.isoformat()
            selected.append(value)
        return sorted(selected, key=lambda item: (item["published_at"], str(item.get("url", ""))))

    def review(self, symbols: Iterable[str], as_of: datetime | str) -> dict[str, dict[str, Any]]:
        if self.synthetic_fallback is not None:
            return self.synthetic_fallback.review(symbols, as_of)
        requested = self._require_symbols(symbols)
        cutoff = _as_datetime(as_of, self.timezone)
        section = self.data.get("review_metrics", self.data.get("review", {}))
        result: dict[str, dict[str, Any]] = {}
        for symbol in requested:
            direct: Mapping[str, Any] | None = None
            if isinstance(section, Mapping):
                value = section.get(symbol) or section.get(f"US.{symbol}")
                if isinstance(value, Mapping):
                    direct = value
            if direct is not None:
                result[symbol] = deepcopy(dict(direct))
            else:
                result[symbol] = _derive_review_metrics(self, symbol, cutoff)
        return result

    def _symbol_records(self, names: Sequence[str], symbol: str) -> list[Mapping[str, Any]]:
        nested = self.data.get("symbols")
        if isinstance(nested, Mapping):
            symbol_value = nested.get(symbol) or nested.get(f"US.{symbol}")
            if isinstance(symbol_value, Mapping):
                for name in names:
                    if name in symbol_value:
                        return _as_record_list(symbol_value[name])
        for name in names:
            section = self.data.get(name)
            if isinstance(section, Mapping):
                if _row_symbol(section) == symbol:
                    return [section]
                value = section.get(symbol) or section.get(f"US.{symbol}")
                if value is not None:
                    return _as_record_list(value)
            elif isinstance(section, list):
                return [row for row in section if isinstance(row, Mapping) and _row_symbol(row) == symbol]
        return []

    def _validate_fixture_allowlist(self) -> None:
        found: set[str] = set()
        symbol_sections = (
            "snapshots", "snapshot", "daily_bars", "daily", "premarket_bars", "premarket",
            "minute_bars", "minutes", "minute", "review_metrics", "review",
        )
        nested = self.data.get("symbols")
        if isinstance(nested, Mapping):
            found.update(_safe_symbol(key) for key in nested if _safe_symbol(key))
        for name in symbol_sections:
            section = self.data.get(name)
            if isinstance(section, Mapping):
                if _row_symbol(section):
                    found.add(_row_symbol(section))
                else:
                    found.update(_safe_symbol(key) for key in section if _safe_symbol(key))
                    for value in section.values():
                        found.update(_symbols_in_records(value))
            elif isinstance(section, list):
                found.update(_symbols_in_records(section))
        news = self.data.get("news")
        if isinstance(news, Mapping):
            found.update(_safe_symbol(key) for key in news if _safe_symbol(key))
            for value in news.values():
                found.update(_symbols_in_records(value, include_related=True))
        elif isinstance(news, list):
            found.update(_symbols_in_records(news, include_related=True))
        outside = sorted(symbol for symbol in found if symbol not in self.allowed_symbols)
        if outside:
            raise ReplayDataError("replay contains symbols outside configured universe: " + ",".join(outside))


class DeterministicMarketDataSource(MarketDataSource):
    """Synthetic point-in-time data keyed only by date, symbol, and timestamp."""

    source_name = "deterministic_mock"

    def __init__(
        self,
        allowed_symbols: Iterable[str] | Any | None = None,
        timezone_name: str = MARKET_TIMEZONE,
    ) -> None:
        super().__init__(allowed_symbols)
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)

    def snapshots(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        requested = self._require_symbols(symbols)
        cutoff = _as_datetime(as_of, self.timezone)
        rows: list[dict[str, Any]] = []
        for symbol in requested:
            prev_close = self._close_for_date(symbol, _previous_weekday(cutoff.date()))
            session_time = cutoff.timetz().replace(tzinfo=None)
            if time(4, 0) <= session_time < time(9, 30):
                bars = self.premarket(symbol, cutoff.date(), cutoff)
            elif time(9, 30) <= session_time < time(16, 1):
                bars = self.minute(symbol, cutoff.date(), cutoff)
            else:
                bars = []
            last = float(bars[-1]["close"]) if bars else self._close_for_date(symbol, cutoff.date())
            high = max((float(bar["high"]) for bar in bars), default=last)
            low = min((float(bar["low"]) for bar in bars), default=last)
            total_volume = sum(float(bar.get("volume", 0.0)) for bar in bars)
            spread_bps = 4.0 + self._unit("spread", symbol, cutoff.date()) * 12.0
            half_spread = last * spread_bps / 20_000.0
            average_dollar_volume = 90_000_000.0 + self._unit("adv", symbol, cutoff.date()) * 1_900_000_000.0
            rows.append({
                "symbol": symbol,
                "code": f"US.{symbol}",
                "timestamp": cutoff.isoformat(),
                "update_time": cutoff.isoformat(),
                "last": round(last, 4),
                "effective_price": round(last, 4),
                "prev_close": round(prev_close, 4),
                "open": round(float(bars[0]["open"]) if bars else last, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "change_pct": round((last / prev_close - 1.0) * 100.0, 6),
                "volume": round(total_volume, 2),
                "turnover": round(total_volume * last, 2),
                "bid_price": round(last - half_spread, 4),
                "ask_price": round(last + half_spread, 4),
                "spread_bps": round(spread_bps, 4),
                "average_daily_dollar_volume": round(average_dollar_volume, 2),
                "pre_price": round(last, 4) if session_time < time(9, 30) else None,
                "pre_high_price": round(high, 4) if session_time < time(9, 30) else None,
                "pre_low_price": round(low, 4) if session_time < time(9, 30) else None,
                "pre_volume": round(total_volume, 2) if session_time < time(9, 30) else None,
                "premarket_dollar_volume": round(total_volume * last, 2) if session_time < time(9, 30) else None,
                "premarket_relative_volume": round(0.8 + self._unit("pm-rvol", symbol, cutoff.date()) * 2.2, 4),
                "status": "ACTIVE",
                "corporate_action_consistent": True,
                "delisted": False,
                "symbol_changed_to": None,
                "data_source": self.source_name,
            })
        return rows

    def daily(
        self,
        symbol: str,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        as_of: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        normalized = self._require_symbol(symbol)
        cutoff = _resolve_daily_cutoff(as_of, end_date, self.timezone)
        end = _as_date(end_date) if end_date is not None else cutoff.date()
        if end == cutoff.date() and cutoff.timetz().replace(tzinfo=None) < time(16, 0):
            end = _previous_weekday(end)
        start = _as_date(start_date) if start_date is not None else end - timedelta(days=125)
        rows: list[dict[str, Any]] = []
        current = start
        while current <= end:
            if current.weekday() < 5:
                timestamp = datetime.combine(current, time(16, 0), self.timezone)
                if timestamp <= cutoff:
                    close = self._close_for_date(normalized, current)
                    previous = self._close_for_date(normalized, _previous_weekday(current))
                    open_price = previous * (1.0 + (self._unit("daily-open", normalized, current) - 0.5) * 0.014)
                    range_pct = 0.006 + self._unit("daily-range", normalized, current) * 0.026
                    high = max(open_price, close) * (1.0 + range_pct / 2.0)
                    low = min(open_price, close) * (1.0 - range_pct / 2.0)
                    volume = 800_000.0 + self._unit("daily-volume", normalized, current) * 25_000_000.0
                    rows.append({
                        "symbol": normalized,
                        "timestamp": timestamp.isoformat(),
                        "time_key": timestamp.isoformat(),
                        "date": current.isoformat(),
                        "open": round(open_price, 4),
                        "high": round(high, 4),
                        "low": round(low, 4),
                        "close": round(close, 4),
                        "volume": round(volume, 2),
                        "turnover": round(volume * close, 2),
                        "is_complete": True,
                    })
            current += timedelta(days=1)
        return rows

    def premarket(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        normalized = self._require_symbol(symbol)
        target = _as_date(trade_date)
        cutoff_dt = _as_datetime(cutoff, self.timezone)
        if cutoff_dt.date() != target:
            return []
        session_start = datetime.combine(target, time(4, 0), self.timezone)
        session_end = min(cutoff_dt, datetime.combine(target, time(9, 30), self.timezone))
        return self._minute_path(normalized, target, session_start, session_end, "premarket")

    def minute(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        normalized = self._require_symbol(symbol)
        target = _as_date(trade_date)
        cutoff_dt = _as_datetime(cutoff, self.timezone)
        if cutoff_dt.date() != target:
            return []
        session_start = datetime.combine(target, time(9, 30), self.timezone)
        session_end = min(cutoff_dt, datetime.combine(target, time(16, 0), self.timezone))
        return self._minute_path(normalized, target, session_start, session_end, "regular")

    def news(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        # Synthetic mode never invents a catalyst. Relative-strength candidates
        # remain testable while catalyst fixtures belong in replay data.
        self._require_symbols(symbols)
        _as_datetime(as_of, self.timezone)
        return []

    def review(self, symbols: Iterable[str], as_of: datetime | str) -> dict[str, dict[str, Any]]:
        requested = self._require_symbols(symbols)
        cutoff = _as_datetime(as_of, self.timezone)
        result: dict[str, dict[str, Any]] = {}
        for symbol in requested:
            adv20 = 80_000_000.0 + self._unit("review-adv20", symbol, cutoff.date()) * 1_500_000_000.0
            result[symbol] = {
                "average_daily_dollar_volume_20d": round(adv20, 2),
                "average_daily_dollar_volume_60d": round(adv20 * (0.92 + self._unit("review-adv60", symbol, cutoff.date()) * 0.16), 2),
                "median_regular_spread_bps": round(3.0 + self._unit("review-rth-spread", symbol, cutoff.date()) * 12.0, 4),
                "median_premarket_spread_bps": round(8.0 + self._unit("review-pm-spread", symbol, cutoff.date()) * 20.0, 4),
                "atr_percent": round(1.2 + self._unit("review-atr", symbol, cutoff.date()) * 3.8, 4),
                "opening_session_dollar_volume": round(5_000_000.0 + self._unit("review-open-dollar", symbol, cutoff.date()) * 150_000_000.0, 2),
                "data_completeness": round(0.97 + self._unit("review-completeness", symbol, cutoff.date()) * 0.03, 6),
                "premarket_filter_pass_frequency": round(0.15 + self._unit("review-pass", symbol, cutoff.date()) * 0.65, 6),
                "opening_watchlist_frequency": round(0.08 + self._unit("review-watch", symbol, cutoff.date()) * 0.42, 6),
                "as_of": cutoff.isoformat(),
            }
        return result

    def _minute_path(
        self,
        symbol: str,
        trade_date: date,
        start: datetime,
        end: datetime,
        session: str,
    ) -> list[dict[str, Any]]:
        if end <= start:
            return []
        previous_close = self._close_for_date(symbol, _previous_weekday(trade_date))
        gap = (self._unit("gap", symbol, trade_date) - 0.5) * 0.05
        opening_price = previous_close * (1.0 + gap)
        direction = (self._unit(f"{session}-direction", symbol, trade_date) - 0.5) * 0.02
        bars: list[dict[str, Any]] = []
        current = start
        index = 0
        previous = opening_price
        while current + timedelta(minutes=1) <= end:
            noise = (self._unit(f"{session}-bar-{index}", symbol, trade_date) - 0.5) * 0.003
            drift = direction / (345.0 if session == "premarket" else 390.0)
            close = max(1.0, previous * (1.0 + drift + noise))
            wick = 0.0005 + self._unit(f"{session}-wick-{index}", symbol, trade_date) * 0.0015
            high = max(previous, close) * (1.0 + wick)
            low = min(previous, close) * (1.0 - wick)
            volume_scale = 35_000.0 if session == "premarket" else 180_000.0
            volume = volume_scale * (0.5 + self._unit(f"{session}-volume-{index}", symbol, trade_date) * 2.5)
            spread_bps = (8.0 if session == "premarket" else 3.0) + self._unit(
                f"{session}-spread-{index}", symbol, trade_date
            ) * (20.0 if session == "premarket" else 10.0)
            bars.append({
                "symbol": symbol,
                "timestamp": current.isoformat(),
                "time_key": current.isoformat(),
                "bar_end": (current + timedelta(minutes=1)).isoformat(),
                "open": round(previous, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": round(volume, 2),
                "turnover": round(volume * close, 2),
                "spread_bps": round(spread_bps, 4),
                "is_complete": True,
                "session": session,
            })
            previous = close
            current += timedelta(minutes=1)
            index += 1
        return bars

    def _close_for_date(self, symbol: str, value: date) -> float:
        if symbol == "VIX":
            return 12.0 + self._unit("vix-level", symbol, value) * 16.0
        symbol_base = 25.0 + self._unit("base", symbol, date(2000, 1, 3)) * 475.0
        ordinal_wave = math.sin((value.toordinal() + int(self._unit("phase", symbol, date(2000, 1, 3)) * 365)) / 41.0)
        daily = (self._unit("close", symbol, value) - 0.5) * 0.035
        return max(5.0, symbol_base * (1.0 + ordinal_wave * 0.12 + daily))

    @staticmethod
    def _unit(stream: str, symbol: str, value: date) -> float:
        digest = sha256(f"{value.isoformat()}|{symbol}|{stream}".encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64 - 1)


class FutuMarketDataSource(MarketDataSource):
    """Allowlisted adapter around the existing Futu quote client.

    Futu may return data newer than a requested stage cutoff, particularly when
    a late manual run occurs. Every adapter method therefore applies its own
    point-in-time filter before returning provider rows.
    """

    source_name = "futu"

    def __init__(
        self,
        allowed_symbols: Iterable[str] | Any | None,
        client: FutuQuoteClient | None = None,
        *,
        host: str = "127.0.0.1",
        port: int = 11111,
        market_prefix: str = "US",
        extended_time: bool = True,
        news_client: NewsRSSClient | None = None,
        timezone_name: str = MARKET_TIMEZONE,
    ) -> None:
        super().__init__(allowed_symbols)
        self.timezone_name = timezone_name
        self.timezone = ZoneInfo(timezone_name)
        self.client = client or FutuQuoteClient(
            host=host, port=port, market_prefix=market_prefix, extended_time=extended_time
        )
        self.news_client = news_client
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "FutuMarketDataSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def snapshots(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        requested = self._require_symbols(symbols)
        cutoff = _as_datetime(as_of, self.timezone)
        rows = self.client.get_market_snapshot(requested)
        filtered = _filter_records(rows, cutoff, self.timezone, require_timestamp=True)
        requested_set = frozenset(requested)
        return [
            _canonical_record(row, _row_symbol(row), self.timezone)
            for row in filtered
            if _row_symbol(row) in requested_set
        ]

    def daily(
        self,
        symbol: str,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        as_of: datetime | str | None = None,
    ) -> list[dict[str, Any]]:
        normalized = self._require_symbol(symbol)
        cutoff = _resolve_daily_cutoff(as_of, end_date, self.timezone)
        start = _as_date(start_date) if start_date is not None else cutoff.date() - timedelta(days=125)
        end = _as_date(end_date) if end_date is not None else cutoff.date()
        frame = self.client.request_history_kline(
            normalized, start=start.isoformat(), end=end.isoformat(), ktype="K_DAY"
        )
        rows = _frame_records(frame)
        return [
            _canonical_record(row, normalized, self.timezone, daily=True)
            for row in _filter_records(rows, cutoff, self.timezone, daily=True, require_timestamp=True)
        ]

    def premarket(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        normalized = self._require_symbol(symbol)
        target = _as_date(trade_date)
        cutoff_dt = _as_datetime(cutoff, self.timezone)
        rows = _filter_completed_bars(
            _frame_records(self.client.get_realtime_kline(normalized, ktype="K_1M", num=1000)),
            cutoff_dt,
            self.timezone,
        )
        return [
            _canonical_record(row, normalized, self.timezone)
            for row in rows
            if _record_datetime(row, self.timezone).date() == target
            and time(4, 0) <= _record_datetime(row, self.timezone).timetz().replace(tzinfo=None) < time(9, 30)
        ]

    def minute(
        self,
        symbol: str,
        trade_date: date | str,
        cutoff: datetime | str,
    ) -> list[dict[str, Any]]:
        normalized = self._require_symbol(symbol)
        target = _as_date(trade_date)
        cutoff_dt = _as_datetime(cutoff, self.timezone)
        rows = _filter_completed_bars(
            _frame_records(self.client.get_realtime_kline(normalized, ktype="K_1M", num=1000)),
            cutoff_dt,
            self.timezone,
        )
        return [
            _canonical_record(row, normalized, self.timezone)
            for row in rows
            if _record_datetime(row, self.timezone).date() == target
            and time(9, 30) <= _record_datetime(row, self.timezone).timetz().replace(tzinfo=None) < time(16, 0)
        ]

    def news(self, symbols: Iterable[str], as_of: datetime | str) -> list[dict[str, Any]]:
        requested = frozenset(self._require_symbols(symbols))
        cutoff = _as_datetime(as_of, self.timezone)
        if self.news_client is None:
            return []
        rows = self.news_client.fetch(allowed_symbols=requested)
        selected: list[dict[str, Any]] = []
        for row in rows:
            published = _news_datetime(row, self.timezone)
            related = frozenset(_related_symbols(row))
            if published is None or published > cutoff or not (related & requested):
                continue
            item = deepcopy(dict(row))
            item["published_at"] = published.isoformat()
            item["related_symbols"] = sorted(related)
            selected.append(item)
        return sorted(selected, key=lambda item: (item["published_at"], str(item.get("url", ""))))

    def review(self, symbols: Iterable[str], as_of: datetime | str) -> dict[str, dict[str, Any]]:
        requested = self._require_symbols(symbols)
        cutoff = _as_datetime(as_of, self.timezone)
        return {symbol: _derive_review_metrics(self, symbol, cutoff) for symbol in requested}


# Short aliases retained for callers that use provider-oriented names.
ReplayDataSource = ReplayMarketDataSource
DeterministicMockMarketDataSource = DeterministicMarketDataSource
MockMarketDataSource = DeterministicMarketDataSource
FutuDataSource = FutuMarketDataSource


def create_market_data_source(
    kind: str,
    allowed_symbols: Iterable[str] | Any | None,
    *,
    replay_path: str | Path | None = None,
    **kwargs: Any,
) -> MarketDataSource:
    normalized = str(kind or "").strip().lower().replace("-", "_")
    if normalized in {"replay", "fixture"}:
        if replay_path is None:
            raise ValueError("replay_path is required for replay data")
        return ReplayMarketDataSource(replay_path, allowed_symbols=allowed_symbols, **kwargs)
    if normalized in {"mock", "deterministic", "deterministic_mock", "synthetic"}:
        return DeterministicMarketDataSource(allowed_symbols=allowed_symbols, **kwargs)
    if normalized in {"futu", "moomoo", "live"}:
        return FutuMarketDataSource(allowed_symbols=allowed_symbols, **kwargs)
    raise ValueError(f"unsupported market data source: {kind}")


def _derive_review_metrics(source: MarketDataSource, symbol: str, cutoff: datetime) -> dict[str, Any]:
    daily = source.daily(
        symbol,
        start_date=cutoff.date() - timedelta(days=125),
        end_date=cutoff.date(),
        as_of=cutoff,
    )
    dollar_volumes = [
        _number(row.get("turnover"), _number(row.get("close")) * _number(row.get("volume")))
        for row in daily
        if _number(row.get("close")) > 0 and _number(row.get("volume")) >= 0
    ]
    true_ranges: list[float] = []
    previous_close: float | None = None
    for row in daily:
        high = _optional_number(row.get("high"))
        low = _optional_number(row.get("low"))
        close = _optional_number(row.get("close"))
        if high is not None and low is not None and close is not None:
            candidates = [high - low]
            if previous_close is not None:
                candidates.extend((abs(high - previous_close), abs(low - previous_close)))
            true_ranges.append(max(candidates))
            previous_close = close
    recent_close = _optional_number(daily[-1].get("close")) if daily else None
    atr_percent = None
    if true_ranges and recent_close and recent_close > 0:
        atr_percent = sum(true_ranges[-14:]) / min(14, len(true_ranges)) / recent_close * 100.0

    premarket = source.premarket(symbol, cutoff.date(), cutoff)
    regular_cutoff = datetime.combine(cutoff.date(), time(10, 0), cutoff.tzinfo)
    regular = source.minute(symbol, cutoff.date(), min(cutoff, regular_cutoff)) if cutoff.time() >= time(9, 30) else []
    pm_spreads = [_number(row.get("spread_bps")) for row in premarket if row.get("spread_bps") is not None]
    rth_spreads = [_number(row.get("spread_bps")) for row in regular if row.get("spread_bps") is not None]
    opening_dollar_volume = sum(
        _number(row.get("turnover"), _number(row.get("close")) * _number(row.get("volume"))) for row in regular
    )
    return {
        "average_daily_dollar_volume_20d": _mean(dollar_volumes[-20:]),
        "average_daily_dollar_volume_60d": _mean(dollar_volumes[-60:]),
        "median_regular_spread_bps": median(rth_spreads) if rth_spreads else None,
        "median_premarket_spread_bps": median(pm_spreads) if pm_spreads else None,
        "atr_percent": round(atr_percent, 6) if atr_percent is not None else None,
        "opening_session_dollar_volume": round(opening_dollar_volume, 2) if regular else None,
        "data_completeness": round(min(1.0, len(daily) / 60.0), 6),
        "premarket_filter_pass_frequency": None,
        "opening_watchlist_frequency": None,
        "as_of": cutoff.isoformat(),
    }


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else float(default)
    except (TypeError, ValueError):
        return float(default)


def _optional_number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def _as_datetime(value: datetime | str, timezone_value: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("timestamp must not be empty")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            parsed = None
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
            if parsed is None:
                raise ValueError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone_value)
    return parsed.astimezone(timezone_value)


def _resolve_daily_cutoff(
    as_of: datetime | str | None,
    end_date: date | str | None,
    timezone_value: ZoneInfo,
) -> datetime:
    if as_of is not None:
        return _as_datetime(as_of, timezone_value)
    if end_date is not None:
        return datetime.combine(_as_date(end_date), time(23, 59, 59), timezone_value)
    raise ValueError("daily data requires as_of or end_date")


def _record_datetime(row: Mapping[str, Any], timezone_value: ZoneInfo, daily: bool = False) -> datetime:
    for key in _TIMESTAMP_KEYS:
        value = row.get(key)
        if value not in (None, ""):
            parsed = _as_datetime(value, timezone_value)
            # Several quote providers label a daily bar with midnight even
            # though it is not complete until the regular-session close.
            if daily and parsed.timetz().replace(tzinfo=None) == time(0, 0):
                parsed = datetime.combine(parsed.date(), time(16, 0), timezone_value)
            return parsed
    if row.get("date") not in (None, ""):
        close_time = time(16, 0) if daily else time(0, 0)
        return datetime.combine(_as_date(row["date"]), close_time, timezone_value)
    raise ValueError("record has no timestamp")


def _news_datetime(row: Mapping[str, Any], timezone_value: ZoneInfo) -> datetime | None:
    value = row.get("published_at") or row.get("timestamp")
    if value in (None, ""):
        return None
    try:
        return _as_datetime(value, timezone_value)
    except ValueError:
        return None


def _filter_records(
    rows: Iterable[Mapping[str, Any]],
    cutoff: datetime,
    timezone_value: ZoneInfo,
    *,
    daily: bool = False,
    require_timestamp: bool = True,
) -> list[Mapping[str, Any]]:
    selected: list[tuple[datetime, Mapping[str, Any]]] = []
    for row in rows:
        try:
            timestamp = _record_datetime(row, timezone_value, daily=daily)
        except (ValueError, TypeError):
            if require_timestamp:
                continue
            timestamp = cutoff
        if timestamp <= cutoff:
            selected.append((timestamp, row))
    return [row for _, row in sorted(selected, key=lambda item: item[0])]


def _filter_completed_bars(
    rows: Iterable[Mapping[str, Any]], cutoff: datetime, timezone_value: ZoneInfo
) -> list[Mapping[str, Any]]:
    selected: list[tuple[datetime, Mapping[str, Any]]] = []
    for row in rows:
        if row.get("is_complete") is False:
            continue
        try:
            start = _record_datetime(row, timezone_value)
        except (ValueError, TypeError):
            continue
        explicit_end = row.get("bar_end") or row.get("end_time")
        end = _as_datetime(explicit_end, timezone_value) if explicit_end else start + timedelta(minutes=1)
        if end <= cutoff:
            selected.append((start, row))
    return [row for _, row in sorted(selected, key=lambda item: item[0])]


def _canonical_record(
    row: Mapping[str, Any], symbol: str, timezone_value: ZoneInfo, daily: bool = False
) -> dict[str, Any]:
    value = deepcopy(dict(row))
    value["symbol"] = normalize_symbol(symbol)
    try:
        value["timestamp"] = _record_datetime(row, timezone_value, daily=daily).isoformat()
    except ValueError:
        pass
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _frame_records(frame: Any) -> list[Mapping[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, list):
        return [row for row in frame if isinstance(row, Mapping)]
    if hasattr(frame, "to_dict"):
        return frame.to_dict(orient="records")
    return []


def _as_record_list(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, list):
        return [row for row in value if isinstance(row, Mapping)]
    return []


def _row_symbol(row: Mapping[str, Any]) -> str:
    value = row.get("symbol") or row.get("ticker") or row.get("code") or ""
    try:
        return normalize_symbol(str(value))
    except ValueError:
        return ""


def _related_symbols(row: Mapping[str, Any]) -> tuple[str, ...]:
    raw = row.get("related_symbols") or row.get("symbols") or []
    if isinstance(raw, str):
        raw = [raw]
    result: list[str] = []
    for value in raw if isinstance(raw, Iterable) else []:
        try:
            result.append(normalize_symbol(str(value)))
        except ValueError:
            continue
    if not result and _row_symbol(row):
        result.append(_row_symbol(row))
    return tuple(dict.fromkeys(result))


def _safe_symbol(value: Any) -> str:
    try:
        return normalize_symbol(str(value))
    except ValueError:
        return ""


def _symbols_in_records(value: Any, include_related: bool = False) -> set[str]:
    found: set[str] = set()
    for row in _as_record_list(value):
        if _row_symbol(row):
            found.add(_row_symbol(row))
        if include_related:
            found.update(_related_symbols(row))
    return found


def _previous_weekday(value: date) -> date:
    current = value - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current
