from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.validators.configuration_validator import validate_market_configuration


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKET_CONFIG_PATH = PROJECT_ROOT / "config" / "market_strategy.json"


class ConfigurationError(ValueError):
    """Raised when deterministic strategy configuration is invalid."""


def _split_urls(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_market_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load and validate the single source of truth for the market strategy.

    JSON is deliberately used so configuration loading relies only on the Python
    standard library. The returned object is a fresh copy and can safely be
    persisted as the point-in-time configuration snapshot for a run.
    """

    config_path = Path(path or os.getenv("MARKET_CONFIG_PATH", DEFAULT_MARKET_CONFIG_PATH)).expanduser()
    if not config_path.is_absolute():
        config_path = (PROJECT_ROOT / config_path).resolve()
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigurationError(f"market configuration not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"market configuration is not valid JSON: {exc}") from exc

    errors = validate_market_configuration(value)
    if errors:
        raise ConfigurationError("invalid market configuration: " + "; ".join(errors))
    return deepcopy(value)


@dataclass(frozen=True)
class Settings:
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
    output_dir: Path
    timezone: str
    market_config_path: Path
    market_config: dict[str, Any] = field(repr=False)
    news_rss_urls: list[str]

    monitor_admin_url: str
    monitor_admin_token: str
    monitor_update_enabled: bool
    monitor_update_mode: str
    monitor_update_tiers: list[str]
    monitor_update_max_symbols: int
    monitor_test_mode: bool
    premarket_force_run: bool
    allow_non_trading_day_test: bool

    @property
    def core_symbols(self) -> list[str]:
        return [str(item["symbol"]) for item in self.market_config["universe"]["stocks"]]

    @property
    def index_symbols(self) -> list[str]:
        return [str(item["symbol"]) for item in self.market_config["benchmarks"]["broad_market"]]

    @property
    def sector_etfs(self) -> list[str]:
        benchmark_config = self.market_config["benchmarks"]
        return [
            str(item["symbol"])
            for category in ("sectors", "industries")
            for item in benchmark_config[category]
        ]

    @property
    def news_query_symbols(self) -> list[str]:
        return self.core_symbols


def load_settings(env_file: str | Path | None = None, market_config_path: str | Path | None = None) -> Settings:
    if env_file:
        load_dotenv(env_file, override=True)
    else:
        load_dotenv(override=False)

    selected_config_path = Path(
        market_config_path or os.getenv("MARKET_CONFIG_PATH", DEFAULT_MARKET_CONFIG_PATH)
    ).expanduser()
    if not selected_config_path.is_absolute():
        selected_config_path = (PROJECT_ROOT / selected_config_path).resolve()
    market_config = load_market_config(selected_config_path)

    data_dir = Path(os.getenv("DATA_DIR", "data")).expanduser()
    output_value = os.getenv("MARKET_OUTPUT_DIR", market_config.get("output", {}).get("root", "output/runs"))
    output_dir = Path(output_value).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    return Settings(
        futu_host=os.getenv("FUTU_HOST", "127.0.0.1").strip(),
        futu_port=int(os.getenv("FUTU_PORT", "11111")),
        futu_market_prefix=os.getenv("FUTU_MARKET_PREFIX", "US").strip().upper(),
        futu_connect_timeout=int(os.getenv("FUTU_CONNECT_TIMEOUT", "15")),
        futu_extended_time=_as_bool(os.getenv("FUTU_EXTENDED_TIME"), True),
        demo_mode=_as_bool(os.getenv("DEMO_MODE"), True),
        discord_webhook_url1=os.getenv("DISCORD_WEBHOOK_URL1", "").strip(),
        discord_webhook_url2=os.getenv("DISCORD_WEBHOOK_URL2", "").strip(),
        discord_webhook_url3=os.getenv("DISCORD_WEBHOOK_URL3", "").strip(),
        discord_webhook_url4=os.getenv("DISCORD_WEBHOOK_URL4", "").strip(),
        discord_premarket_webhook_url=os.getenv("DISCORD_PREMARKET_WEBHOOK_URL", "").strip(),
        discord_open_confirmation_webhook_url=os.getenv("DISCORD_OPEN_CONFIRMATION_WEBHOOK_URL", "").strip(),
        discord_intraday_webhook_url=os.getenv("DISCORD_INTRADAY_WEBHOOK_URL", "").strip(),
        discord_after_close_webhook_url=os.getenv("DISCORD_AFTER_CLOSE_WEBHOOK_URL", "").strip(),
        discord_warnings_webhook_url=os.getenv("DISCORD_WARNINGS_WEBHOOK_URL", "").strip(),
        data_dir=data_dir,
        output_dir=output_dir,
        timezone=market_config["scheduler"]["timezone"],
        market_config_path=selected_config_path,
        market_config=market_config,
        news_rss_urls=_split_urls(os.getenv("NEWS_RSS_URLS")),
        monitor_admin_url=os.getenv("MONITOR_ADMIN_URL", os.getenv("ADMIN_API_URL", "http://127.0.0.1:8765")).strip(),
        monitor_admin_token=os.getenv("WATCHLIST_ADMIN_TOKEN", "").strip(),
        monitor_update_enabled=_as_bool(os.getenv("MONITOR_UPDATE_ENABLED"), False),
        monitor_update_mode=os.getenv("MONITOR_UPDATE_MODE", "set").strip().lower(),
        monitor_update_tiers=_split_csv(os.getenv("MONITOR_UPDATE_TIERS", "CONFIRMED,WATCH")),
        monitor_update_max_symbols=int(os.getenv("MONITOR_UPDATE_MAX_SYMBOLS", "8")),
        monitor_test_mode=_as_bool(os.getenv("MONITOR_TEST_MODE"), False),
        premarket_force_run=_as_bool(os.getenv("PREMARKET_FORCE_RUN"), False),
        allow_non_trading_day_test=_as_bool(os.getenv("ALLOW_NON_TRADING_DAY_TEST"), False),
    )
