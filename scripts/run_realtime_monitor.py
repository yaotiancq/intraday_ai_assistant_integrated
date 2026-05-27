#!/usr/bin/env python3
"""
futu_opening_momentum_signal.py

Single-file, signal-only strategy monitor for Futu OpenAPI.

Purpose
-------
Monitor multiple high-volume US ETFs / stocks with Futu real-time K-line data
and print BUY / SELL / WATCH signals for an opening momentum breakout strategy.

Important
---------
- This script DOES NOT place orders.
- It only prints structured signals.
- It assumes Futu OpenD is running and logged in.
- US timestamps returned by Futu are treated as US Eastern time.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from hmac import compare_digest
from typing import Deque, Dict, Iterable, List, Optional, Tuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import os
import urllib.request
import urllib.error

from futu import (  # type: ignore
    AuType,
    CurKlineHandlerBase,
    KLType,
    OpenQuoteContext,
    RET_OK,
    Session,
    SubType,
)

from dotenv import load_dotenv


def load_project_env() -> Path:
    """
    Load .env from the project root instead of relying on the shell's
    current working directory.

    Supported layouts:
    1. project_root/scripts/run_realtime_monitor.py
    2. project_root/futu_opening_momentum_signal.py
    3. running from any directory with ENV_FILE explicitly set

    ENV_FILE has the highest priority:
        ENV_FILE=/absolute/path/to/.env python scripts/run_realtime_monitor.py
    """
    explicit_env = os.getenv("ENV_FILE")
    if explicit_env:
        env_path = Path(explicit_env).expanduser().resolve()
        load_dotenv(dotenv_path=env_path, override=False)
        return env_path

    current_file = Path(__file__).resolve()

    # Case 1: file is inside project_root/scripts/
    if current_file.parent.name == "scripts":
        project_root = current_file.parents[1]
    else:
        # Case 2: file is directly under project root
        project_root = current_file.parent

    env_path = project_root / ".env"

    # Fallback: search upward from the current file location.
    if not env_path.exists():
        for parent in [current_file.parent, *current_file.parents]:
            candidate = parent / ".env"
            if candidate.exists():
                env_path = candidate
                break

    load_dotenv(dotenv_path=env_path, override=False)
    return env_path


ENV_PATH = load_project_env()


# ==============================
# User Configuration
# ==============================

DEFAULT_SYMBOLS = [
    # ETFs
    "US.SPY", "US.QQQ", "US.IWM", "US.DIA", "US.SMH", "US.SOXX", "US.XLF", "US.XLE",
    # Mega-cap / high-volume stocks
    "US.NVDA", "US.TSLA", "US.AMD", "US.AAPL", "US.MSFT", "US.AMZN", "US.META",
    "US.GOOGL", "US.AVGO", "US.PLTR", "US.INTC", "US.MU",
]

FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111

# Universe selection
MAX_SYMBOLS_TO_MONITOR = 20
MIN_DAILY_VOLUME = 1_000_000
MIN_VOLUME_RATIO = 0.8
MIN_PRICE = 5.0

# Strategy time window. Futu US market time is treated as US Eastern time.
ENTRY_START = dtime(9, 33)
ENTRY_END = dtime(10, 30)
FORCE_EXIT_TIME = dtime(15, 55)

# Breakout setup
BOOTSTRAP_BARS = 80
MAX_STORED_BARS = 240
BREAKOUT_VOLUME_MULT = 2.0 #2.0
BREAKOUT_RANGE_MULT = 1.4
MIN_BODY_RATIO = 0.45
MIN_CLOSE_POSITION = 0.75
MAX_ONE_BAR_RETURN = 0.015  # avoid chasing a single bar already up too much

# Compression filter before breakout
REQUIRE_COMPRESSION = True
COMPRESSION_BARS = 4
COMPRESSION_VOL_RATIO = 0.85
COMPRESSION_RANGE_RATIO = 0.85

# Risk / exit logic
STOP_LOSS_PCT = 0.004          # 0.4% hard stop from signal price
BREAKOUT_FAIL_BUFFER = 0.001   # 0.1% below breakout level
TRAILING_PULLBACK_PCT = 0.006  # 0.6% from high-water mark
STALL_BARS = 3
EMA_EXIT_PERIOD = 9

# Operational
WORKER_THREADS = 8
HEARTBEAT_SECONDS = 30

# Local admin API for Discord Bot control. Keep it bound to 127.0.0.1.
ADMIN_HOST = "127.0.0.1"
ADMIN_PORT = 8765
ADMIN_TOKEN_ENV = "WATCHLIST_ADMIN_TOKEN"
ADMIN_ALLOW_EMPTY_TOKEN_ENV = "MONITOR_ALLOW_EMPTY_ADMIN_TOKEN"


@dataclass(frozen=True)
class BarPeriodConfig:
    label: str
    minutes: int
    futu_type_name: str
    breakout_lookback: int
    compression_bars: int
    ema_exit_period: int
    stall_bars: int
    max_one_bar_return: float

    @property
    def breakout_lookback_minutes(self) -> int:
        return self.minutes * self.breakout_lookback

    @property
    def compression_minutes(self) -> int:
        return self.minutes * self.compression_bars

    @property
    def ema_exit_minutes(self) -> int:
        return self.minutes * self.ema_exit_period

    @property
    def stall_minutes(self) -> int:
        return self.minutes * self.stall_bars


BAR_PERIOD_CONFIGS = {
    "1m": BarPeriodConfig("1m", 1, "K_1M", 20, 4, 9, 3, 0.010),
    "3m": BarPeriodConfig("3m", 3, "K_3M", 8, 2, 5, 2, 0.015),
    "5m": BarPeriodConfig("5m", 5, "K_5M", 6, 2, 3, 2, 0.020),
}


def _env_bool_monitor(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_bar_period(value: str | None) -> str:
    raw = str(value or "3m").strip().lower().replace("_", "")
    aliases = {
        "1": "1m",
        "1m": "1m",
        "1min": "1m",
        "k1m": "1m",
        "3": "3m",
        "3m": "3m",
        "3min": "3m",
        "k3m": "3m",
        "5": "5m",
        "5m": "5m",
        "5min": "5m",
        "k5m": "5m",
    }
    try:
        return aliases[raw]
    except KeyError as exc:
        raise ValueError("bar_period must be one of: 1m, 3m, 5m") from exc


def get_bar_period_config(value: str | None) -> BarPeriodConfig:
    return BAR_PERIOD_CONFIGS[normalize_bar_period(value)]


def _subtype_for_period(config: BarPeriodConfig) -> object:
    return getattr(SubType, config.futu_type_name)


def _kltype_for_period(config: BarPeriodConfig) -> object:
    return getattr(KLType, config.futu_type_name)


def _env_session_name() -> str:
    value = os.getenv("MONITOR_FUTU_SESSION", "RTH").strip().upper()
    if _env_bool_monitor("MONITOR_TEST_MODE", False):
        value = "ALL"
    return "ALL" if value == "ALL" else "RTH"


def _session_from_name(value: str) -> object:
    return Session.ALL if value == "ALL" else Session.RTH


MONITOR_TEST_MODE = _env_bool_monitor("MONITOR_TEST_MODE", False)
MONITOR_EXTENDED_TIME = _env_bool_monitor("MONITOR_EXTENDED_TIME", MONITOR_TEST_MODE)
MONITOR_FUTU_SESSION_NAME = _env_session_name()
MONITOR_FUTU_SESSION = _session_from_name(MONITOR_FUTU_SESSION_NAME)

# Default production behavior: regular trading hours only.
# Test override: MONITOR_TEST_MODE=true allows all-day windows and all-session subscription.
if MONITOR_TEST_MODE:
    ENTRY_START = dtime(0, 0)
    ENTRY_END = dtime(23, 59)
    FORCE_EXIT_TIME = dtime(23, 58)
DISCORD_WEBHOOK_URL_ENV = "DISCORD_WEBHOOK_URL"
DISCORD_WEBHOOK_EVENTS_ENV = "DISCORD_WEBHOOK_EVENTS"


# ==============================
# Data Models
# ==============================

@dataclass(frozen=True)
class Bar:
    code: str
    time_key: str
    dt: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    turnover: float

    @property
    def range_abs(self) -> float:
        return max(self.high - self.low, 0.0)

    @property
    def body_abs(self) -> float:
        return abs(self.close - self.open)

    @property
    def body_ratio(self) -> float:
        return safe_div(self.body_abs, self.range_abs)

    @property
    def close_position(self) -> float:
        # 1.0 means close at high; 0.0 means close at low
        return safe_div(self.close - self.low, self.range_abs)

    @property
    def return_from_open(self) -> float:
        return safe_div(self.close - self.open, self.open)


@dataclass
class SymbolState:
    code: str
    bars: Deque[Bar] = field(default_factory=lambda: deque(maxlen=MAX_STORED_BARS))
    current_bar: Optional[Bar] = None

    position: str = "FLAT"  # FLAT or LONG
    entry_price: Optional[float] = None
    entry_time: Optional[str] = None
    breakout_level: Optional[float] = None
    high_water: Optional[float] = None
    bars_since_entry: int = 0
    bars_without_new_high: int = 0

    last_signal: Optional[str] = None
    last_signal_time: Optional[str] = None
    lock: threading.RLock = field(default_factory=threading.RLock)


# ==============================
# Utility Functions
# ==============================

def safe_div(a: float, b: float, default: float = 0.0) -> float:
    if b is None or abs(b) < 1e-12:
        return default
    return a / b


def parse_time_key(time_key: str) -> datetime:
    return datetime.strptime(str(time_key), "%Y-%m-%d %H:%M:%S")


def in_time_window(dt: datetime, start: dtime, end: dtime) -> bool:
    t = dt.time()
    return start <= t <= end


def mean(values: Iterable[float]) -> float:
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def intraday_vwap(bars: List[Bar]) -> Optional[float]:
    if not bars:
        return None
    last_date = bars[-1].dt.date()
    today_bars = [b for b in bars if b.dt.date() == last_date]
    total_volume = sum(b.volume for b in today_bars)
    if total_volume <= 0:
        return None

    # Prefer Futu turnover when available. Otherwise approximate with typical price * volume.
    total_turnover = 0.0
    for b in today_bars:
        if b.turnover and b.turnover > 0:
            total_turnover += b.turnover
        else:
            typical_price = (b.high + b.low + b.close) / 3
            total_turnover += typical_price * b.volume
    return total_turnover / total_volume


def make_bar_from_row(row: dict) -> Optional[Bar]:
    try:
        time_key = str(row["time_key"])
        return Bar(
            code=str(row["code"]),
            time_key=time_key,
            dt=parse_time_key(time_key),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(float(row.get("volume", 0) or 0)),
            turnover=float(row.get("turnover", 0.0) or 0.0),
        )
    except Exception as exc:
        print_json("ERROR", {"reason": "failed_to_parse_bar", "row": str(row), "error": str(exc)})
        return None


def send_discord_webhook_async(msg: dict) -> None:
    """
    Send selected events to Discord via incoming webhook.

    This intentionally uses the Python standard library to avoid adding a
    blocking dependency to the signal path. Network errors are swallowed and
    printed as WARN events to avoid crashing the monitor.
    """
    webhook_url = os.getenv(DISCORD_WEBHOOK_URL_ENV, "").strip()
    if not webhook_url:
        return

    allowed_events_raw = os.getenv(DISCORD_WEBHOOK_EVENTS_ENV, "SIGNAL,ADMIN,STARTED,STOPPED").strip()
    allowed_events = {x.strip().upper() for x in allowed_events_raw.split(",") if x.strip()}
    if str(msg.get("event", "")).upper() not in allowed_events:
        return

    event = str(msg.get("event", "EVENT"))
    side = str(msg.get("side", "") or "")
    code = str(msg.get("code", "") or "")
    reason = str(msg.get("reason", "") or "")

    title_parts = [event]
    if side:
        title_parts.append(side)
    if code:
        title_parts.append(code)

    content = "**{}**\n```json\n{}\n```".format(
        " | ".join(title_parts),
        json.dumps(msg, ensure_ascii=False, indent=2, sort_keys=True)[:1800],
    )

    def _post() -> None:
        try:
            raw = json.dumps({"content": content}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=raw,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "monitor/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
        except Exception as exc:
            print(json.dumps({
                "event": "WARN",
                "local_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reason": "discord_webhook_failed",
                "error": str(exc),
            }, ensure_ascii=False, sort_keys=True), flush=True)

    threading.Thread(target=_post, name="discord-webhook-post", daemon=True).start()


def print_json(event: str, payload: dict) -> None:
    msg = {
        "event": event,
        "local_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **payload,
    }
    print(json.dumps(msg, ensure_ascii=False, sort_keys=True), flush=True)
    send_discord_webhook_async(msg)


# ==============================
# Strategy Logic
# ==============================

def has_pre_breakout_compression(prev_bars: List[Bar], config: BarPeriodConfig) -> Tuple[bool, dict]:
    """
    Healthy compression before breakout:
    - last few bars before breakout have smaller volume and smaller range than the prior baseline
    - price does not collapse during compression
    """
    if len(prev_bars) < config.breakout_lookback:
        return False, {"reason": "not_enough_bars_for_compression"}

    compression_bars = config.compression_bars
    compression = prev_bars[-compression_bars:]
    baseline = prev_bars[-config.breakout_lookback:-compression_bars]
    if len(compression) < compression_bars or not baseline:
        return False, {"reason": "not_enough_baseline"}

    compression_avg_vol = mean(b.volume for b in compression)
    baseline_avg_vol = mean(b.volume for b in baseline)
    compression_avg_range = mean(b.range_abs for b in compression)
    baseline_avg_range = mean(b.range_abs for b in baseline)

    volume_compressed = compression_avg_vol <= baseline_avg_vol * COMPRESSION_VOL_RATIO
    range_compressed = compression_avg_range <= baseline_avg_range * COMPRESSION_RANGE_RATIO

    # Price should hold near the upper half of the compression zone, not drift down aggressively.
    first_close = compression[0].close
    last_close = compression[-1].close
    price_holding = last_close >= first_close * 0.997

    ok = volume_compressed and range_compressed and price_holding
    details = {
        "bar_period": config.label,
        "breakout_lookback": config.breakout_lookback,
        "breakout_lookback_minutes": config.breakout_lookback_minutes,
        "compression_bars": config.compression_bars,
        "compression_minutes": config.compression_minutes,
        "compression_avg_vol": round(compression_avg_vol, 2),
        "baseline_avg_vol": round(baseline_avg_vol, 2),
        "compression_avg_range": round(compression_avg_range, 4),
        "baseline_avg_range": round(baseline_avg_range, 4),
        "volume_compressed": volume_compressed,
        "range_compressed": range_compressed,
        "price_holding": price_holding,
    }
    return ok, details


def detect_long_entry(bars: List[Bar], state: SymbolState, config: BarPeriodConfig) -> Tuple[bool, dict]:
    """
    Detect a completed breakout bar.
    The latest bar is the just-completed signal bar.
    """
    if len(bars) < config.breakout_lookback + 2:
        return False, {"reason": "not_enough_bars"}

    signal_bar = bars[-1]
    prev_bars = bars[:-1]
    lookback = prev_bars[-config.breakout_lookback:]

    if not in_time_window(signal_bar.dt, ENTRY_START, ENTRY_END):
        return False, {"reason": "outside_entry_window"}

    prior_high = max(b.high for b in lookback)
    avg_volume = mean(b.volume for b in lookback)
    avg_range = mean(b.range_abs for b in lookback)
    prior_close = prev_bars[-1].close

    if REQUIRE_COMPRESSION:
        compression_ok, compression_details = has_pre_breakout_compression(prev_bars, config)
        if not compression_ok:
            return False, {"reason": "no_pre_breakout_compression", **compression_details}
    else:
        compression_details = {}

    vwap = intraday_vwap(bars)
    above_vwap = vwap is not None and signal_bar.close > vwap

    close_breakout = signal_bar.close > prior_high
    high_volume = signal_bar.volume >= avg_volume * BREAKOUT_VOLUME_MULT
    wide_range = signal_bar.range_abs >= avg_range * BREAKOUT_RANGE_MULT
    strong_close = signal_bar.close_position >= MIN_CLOSE_POSITION
    strong_body = signal_bar.body_ratio >= MIN_BODY_RATIO
    not_overextended = safe_div(signal_bar.close - prior_close, prior_close) <= config.max_one_bar_return

    ok = all([
        close_breakout,
        high_volume,
        wide_range,
        strong_close,
        strong_body,
        above_vwap,
        not_overextended,
    ])

    details = {
        "bar_period": config.label,
        "breakout_lookback": config.breakout_lookback,
        "breakout_lookback_minutes": config.breakout_lookback_minutes,
        "max_one_bar_return": config.max_one_bar_return,
        "signal_price": round(signal_bar.close, 4),
        "breakout_level": round(prior_high, 4),
        "bar_volume": signal_bar.volume,
        "avg_volume": round(avg_volume, 2),
        "volume_mult": round(safe_div(signal_bar.volume, avg_volume), 2),
        "bar_range": round(signal_bar.range_abs, 4),
        "avg_range": round(avg_range, 4),
        "range_mult": round(safe_div(signal_bar.range_abs, avg_range), 2),
        "body_ratio": round(signal_bar.body_ratio, 3),
        "close_position": round(signal_bar.close_position, 3),
        "vwap": round(vwap, 4) if vwap is not None else None,
        "above_vwap": above_vwap,
        "not_overextended": not_overextended,
        **compression_details,
    }

    if not ok:
        failed = []
        for name, passed in [
            ("close_breakout", close_breakout),
            ("high_volume", high_volume),
            ("wide_range", wide_range),
            ("strong_close", strong_close),
            ("strong_body", strong_body),
            ("above_vwap", above_vwap),
            ("not_overextended", not_overextended),
        ]:
            if not passed:
                failed.append(name)
        details["reason"] = "entry_conditions_failed"
        details["failed"] = failed
        return False, details

    return True, details


def detect_long_exit(bars: List[Bar], state: SymbolState, config: BarPeriodConfig) -> Tuple[bool, str, dict]:
    if state.position != "LONG" or state.entry_price is None:
        return False, "", {"reason": "not_long"}

    if len(bars) < max(config.ema_exit_period, 3):
        return False, "", {"reason": "not_enough_bars"}

    bar = bars[-1]
    ema_period = config.ema_exit_period
    closes = [b.close for b in bars[-max(ema_period * 3, 30):]]
    ema_value = ema(closes, ema_period)

    state.bars_since_entry += 1

    if state.high_water is None:
        state.high_water = bar.high

    if bar.high > state.high_water:
        state.high_water = bar.high
        state.bars_without_new_high = 0
    else:
        state.bars_without_new_high += 1

    breakout_level = state.breakout_level or state.entry_price
    hard_stop = bar.close <= state.entry_price * (1 - STOP_LOSS_PCT)
    breakout_failed = bar.close < breakout_level * (1 - BREAKOUT_FAIL_BUFFER)
    trailing_pullback = state.high_water is not None and bar.close <= state.high_water * (1 - TRAILING_PULLBACK_PCT)
    ema_break = ema_value is not None and bar.close < ema_value
    stall = state.bars_without_new_high >= config.stall_bars and bar.close < bars[-2].close
    force_exit = bar.dt.time() >= FORCE_EXIT_TIME

    details = {
        "bar_period": config.label,
        "signal_price": round(bar.close, 4),
        "entry_price": round(state.entry_price, 4),
        "entry_time": state.entry_time,
        "breakout_level": round(breakout_level, 4),
        "high_water": round(state.high_water, 4) if state.high_water is not None else None,
        "bars_since_entry": state.bars_since_entry,
        "bars_without_new_high": state.bars_without_new_high,
        "ema_exit": round(ema_value, 4) if ema_value is not None else None,
        "ema_exit_period": config.ema_exit_period,
        "ema_exit_minutes": config.ema_exit_minutes,
        "stall_bars": config.stall_bars,
        "stall_minutes": config.stall_minutes,
        "unrealized_return_pct": round(100 * safe_div(bar.close - state.entry_price, state.entry_price), 3),
    }

    if hard_stop:
        return True, "hard_stop", details
    if breakout_failed:
        return True, "breakout_failed", details
    if trailing_pullback:
        return True, "trailing_pullback", details
    if stall:
        return True, "stall_no_follow_through", details
    if ema_break and state.bars_since_entry >= 2:
        return True, "ema9_break", details
    if force_exit:
        return True, "force_exit_time", details

    return False, "", details


# ==============================
# Signal Engine
# ==============================

class OpeningMomentumSignalEngine:
    def __init__(
        self,
        symbols: List[str],
        workers: int = WORKER_THREADS,
        bar_period_config: BarPeriodConfig | None = None,
    ):
        self.states_lock = threading.RLock()
        self.config_lock = threading.RLock()
        self.bar_period_config = bar_period_config or get_bar_period_config("3m")
        self.states: Dict[str, SymbolState] = {code: SymbolState(code=code) for code in symbols}
        self.executor = ThreadPoolExecutor(max_workers=workers)
        self._shutdown = threading.Event()

    def list_symbols(self) -> List[str]:
        with self.states_lock:
            return sorted(self.states.keys())

    def get_bar_period_config(self) -> BarPeriodConfig:
        with self.config_lock:
            return self.bar_period_config

    def strategy_status(self) -> dict:
        config = self.get_bar_period_config()
        return {
            "bar_period": config.label,
            "futu_type": config.futu_type_name,
            "breakout_lookback": config.breakout_lookback,
            "breakout_lookback_minutes": config.breakout_lookback_minutes,
            "compression_bars": config.compression_bars,
            "compression_minutes": config.compression_minutes,
            "ema_exit_period": config.ema_exit_period,
            "ema_exit_minutes": config.ema_exit_minutes,
            "stall_bars": config.stall_bars,
            "stall_minutes": config.stall_minutes,
            "max_one_bar_return": config.max_one_bar_return,
        }

    def set_bar_period_config(self, config: BarPeriodConfig) -> None:
        with self.config_lock:
            self.bar_period_config = config
        with self.states_lock:
            states = list(self.states.values())
        for state in states:
            with state.lock:
                self._reset_state_locked(state)

    def ensure_symbol_state(self, code: str) -> bool:
        """Create state for a new symbol. Return True if newly added."""
        with self.states_lock:
            if code in self.states:
                return False
            self.states[code] = SymbolState(code=code)
            return True

    def remove_symbol_state(self, code: str) -> bool:
        """Remove local strategy state for a symbol. Return True if removed."""
        with self.states_lock:
            return self.states.pop(code, None) is not None

    @staticmethod
    def _reset_state_locked(state: SymbolState) -> None:
        state.bars.clear()
        state.current_bar = None
        state.position = "FLAT"
        state.entry_price = None
        state.entry_time = None
        state.breakout_level = None
        state.high_water = None
        state.bars_since_entry = 0
        state.bars_without_new_high = 0
        state.last_signal = None
        state.last_signal_time = None

    def shutdown(self) -> None:
        self._shutdown.set()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def bootstrap_symbol(self, code: str, data) -> None:
        """
        Load recent K-lines. Treat the last row as the currently forming bar
        and earlier rows as completed bars.
        """
        if code not in self.states or data is None or len(data) == 0:
            return

        rows = data.to_dict("records")
        bars = [make_bar_from_row(row) for row in rows]
        bars = [b for b in bars if b is not None]
        bars.sort(key=lambda b: b.dt)

        if not bars:
            return

        state = self.states[code]
        with state.lock:
            state.bars.clear()
            for b in bars[:-1]:
                state.bars.append(b)
            state.current_bar = bars[-1]

        print_json("BOOTSTRAP", {
            "code": code,
            **self.strategy_status(),
            "completed_bars": max(len(bars) - 1, 0),
            "current_bar": bars[-1].time_key,
        })

    def on_kline_dataframe(self, data) -> None:
        if data is None or len(data) == 0:
            return

        for row in data.to_dict("records"):
            bar = make_bar_from_row(row)
            if bar is not None:
                self.on_bar_update(bar)

    def on_bar_update(self, bar: Bar) -> None:
        with self.states_lock:
            state = self.states.get(bar.code)
        if state is None:
            return

        completed_bar: Optional[Bar] = None

        with state.lock:
            if state.current_bar is None:
                state.current_bar = bar
                return

            # Update the currently forming bar.
            if bar.time_key == state.current_bar.time_key:
                state.current_bar = bar
                self._emit_intrabar_watch_if_needed(state, bar)
                return

            # New minute started. The previous current bar is now completed.
            if bar.dt > state.current_bar.dt:
                completed_bar = state.current_bar
                state.bars.append(completed_bar)
                state.current_bar = bar
            else:
                # Ignore out-of-order old bar updates.
                return

        if completed_bar is not None and not self._shutdown.is_set():
            self.executor.submit(self.evaluate_completed_bar, bar.code)

    def _emit_intrabar_watch_if_needed(self, state: SymbolState, current_bar: Bar) -> None:
        """
        Optional early warning on the forming bar.
        This is not a BUY signal. It is a watch signal for a rapidly forming breakout bar.
        """
        if state.position != "FLAT":
            return
        if not in_time_window(current_bar.dt, ENTRY_START, ENTRY_END):
            return
        config = self.get_bar_period_config()
        if len(state.bars) < config.breakout_lookback:
            return

        bars = list(state.bars)
        lookback = bars[-config.breakout_lookback:]
        prior_high = max(b.high for b in lookback)
        avg_volume = mean(b.volume for b in lookback)
        avg_range = mean(b.range_abs for b in lookback)

        is_fast_breakout_forming = (
            current_bar.high > prior_high and
            current_bar.volume >= avg_volume * BREAKOUT_VOLUME_MULT and
            current_bar.range_abs >= avg_range * BREAKOUT_RANGE_MULT and
            current_bar.close_position >= MIN_CLOSE_POSITION
        )

        if not is_fast_breakout_forming:
            return

        # Avoid spamming repeated WATCH for same forming bar.
        if state.last_signal == "WATCH" and state.last_signal_time == current_bar.time_key:
            return

        state.last_signal = "WATCH"
        state.last_signal_time = current_bar.time_key

        print_json("SIGNAL", {
            "code": state.code,
            "side": "WATCH",
            "bar_time": current_bar.time_key,
            "reason": "forming_fast_breakout",
            **self.strategy_status(),
            "price": round(current_bar.close, 4),
            "prior_high": round(prior_high, 4),
            "volume_mult": round(safe_div(current_bar.volume, avg_volume), 2),
            "range_mult": round(safe_div(current_bar.range_abs, avg_range), 2),
            "note": "WATCH only; final BUY requires completed bar confirmation.",
        })

    def evaluate_completed_bar(self, code: str) -> None:
        with self.states_lock:
            state = self.states.get(code)
        if state is None:
            return

        with state.lock:
            bars = list(state.bars)
            if not bars:
                return

            latest_bar = bars[-1]

            # First manage exits if virtually long.
            if state.position == "LONG":
                should_exit, exit_reason, exit_details = detect_long_exit(
                    bars,
                    state,
                    self.get_bar_period_config(),
                )
                if should_exit:
                    self._emit_sell_signal_locked(state, latest_bar, exit_reason, exit_details)
                return

            # Then look for long entries if flat.
            if state.position == "FLAT":
                should_buy, entry_details = detect_long_entry(bars, state, self.get_bar_period_config())
                if should_buy:
                    self._emit_buy_signal_locked(state, latest_bar, entry_details)

    def _emit_buy_signal_locked(self, state: SymbolState, bar: Bar, details: dict) -> None:
        # Avoid duplicate signal for the same bar.
        if state.last_signal == "BUY" and state.last_signal_time == bar.time_key:
            return

        state.position = "LONG"
        state.entry_price = bar.close
        state.entry_time = bar.time_key
        state.breakout_level = details.get("breakout_level", bar.high)
        state.high_water = bar.high
        state.bars_since_entry = 0
        state.bars_without_new_high = 0
        state.last_signal = "BUY"
        state.last_signal_time = bar.time_key

        print_json("SIGNAL", {
            "code": state.code,
            "side": "BUY",
            "bar_time": bar.time_key,
            "reason": "opening_momentum_breakout_confirmed",
            **details,
        })

    def _emit_sell_signal_locked(self, state: SymbolState, bar: Bar, reason: str, details: dict) -> None:
        if state.last_signal == "SELL" and state.last_signal_time == bar.time_key:
            return

        state.position = "FLAT"
        state.last_signal = "SELL"
        state.last_signal_time = bar.time_key

        print_json("SIGNAL", {
            "code": state.code,
            "side": "SELL",
            "bar_time": bar.time_key,
            "reason": reason,
            **self.strategy_status(),
            **details,
        })

        # Reset virtual position fields after printing details.
        state.entry_price = None
        state.entry_time = None
        state.breakout_level = None
        state.high_water = None
        state.bars_since_entry = 0
        state.bars_without_new_high = 0



# ==============================
# Local Admin API for Watchlist Control
# ==============================

def normalize_symbol(raw: str) -> str:
    """
    Normalize user input:
    - NVDA -> US.NVDA
    - us.nvda -> US.NVDA
    - US.NVDA -> US.NVDA
    """
    symbol = str(raw or "").strip().upper()
    if not symbol:
        raise ValueError("empty symbol")
    if "." not in symbol:
        symbol = f"US.{symbol}"
    market, ticker = symbol.split(".", 1)
    if market != "US":
        raise ValueError("only US.* symbols are allowed in this script")
    if not ticker.replace("-", "").replace(".", "").isalnum():
        raise ValueError(f"invalid ticker: {ticker}")
    return symbol


class WatchlistAdminController:
    """
    Thin control layer used by the local HTTP admin server.

    It updates both:
    1. Futu subscriptions
    2. in-memory strategy states
    """

    def __init__(self, quote_ctx: OpenQuoteContext, engine: OpeningMomentumSignalEngine):
        self.quote_ctx = quote_ctx
        self.engine = engine
        self.lock = threading.RLock()

    def list_symbols(self) -> dict:
        return {"symbols": self.engine.list_symbols(), **self.engine.strategy_status()}

    def strategy_status(self) -> dict:
        return {"status": "ok", "symbols": self.engine.list_symbols(), **self.engine.strategy_status()}

    def _subscribe_symbols(self, symbols: List[str], config: BarPeriodConfig) -> None:
        if not symbols:
            return
        ret, message = self.quote_ctx.subscribe(
            code_list=symbols,
            subtype_list=[_subtype_for_period(config)],
            is_first_push=True,
            subscribe_push=True,
            extended_time=MONITOR_EXTENDED_TIME,
            session=MONITOR_FUTU_SESSION,
        )
        if ret != RET_OK:
            raise RuntimeError(f"Futu subscribe failed for {symbols}: {message}")

    def _unsubscribe_symbols(self, symbols: List[str], config: BarPeriodConfig, strict: bool = False) -> None:
        if not symbols:
            return
        try:
            ret, message = self.quote_ctx.unsubscribe(symbols, [_subtype_for_period(config)])
        except Exception as exc:
            print_json("WARN", {
                "reason": "unsubscribe_failed",
                "symbols": symbols,
                "bar_period": config.label,
                "message": str(exc),
            })
            if strict:
                raise
            return
        if ret != RET_OK:
            print_json("WARN", {
                "reason": "unsubscribe_failed",
                "symbols": symbols,
                "bar_period": config.label,
                "message": str(message),
            })
            if strict:
                raise RuntimeError(f"Futu unsubscribe failed for {symbols}: {message}")

    def _bootstrap_symbol(self, code: str, config: BarPeriodConfig) -> None:
        ret, data = self.quote_ctx.get_cur_kline(
            code=code,
            num=BOOTSTRAP_BARS,
            ktype=_kltype_for_period(config),
            autype=AuType.QFQ,
        )
        if ret == RET_OK:
            self.engine.bootstrap_symbol(code, data)
        else:
            print_json("WARN", {
                "code": code,
                "reason": "admin_bootstrap_get_cur_kline_failed",
                "bar_period": config.label,
                "message": str(data),
            })

    def add_symbol(self, symbol: str) -> dict:
        code = normalize_symbol(symbol)
        with self.lock:
            already_exists = not self.engine.ensure_symbol_state(code)
            if already_exists:
                return {"status": "ok", "action": "noop", "symbol": code, **self.list_symbols()}

            config = self.engine.get_bar_period_config()
            try:
                self._subscribe_symbols([code], config)
            except Exception:
                self.engine.remove_symbol_state(code)
                raise

            self._bootstrap_symbol(code, config)

            print_json("ADMIN", {"action": "add", "symbol": code, **self.list_symbols()})
            return {"status": "ok", "action": "add", "symbol": code, **self.list_symbols()}

    def remove_symbol(self, symbol: str) -> dict:
        code = normalize_symbol(symbol)
        with self.lock:
            existed = self.engine.remove_symbol_state(code)
            self._unsubscribe_symbols([code], self.engine.get_bar_period_config())

            print_json("ADMIN", {"action": "remove", "symbol": code, "existed": existed, **self.list_symbols()})
            return {"status": "ok", "action": "remove", "symbol": code, "existed": existed, **self.list_symbols()}

    def set_symbols(self, symbols: List[str]) -> dict:
        desired = sorted({normalize_symbol(s) for s in symbols})
        with self.lock:
            current = set(self.engine.list_symbols())
            desired_set = set(desired)

            for code in sorted(current - desired_set):
                self.remove_symbol(code)

            for code in desired:
                if code not in current:
                    self.add_symbol(code)

            print_json("ADMIN", {"action": "set", **self.list_symbols()})
            return {"status": "ok", "action": "set", **self.list_symbols()}

    def clear(self) -> dict:
        with self.lock:
            current = self.engine.list_symbols()
            for code in current:
                self.remove_symbol(code)

            print_json("ADMIN", {"action": "clear", **self.list_symbols()})
            return {"status": "ok", "action": "clear", **self.list_symbols()}

    def set_bar_period(self, raw_period: str) -> dict:
        new_config = get_bar_period_config(raw_period)
        with self.lock:
            old_config = self.engine.get_bar_period_config()
            symbols = self.engine.list_symbols()
            if old_config.label == new_config.label:
                return {"status": "ok", "action": "noop", **self.list_symbols()}

            self._unsubscribe_symbols(symbols, old_config, strict=True)
            try:
                self._subscribe_symbols(symbols, new_config)
            except Exception:
                # Best-effort restore of the previous live subscription.
                self._subscribe_symbols(symbols, old_config)
                raise

            self.engine.set_bar_period_config(new_config)
            for code in symbols:
                self._bootstrap_symbol(code, new_config)

            result = {"status": "ok", "action": "set_bar_period", **self.list_symbols()}
            print_json("ADMIN", result)
            return result


class WatchlistAdminHandler(BaseHTTPRequestHandler):
    controller: Optional[WatchlistAdminController] = None
    token: str = ""

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send_json(self, status: int, payload: dict) -> bool:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return True

        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected before the response was fully written.
            # This is not a monitor business failure.
            return False

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw) if raw else {}

    def _authorized(self) -> bool:
        if not self.token:
            return True
        return compare_digest(self.headers.get("X-Admin-Token", ""), self.token)

    def do_GET(self) -> None:
        if not self._authorized():
            self._send_json(401, {"status": "error", "error": "unauthorized"})
            return

        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if path == "/watchlist":
            assert self.controller is not None
            self._send_json(200, {"status": "ok", **self.controller.list_symbols()})
            return
        if path == "/strategy":
            assert self.controller is not None
            self._send_json(200, self.controller.strategy_status())
            return
        self._send_json(404, {"status": "error", "error": "not_found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send_json(401, {"status": "error", "error": "unauthorized"})
            return

        assert self.controller is not None
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/watchlist/add":
                self._send_json(200, self.controller.add_symbol(payload.get("symbol", "")))
                return
            if path == "/watchlist/remove":
                self._send_json(200, self.controller.remove_symbol(payload.get("symbol", "")))
                return
            if path == "/watchlist/set":
                self._send_json(200, self.controller.set_symbols(payload.get("symbols", [])))
                return
            if path == "/watchlist/clear":
                self._send_json(200, self.controller.clear())
                return
            if path == "/strategy/bar-period":
                period = payload.get("bar_period", payload.get("period", ""))
                self._send_json(200, self.controller.set_bar_period(period))
                return
            self._send_json(404, {"status": "error", "error": "not_found"})
        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected. Do not try to write another error response.
            return

        except Exception as exc:
            self._send_json(400, {"status": "error", "error": str(exc)})


def start_admin_server(host: str, port: int, token: str, controller: WatchlistAdminController) -> ThreadingHTTPServer:
    WatchlistAdminHandler.controller = controller
    WatchlistAdminHandler.token = token
    server = ThreadingHTTPServer((host, port), WatchlistAdminHandler)
    thread = threading.Thread(target=server.serve_forever, name="watchlist-admin-api", daemon=True)
    thread.start()
    print_json("ADMIN_SERVER_STARTED", {
        "host": host,
        "port": port,
        "token_required": bool(token),
        "endpoints": [
            "GET /health",
            "GET /watchlist",
            "GET /strategy",
            "POST /watchlist/add",
            "POST /watchlist/remove",
            "POST /watchlist/set",
            "POST /watchlist/clear",
            "POST /strategy/bar-period",
        ],
    })
    return server


# ==============================
# Futu Handler
# ==============================

class KlineSignalHandler(CurKlineHandlerBase):
    def __init__(self, engine: OpeningMomentumSignalEngine):
        super().__init__()
        self.engine = engine

    def on_recv_rsp(self, rsp_pb):
        ret_code, data = super(KlineSignalHandler, self).on_recv_rsp(rsp_pb)
        if ret_code != RET_OK:
            print_json("ERROR", {"reason": "kline_callback_error", "message": str(data)})
            return ret_code, data

        self.engine.on_kline_dataframe(data)
        return RET_OK, data


# ==============================
# Futu Connection / Universe
# ==============================

def create_quote_context(host: str, port: int) -> OpenQuoteContext:
    return OpenQuoteContext(host=host, port=port)


def select_high_volume_symbols(
    quote_ctx: OpenQuoteContext,
    candidates: List[str],
    max_symbols: int = MAX_SYMBOLS_TO_MONITOR,
) -> List[str]:
    """
    Pick high-volume symbols from the configured candidate list using Futu market snapshot.

    Fallback: if snapshot fails or returns no candidates, use the original candidate list.
    """
    ret, data = quote_ctx.get_market_snapshot(candidates)
    if ret != RET_OK:
        print_json("WARN", {
            "reason": "snapshot_failed_fallback_to_configured_symbols",
            "message": str(data),
        })
        return candidates[:max_symbols]

    selected_rows = []
    for row in data.to_dict("records"):
        try:
            code = str(row["code"])
            last_price = float(row.get("last_price", 0.0) or 0.0)
            volume = int(float(row.get("volume", 0) or 0))
            volume_ratio = float(row.get("volume_ratio", 0.0) or 0.0)
            suspension = bool(row.get("suspension", False))
            if (
                not suspension
                and last_price >= MIN_PRICE
                and volume >= MIN_DAILY_VOLUME
                and volume_ratio >= MIN_VOLUME_RATIO
            ):
                selected_rows.append((code, volume, volume_ratio, last_price))
        except Exception:
            continue

    if not selected_rows:
        print_json("WARN", {
            "reason": "no_symbols_passed_snapshot_filter_fallback_to_configured_symbols",
            "candidate_count": len(candidates),
        })
        return candidates[:max_symbols]

    selected_rows.sort(key=lambda x: (x[2], x[1]), reverse=True)
    selected = [x[0] for x in selected_rows[:max_symbols]]

    print_json("UNIVERSE", {
        "selected_count": len(selected),
        "selected": selected,
    })
    return selected


def subscribe_realtime_bars(quote_ctx: OpenQuoteContext, symbols: List[str], config: BarPeriodConfig) -> None:
    ret, message = quote_ctx.subscribe(
        code_list=symbols,
        subtype_list=[_subtype_for_period(config)],
        is_first_push=True,
        subscribe_push=True,
        extended_time=MONITOR_EXTENDED_TIME,
        session=MONITOR_FUTU_SESSION,
    )
    if ret != RET_OK:
        raise RuntimeError(f"Futu subscribe failed: {message}")

    print_json("SUBSCRIBED", {
        "symbols": symbols,
        "subtypes": [config.futu_type_name],
        "bar_period": config.label,
        "breakout_lookback": config.breakout_lookback,
        "breakout_lookback_minutes": config.breakout_lookback_minutes,
        "session": MONITOR_FUTU_SESSION_NAME,
        "extended_time": MONITOR_EXTENDED_TIME,
    })


def bootstrap_recent_bars(
    quote_ctx: OpenQuoteContext,
    engine: OpeningMomentumSignalEngine,
    symbols: List[str],
    config: BarPeriodConfig,
) -> None:
    for code in symbols:
        ret, data = quote_ctx.get_cur_kline(
            code=code,
            num=BOOTSTRAP_BARS,
            ktype=_kltype_for_period(config),
            autype=AuType.QFQ,
        )
        if ret == RET_OK:
            engine.bootstrap_symbol(code, data)
        else:
            print_json("WARN", {
                "code": code,
                "reason": "bootstrap_get_cur_kline_failed",
                "bar_period": config.label,
                "message": str(data),
            })


# ==============================
# Main
# ==============================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Futu real-time opening momentum signal monitor. Signal-only; no order placement."
    )
    parser.add_argument("--host", default=FUTU_HOST, help="Futu OpenD host. Default: 127.0.0.1")
    parser.add_argument("--port", type=int, default=FUTU_PORT, help="Futu OpenD port. Default: 11111")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated Futu symbols, e.g. US.SPY,US.QQQ,US.NVDA",
    )
    parser.add_argument("--max-symbols", type=int, default=MAX_SYMBOLS_TO_MONITOR)
    parser.add_argument("--workers", type=int, default=WORKER_THREADS)
    parser.add_argument(
        "--bar-period",
        default=os.getenv("MONITOR_BAR_PERIOD", "3m"),
        help="K-line bar period for the realtime monitor: 1m, 3m, or 5m. Default: env MONITOR_BAR_PERIOD or 3m.",
    )
    parser.add_argument(
        "--skip-snapshot-filter",
        action="store_true",
        help="Use configured symbols directly instead of selecting by market snapshot volume/volume_ratio.",
    )
    parser.add_argument("--admin-host", default=ADMIN_HOST, help="Local admin API host. Keep 127.0.0.1 for safety.")
    parser.add_argument("--admin-port", type=int, default=ADMIN_PORT, help="Local admin API port.")
    parser.add_argument(
        "--admin-token",
        default=os.getenv(ADMIN_TOKEN_ENV, ""),
        help="Admin API token. Prefer setting WATCHLIST_ADMIN_TOKEN in the environment.",
    )
    parser.add_argument(
        "--allow-empty-admin-token",
        action="store_true",
        default=_env_bool_monitor(ADMIN_ALLOW_EMPTY_TOKEN_ENV, False),
        help="Allow an unauthenticated admin API. Only use for isolated local tests.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print_json("CONFIG", {
        "env_path": str(ENV_PATH),
        "admin_token_loaded": bool(args.admin_token),
        "futu_host": args.host,
        "futu_port": args.port,
        "admin_host": args.admin_host,
        "admin_port": args.admin_port,
        "symbols": args.symbols,
        "monitor_test_mode": MONITOR_TEST_MODE,
        "monitor_extended_time": MONITOR_EXTENDED_TIME,
        "monitor_futu_session": MONITOR_FUTU_SESSION_NAME,
    })
    candidates = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not candidates:
        print_json("ERROR", {"reason": "empty_symbol_list"})
        return 2
    try:
        bar_period_config = get_bar_period_config(args.bar_period)
    except ValueError as exc:
        print_json("ERROR", {"reason": "invalid_bar_period", "message": str(exc)})
        return 2
    if not args.admin_token and not args.allow_empty_admin_token:
        print_json("ERROR", {
            "reason": "missing_admin_token",
            "message": (
                f"Set {ADMIN_TOKEN_ENV} to protect the watchlist admin API. "
                f"For isolated local tests only, set {ADMIN_ALLOW_EMPTY_TOKEN_ENV}=true "
                "or pass --allow-empty-admin-token."
            ),
        })
        return 2
    if not args.admin_token and args.allow_empty_admin_token:
        print_json("WARN", {
            "reason": "empty_admin_token_allowed",
            "message": "Watchlist admin API is unauthenticated for this run.",
        })

    quote_ctx = create_quote_context(args.host, args.port)

    if args.skip_snapshot_filter:
        symbols = candidates[:args.max_symbols]
    else:
        symbols = select_high_volume_symbols(quote_ctx, candidates, max_symbols=args.max_symbols)

    engine = OpeningMomentumSignalEngine(
        symbols=symbols,
        workers=args.workers,
        bar_period_config=bar_period_config,
    )
    quote_ctx.set_handler(KlineSignalHandler(engine))
    admin_controller = WatchlistAdminController(quote_ctx=quote_ctx, engine=engine)
    admin_server: Optional[ThreadingHTTPServer] = None

    stop_event = threading.Event()

    def _handle_stop(signum, frame):
        print_json("INFO", {"reason": "shutdown_signal_received", "signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    try:
        subscribe_realtime_bars(quote_ctx, symbols, bar_period_config)
        bootstrap_recent_bars(quote_ctx, engine, symbols, bar_period_config)
        admin_server = start_admin_server(
            host=args.admin_host,
            port=args.admin_port,
            token=args.admin_token,
            controller=admin_controller,
        )

        print_json("STARTED", {
            "message": "Signal monitor is running. It prints WATCH/BUY/SELL only; no orders are placed.",
            "symbol_count": len(symbols),
            "symbols": symbols,
            **engine.strategy_status(),
            "monitor_test_mode": MONITOR_TEST_MODE,
            "monitor_extended_time": MONITOR_EXTENDED_TIME,
            "monitor_futu_session": MONITOR_FUTU_SESSION_NAME,
        })

        last_heartbeat = 0.0
        while not stop_event.is_set():
            now = time.time()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                last_heartbeat = now
                current_symbols = engine.list_symbols()
                print_json("HEARTBEAT", {
                    "symbol_count": len(current_symbols),
                    "symbols": current_symbols,
                    **engine.strategy_status(),
                    "state": "running",
                })
            time.sleep(1)

    except Exception as exc:
        print_json("ERROR", {"reason": "fatal", "message": str(exc)})
        return 1

    finally:
        if admin_server is not None:
            try:
                admin_server.shutdown()
                admin_server.server_close()
            except Exception:
                pass
        try:
            quote_ctx.unsubscribe_all()
        except Exception:
            pass
        try:
            quote_ctx.close()
        except Exception:
            pass
        engine.shutdown()
        print_json("STOPPED", {"message": "Signal monitor stopped."})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
