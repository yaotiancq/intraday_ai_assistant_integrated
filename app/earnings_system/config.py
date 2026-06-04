from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
from typing import Iterable

from dotenv import load_dotenv


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [normalize_symbol(x) for x in value.split(",") if x.strip()]


def normalize_symbol(value: str) -> str:
    symbol = str(value or "").strip().upper()
    if symbol.startswith("US."):
        symbol = symbol.split(".", 1)[1]
    return symbol


def normalize_symbols(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        symbol = normalize_symbol(value)
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


@dataclass(frozen=True)
class EarningsConfig:
    fmp_api_key: str
    alphavantage_api_key: str
    discord_webhook_url: str
    earnings_lookahead_days: int
    universe_mode: str
    watchlist_symbols: list[str]
    max_deep_analysis_candidates: int
    request_timeout_seconds: int
    request_retry_count: int
    request_throttle_seconds: float
    timezone_user: str
    timezone_market: str
    bmo_notification_time_pt: str
    amc_notification_time_pt: str
    morning_report_time_pt: str
    pre_close_amc_report_time_pt: str
    post_market_report_time_pt: str
    publish_state_ttl_days: int
    market_reaction_update_threshold_pct: float
    news_limit: int
    news_digest_max_items: int
    output_dir: Path
    dry_run: bool = False


def load_earnings_config(env_file: str | Path | None = None) -> EarningsConfig:
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=False)

    data_dir = Path(os.getenv("DATA_DIR", "data"))
    watchlist_raw = (
        os.getenv("EARNINGS_WATCHLIST_SYMBOLS")
        or os.getenv("WATCH_SYMBOLS")
        or os.getenv("CORE_SYMBOLS")
        or ""
    )
    output_dir = Path(os.getenv("EARNINGS_OUTPUT_DIR", str(data_dir / "earnings")))

    return EarningsConfig(
        fmp_api_key=os.getenv("FMP_API_KEY", "").strip(),
        alphavantage_api_key=os.getenv("ALPHAVANTAGE_API_KEY", "").strip(),
        discord_webhook_url=os.getenv("DISCORD_EARNINGS_WEBHOOK_URL", "").strip(),
        earnings_lookahead_days=int(os.getenv("EARNINGS_LOOKAHEAD_DAYS", "7")),
        universe_mode=os.getenv("EARNINGS_UNIVERSE_MODE", "watchlist_only").strip().lower(),
        watchlist_symbols=_split_csv(watchlist_raw),
        max_deep_analysis_candidates=int(os.getenv("EARNINGS_MAX_DEEP_ANALYSIS_CANDIDATES", "25")),
        request_timeout_seconds=int(os.getenv("EARNINGS_REQUEST_TIMEOUT_SECONDS", "20")),
        request_retry_count=int(os.getenv("EARNINGS_REQUEST_RETRY_COUNT", "2")),
        request_throttle_seconds=float(os.getenv("EARNINGS_REQUEST_THROTTLE_SECONDS", "0.2")),
        timezone_user=os.getenv("EARNINGS_TIMEZONE_USER", os.getenv("TIMEZONE", "America/Los_Angeles")).strip(),
        timezone_market=os.getenv("EARNINGS_TIMEZONE_MARKET", "America/New_York").strip(),
        bmo_notification_time_pt=os.getenv("EARNINGS_BMO_NOTIFICATION_TIME_PT", "04:00").strip(),
        amc_notification_time_pt=os.getenv("EARNINGS_AMC_NOTIFICATION_TIME_PT", "12:45").strip(),
        morning_report_time_pt=os.getenv("EARNINGS_MORNING_REPORT_TIME_PT", "05:30").strip(),
        pre_close_amc_report_time_pt=os.getenv("EARNINGS_PRE_CLOSE_AMC_REPORT_TIME_PT", "12:45").strip(),
        post_market_report_time_pt=os.getenv("EARNINGS_POST_MARKET_REPORT_TIME_PT", "15:30").strip(),
        publish_state_ttl_days=int(os.getenv("EARNINGS_PUBLISH_STATE_TTL_DAYS", "14")),
        market_reaction_update_threshold_pct=float(os.getenv("EARNINGS_MARKET_REACTION_UPDATE_THRESHOLD_PCT", "1.5")),
        news_limit=int(os.getenv("EARNINGS_NEWS_LIMIT", "20")),
        news_digest_max_items=int(os.getenv("EARNINGS_NEWS_DIGEST_MAX_ITEMS", "3")),
        output_dir=output_dir,
        dry_run=_as_bool(os.getenv("EARNINGS_DRY_RUN"), False),
    )
