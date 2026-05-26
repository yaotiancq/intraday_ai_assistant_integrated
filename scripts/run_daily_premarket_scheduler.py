from __future__ import annotations

import os
import subprocess
import sys
import time as time_mod
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.config import load_settings
from app.integration.trading_calendar import should_run_trading_day_task


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_hhmm(value: str) -> time:
    h, m = value.strip().split(":", 1)
    return time(int(h), int(m))


def main() -> None:
    load_dotenv(override=False)
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)
    run_time = _parse_hhmm(os.getenv("PREMARKET_RUN_TIME", "05:45"))
    poll_seconds = int(os.getenv("PREMARKET_SCHEDULER_POLL_SECONDS", "30"))
    send_discord = _as_bool(os.getenv("PREMARKET_SEND_DISCORD"), True)
    send_to_monitor = _as_bool(os.getenv("PREMARKET_SEND_TO_MONITOR"), True)
    test_run_on_start = _as_bool(os.getenv("PREMARKET_TEST_RUN_ON_START"), False)
    dry_run = _as_bool(os.getenv("PREMARKET_DRY_RUN"), False)

    ran_dates: set[str] = set()

    print(
        f"[scheduler] started timezone={settings.timezone} run_time={run_time} "
        f"send_discord={send_discord} send_to_monitor={send_to_monitor} dry_run={dry_run}",
        flush=True,
    )

    if test_run_on_start:
        print("[scheduler] PREMARKET_TEST_RUN_ON_START=true, running once immediately.", flush=True)
        _run_once(send_discord=send_discord, send_to_monitor=send_to_monitor, dry_run=dry_run, force_run=True)
        ran_dates.add(datetime.now(tz).date().isoformat())

    while True:
        now = datetime.now(tz)
        today = now.date().isoformat()
        decision = should_run_trading_day_task(
            tz_name=settings.timezone,
            force_run=False,
            allow_non_trading_day_test=False,
            now=now,
        )

        if today not in ran_dates and decision.should_run and now.time() >= run_time:
            print(f"[scheduler] running premarket for {today}; reason={decision.reason}", flush=True)
            _run_once(send_discord=send_discord, send_to_monitor=send_to_monitor, dry_run=dry_run, force_run=False)
            ran_dates.add(today)

        # Prevent unbounded memory over many days.
        if len(ran_dates) > 10:
            ran_dates = set(sorted(ran_dates)[-5:])

        time_mod.sleep(poll_seconds)


def _run_once(send_discord: bool, send_to_monitor: bool, dry_run: bool, force_run: bool) -> None:
    cmd = [sys.executable, "scripts/run_premarket.py"]
    if send_discord:
        cmd.append("--send-discord")
    if send_to_monitor:
        cmd.append("--send-to-monitor")
    if dry_run:
        cmd.append("--dry-run")
    if force_run:
        cmd.append("--force-run")
        cmd.append("--allow-non-trading-day-test")
    print("[scheduler] exec: " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=str(ROOT), check=False)


if __name__ == "__main__":
    main()
