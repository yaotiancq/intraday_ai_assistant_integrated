from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List
import os
from dotenv import load_dotenv


def _split_csv(value: str | None) -> List[str]:
    if not value:
        return []
    return [x.strip().upper() for x in value.split(',') if x.strip()]


def _split_urls(value: str | None) -> List[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(',') if x.strip()]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'y', 'on'}


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    premarket_llm_enabled: bool
    premarket_rule_fallback_enabled: bool
    openai_max_retries: int

    futu_host: str
    futu_port: int
    futu_market_prefix: str
    futu_connect_timeout: int
    futu_extended_time: bool
    demo_mode: bool

    discord_webhook_url1: str
    discord_webhook_url2: str
    discord_webhook_url3: str
    discord_webhook_url4: str
    discord_premarket_webhook_url: str
    discord_open_confirmation_webhook_url: str
    discord_intraday_webhook_url: str
    discord_after_close_webhook_url: str
    discord_warnings_webhook_url: str

    data_dir: Path
    timezone: str
    core_symbols: List[str]
    index_symbols: List[str]
    sector_etfs: List[str]
    news_rss_urls: List[str]
    news_query_symbols: List[str]
    max_a_tier: int
    max_b_tier: int

    monitor_admin_url: str
    monitor_admin_token: str
    monitor_update_enabled: bool
    monitor_update_mode: str
    monitor_update_tiers: List[str]
    monitor_update_max_symbols: int
    monitor_test_mode: bool
    premarket_force_run: bool
    allow_non_trading_day_test: bool


def load_settings(env_file: str | Path | None = None) -> Settings:
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=False)

    data_dir = Path(os.getenv('DATA_DIR', 'data'))
    data_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        openai_api_key=os.getenv('OPENAI_API_KEY', '').strip(),
        openai_model=os.getenv('OPENAI_MODEL', 'gpt-5-mini').strip(),
        premarket_llm_enabled=_as_bool(os.getenv('PREMARKET_LLM_ENABLED'), True),
        premarket_rule_fallback_enabled=_as_bool(os.getenv('PREMARKET_RULE_FALLBACK_ENABLED'), True),
        openai_max_retries=int(os.getenv('OPENAI_MAX_RETRIES', '1')),
        futu_host=os.getenv('FUTU_HOST', '127.0.0.1').strip(),
        futu_port=int(os.getenv('FUTU_PORT', '11111')),
        futu_market_prefix=os.getenv('FUTU_MARKET_PREFIX', 'US').strip().upper(),
        futu_connect_timeout=int(os.getenv('FUTU_CONNECT_TIMEOUT', '15')),
        futu_extended_time=_as_bool(os.getenv('FUTU_EXTENDED_TIME'), True),
        demo_mode=_as_bool(os.getenv('DEMO_MODE'), True),
        discord_webhook_url1=os.getenv('DISCORD_WEBHOOK_URL1', '').strip(),
        discord_webhook_url2=os.getenv('DISCORD_WEBHOOK_URL2', '').strip(),
        discord_webhook_url3=os.getenv('DISCORD_WEBHOOK_URL3', '').strip(),
        discord_webhook_url4=os.getenv('DISCORD_WEBHOOK_URL4', '').strip(),
        discord_premarket_webhook_url=os.getenv('DISCORD_PREMARKET_WEBHOOK_URL', '').strip(),
        discord_open_confirmation_webhook_url=os.getenv('DISCORD_OPEN_CONFIRMATION_WEBHOOK_URL', '').strip(),
        discord_intraday_webhook_url=os.getenv('DISCORD_INTRADAY_WEBHOOK_URL', '').strip(),
        discord_after_close_webhook_url=os.getenv('DISCORD_AFTER_CLOSE_WEBHOOK_URL', '').strip(),
        discord_warnings_webhook_url=os.getenv('DISCORD_WARNINGS_WEBHOOK_URL', '').strip(),
        data_dir=data_dir,
        timezone=os.getenv('TIMEZONE', 'America/Los_Angeles').strip(),
        core_symbols=_split_csv(os.getenv('CORE_SYMBOLS')),
        index_symbols=_split_csv(os.getenv('INDEX_SYMBOLS')),
        sector_etfs=_split_csv(os.getenv('SECTOR_ETFS')),
        news_rss_urls=_split_urls(os.getenv('NEWS_RSS_URLS')),
        news_query_symbols=_split_csv(os.getenv('NEWS_QUERY_SYMBOLS')),
        max_a_tier=int(os.getenv('MAX_A_TIER', '5')),
        max_b_tier=int(os.getenv('MAX_B_TIER', '8')),
        monitor_admin_url=os.getenv('MONITOR_ADMIN_URL', os.getenv('ADMIN_API_URL', 'http://127.0.0.1:8765')).strip(),
        monitor_admin_token=os.getenv('WATCHLIST_ADMIN_TOKEN', '').strip(),
        monitor_update_enabled=_as_bool(os.getenv('MONITOR_UPDATE_ENABLED'), True),
        monitor_update_mode=os.getenv('MONITOR_UPDATE_MODE', 'add').strip().lower(),
        monitor_update_tiers=_split_csv(os.getenv('MONITOR_UPDATE_TIERS', 'A,B')),
        monitor_update_max_symbols=int(os.getenv('MONITOR_UPDATE_MAX_SYMBOLS', '12')),
        monitor_test_mode=_as_bool(os.getenv('MONITOR_TEST_MODE'), False),
        premarket_force_run=_as_bool(os.getenv('PREMARKET_FORCE_RUN'), False),
        allow_non_trading_day_test=_as_bool(os.getenv('ALLOW_NON_TRADING_DAY_TEST'), False),
    )
