from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import load_settings
from app.data_sources import create_market_data_source
from app.universe import build_fixed_universe, validate_universe_health


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the configured fixed 30-stock universe.")
    parser.add_argument("--date", default=None)
    parser.add_argument("--data-source", choices=["mock", "futu", "replay"], default="mock")
    parser.add_argument("--input-file", default=None)
    parser.add_argument("--env-file", default=None)
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    settings = load_settings(args.env_file, args.config)
    timezone = ZoneInfo(settings.market_config["scheduler"]["timezone"])
    trade_date = args.date or datetime.now(timezone).date().isoformat()
    universe = build_fixed_universe(trade_date, settings.market_config)
    source_kwargs = {}
    if args.data_source == "futu":
        source_kwargs = {
            "host": settings.futu_host,
            "port": settings.futu_port,
            "market_prefix": settings.futu_market_prefix,
            "extended_time": settings.futu_extended_time,
        }
    source = create_market_data_source(
        args.data_source,
        universe,
        replay_path=args.input_file,
        **source_kwargs,
    )
    cutoff = datetime.fromisoformat(f"{trade_date}T08:20:00").replace(tzinfo=timezone)
    try:
        report = validate_universe_health(universe, source, cutoff).to_dict()
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    finally:
        close = getattr(source, "close", None)
        if callable(close):
            close()


if __name__ == "__main__":
    raise SystemExit(main())
