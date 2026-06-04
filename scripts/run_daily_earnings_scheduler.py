from __future__ import annotations

import os
import subprocess
import sys
import time as time_mod
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from app.earnings_system.config import EarningsConfig, load_earnings_config
from app.integration.trading_calendar import is_us_trading_day


@dataclass(frozen=True)
class ScheduledEarningsJob:
    name: str
    command: str
    run_time: time


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_hhmm(value: str) -> time:
    h, m = value.strip().split(":", 1)
    return time(int(h), int(m))


def scheduled_jobs(config: EarningsConfig) -> list[ScheduledEarningsJob]:
    return [
        ScheduledEarningsJob(
            name="morning",
            command="run-morning-earnings-report",
            run_time=_parse_hhmm(config.morning_report_time_pt),
        ),
        ScheduledEarningsJob(
            name="pre_close_amc",
            command="run-pre-close-amc-report",
            run_time=_parse_hhmm(config.pre_close_amc_report_time_pt),
        ),
        ScheduledEarningsJob(
            name="post_market",
            command="run-post-market-earnings-report",
            run_time=_parse_hhmm(config.post_market_report_time_pt),
        ),
    ]


def _flag_path(output_dir: Path, date_iso: str, command: str) -> Path:
    return output_dir / ".scheduler" / f"{date_iso}_{command}.done"


def _mark_complete(output_dir: Path, date_iso: str, command: str, tz: ZoneInfo) -> None:
    flag_path = _flag_path(output_dir, date_iso, command)
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(datetime.now(tz).isoformat(), encoding="utf-8")


def build_earnings_command(command: str, *, dry_run: bool, skip_discord: bool) -> list[str]:
    cmd = [sys.executable, "-m", "earnings_system.cli", command]
    if dry_run:
        cmd.append("--dry-run")
    if skip_discord:
        cmd.append("--skip-discord")
    return cmd


def _run_once(command: str, *, dry_run: bool, skip_discord: bool) -> int:
    cmd = build_earnings_command(command, dry_run=dry_run, skip_discord=skip_discord)
    print("[earnings-scheduler] exec: " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(ROOT), check=False).returncode


def run_startup_once_if_needed(
    *,
    output_dir: Path,
    tz: ZoneInfo,
    dry_run: bool,
    skip_discord: bool,
    jobs: list[ScheduledEarningsJob],
) -> bool:
    startup_date = datetime.now(tz).date().isoformat()
    print("[earnings-scheduler] EARNINGS_TEST_RUN_ON_START=true, running daily workflow once.", flush=True)
    returncode = _run_once("run-daily-earnings-workflow", dry_run=dry_run, skip_discord=skip_discord)
    if returncode == 0 and not dry_run:
        for job in jobs:
            _mark_complete(output_dir, startup_date, job.command, tz)
        print(f"[earnings-scheduler] marked all earnings jobs complete for {startup_date}.", flush=True)
    return returncode == 0


def main() -> None:
    load_dotenv(override=False)
    config = load_earnings_config()
    tz = ZoneInfo(config.timezone_user)
    jobs = scheduled_jobs(config)
    poll_seconds = int(os.getenv("EARNINGS_SCHEDULER_POLL_SECONDS", "30"))
    dry_run = _as_bool(os.getenv("EARNINGS_DRY_RUN"), config.dry_run)
    skip_discord = _as_bool(os.getenv("EARNINGS_SKIP_DISCORD"), False)
    test_run_on_start = _as_bool(os.getenv("EARNINGS_TEST_RUN_ON_START"), False)

    config.output_dir.mkdir(parents=True, exist_ok=True)
    last_notice: tuple[str, str] | None = None

    schedule_text = ", ".join(f"{job.command}@{job.run_time.strftime('%H:%M')}" for job in jobs)
    print(
        f"[earnings-scheduler] started timezone={config.timezone_user} schedule={schedule_text} "
        f"dry_run={dry_run} skip_discord={skip_discord}",
        flush=True,
    )

    if test_run_on_start:
        run_startup_once_if_needed(
            output_dir=config.output_dir,
            tz=tz,
            dry_run=dry_run,
            skip_discord=skip_discord,
            jobs=jobs,
        )

    while True:
        now = datetime.now(tz)
        today = now.date().isoformat()

        if not is_us_trading_day(now.date()):
            notice = (today, "not_trading_day")
            if last_notice != notice:
                print(f"[earnings-scheduler] {today} is not a US trading day; waiting.", flush=True)
                last_notice = notice
        else:
            ran_any = False
            for job in jobs:
                flag_path = _flag_path(config.output_dir, today, job.command)
                if flag_path.exists() or now.time() < job.run_time:
                    continue

                print(f"[earnings-scheduler] running {job.command} for {today}.", flush=True)
                returncode = _run_once(job.command, dry_run=dry_run, skip_discord=skip_discord)
                if returncode == 0:
                    _mark_complete(config.output_dir, today, job.command, tz)
                    print(f"[earnings-scheduler] marked {job.command} complete for {today}.", flush=True)
                else:
                    print(
                        f"[earnings-scheduler] {job.command} failed with exit code {returncode}; will retry.",
                        flush=True,
                    )
                ran_any = True
                last_notice = None

            if not ran_any:
                remaining = [
                    job
                    for job in jobs
                    if not _flag_path(config.output_dir, today, job.command).exists()
                ]
                if remaining:
                    next_job = min(remaining, key=lambda job: job.run_time)
                    notice = (today, f"waiting_{next_job.command}")
                    if last_notice != notice:
                        print(
                            f"[earnings-scheduler] waiting until {next_job.run_time} for {next_job.command}.",
                            flush=True,
                        )
                        last_notice = notice
                else:
                    notice = (today, "all_complete")
                    if last_notice != notice:
                        print(f"[earnings-scheduler] all earnings jobs complete for {today}.", flush=True)
                        last_notice = notice

        time_mod.sleep(poll_seconds)


if __name__ == "__main__":
    main()
