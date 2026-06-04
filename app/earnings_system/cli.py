from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from .config import load_earnings_config
from .workflow import run_earnings_workflow


COMMANDS = {
    "scan-earnings-calendar",
    "run-morning-earnings-report",
    "run-pre-close-amc-report",
    "run-post-market-earnings-report",
    "run-daily-earnings-workflow",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run batch FMP earnings event intelligence workflows.")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--env-file", default=None, help="Path to .env file")
    parser.add_argument("--days", type=int, default=None, help="Calendar lookahead days")
    parser.add_argument("--as-of-date", default=None, help="Override local run date, YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Write outputs but do not send Discord")
    parser.add_argument("--skip-discord", action="store_true", help="Do not send Discord")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_earnings_config(args.env_file)
    if args.dry_run:
        config = config.__class__(**{**config.__dict__, "dry_run": True})
    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else None

    result = run_earnings_workflow(
        config=config,
        command=args.command,
        days=args.days,
        as_of=as_of,
        send_discord=not args.skip_discord,
    )
    print(
        f"{args.command}: candidates={len(result.candidates)} "
        f"published={len(result.published_messages)} skipped={result.skipped_messages} "
        f"warnings={len(result.warnings)}"
    )
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings[:20]:
            print(f"- {warning}")
    for message in result.published_messages:
        print("\n---\n")
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

