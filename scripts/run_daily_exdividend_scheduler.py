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
from app.integration.trading_calendar import is_us_trading_day


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_hhmm(value: str) -> time:
    h, m = value.strip().split(":", 1)
    return time(int(h), int(m))


def _flag_path(data_dir: Path, date_iso: str) -> Path:
    return data_dir / f".exdividend_ran_{date_iso}"


def _mark_complete(data_dir: Path, date_iso: str, tz: ZoneInfo) -> None:
    _flag_path(data_dir, date_iso).write_text(datetime.now(tz).isoformat(), encoding="utf-8")


def build_exdividend_command(
    *,
    dry_run: bool,
    top: int,
    max_candidates: int,
    delay_seconds: float,
) -> list[str]:
    cmd = [
        sys.executable,
        "scripts/run_get_exdividend_date.py",
        "--top",
        str(top),
        "--delay-seconds",
        str(delay_seconds),
    ]
    if max_candidates > 0:
        cmd.extend(["--max-candidates", str(max_candidates)])
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def _run_once(*, dry_run: bool, top: int, max_candidates: int, delay_seconds: float) -> int:
    cmd = build_exdividend_command(
        dry_run=dry_run,
        top=top,
        max_candidates=max_candidates,
        delay_seconds=delay_seconds,
    )
    print("[exdividend-scheduler] exec: " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(ROOT), check=False).returncode


def run_startup_once_if_needed(
    *,
    data_dir: Path,
    tz: ZoneInfo,
    dry_run: bool,
    top: int,
    max_candidates: int,
    delay_seconds: float,
) -> bool:
    startup_now = datetime.now(tz)
    startup_date = startup_now.date().isoformat()
    print("[exdividend-scheduler] EXDIVIDEND_TEST_RUN_ON_START=true, running once immediately.", flush=True)
    returncode = _run_once(dry_run=dry_run, top=top, max_candidates=max_candidates, delay_seconds=delay_seconds)
    if returncode == 0 and not dry_run:
        _mark_complete(data_dir, startup_date, tz)
        print(f"[exdividend-scheduler] marked {startup_date} complete after startup run.", flush=True)
    return returncode == 0


def main() -> None:
    load_dotenv(override=False)
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)
    run_time = _parse_hhmm(os.getenv("EXDIVIDEND_RUN_TIME", "05:30"))
    poll_seconds = int(os.getenv("EXDIVIDEND_SCHEDULER_POLL_SECONDS", "30"))
    dry_run = _as_bool(os.getenv("EXDIVIDEND_DRY_RUN"), False)
    test_run_on_start = _as_bool(os.getenv("EXDIVIDEND_TEST_RUN_ON_START"), False)
    top = int(os.getenv("EXDIVIDEND_TOP", "20"))
    max_candidates = int(os.getenv("EXDIVIDEND_MAX_CANDIDATES", "0"))
    delay_seconds = float(os.getenv("EXDIVIDEND_DELAY_SECONDS", "0.2"))

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    last_notice: tuple[str, str] | None = None

    print(
        f"[exdividend-scheduler] started timezone={settings.timezone} run_time={run_time} "
        f"dry_run={dry_run} top={top} max_candidates={max_candidates} delay_seconds={delay_seconds}",
        flush=True,
    )

    if test_run_on_start:
        run_startup_once_if_needed(
            data_dir=settings.data_dir,
            tz=tz,
            dry_run=dry_run,
            top=top,
            max_candidates=max_candidates,
            delay_seconds=delay_seconds,
        )

    while True:
        now = datetime.now(tz)
        today = now.date().isoformat()
        flag_path = _flag_path(settings.data_dir, today)

        if not is_us_trading_day(now.date()):
            notice = (today, "not_trading_day")
            if last_notice != notice:
                print(f"[exdividend-scheduler] {today} is not a US trading day; waiting.", flush=True)
                last_notice = notice
        elif flag_path.exists():
            notice = (today, "already_ran")
            if last_notice != notice:
                print(f"[exdividend-scheduler] already ran for {today}; waiting for next day.", flush=True)
                last_notice = notice
        elif now.time() < run_time:
            notice = (today, "waiting_for_run_time")
            if last_notice != notice:
                print(f"[exdividend-scheduler] waiting until {run_time} for {today}.", flush=True)
                last_notice = notice
        else:
            print(f"[exdividend-scheduler] running ex-dividend report for {today}.", flush=True)
            returncode = _run_once(
                dry_run=dry_run,
                top=top,
                max_candidates=max_candidates,
                delay_seconds=delay_seconds,
            )
            if returncode == 0:
                _mark_complete(settings.data_dir, today, tz)
                print(f"[exdividend-scheduler] marked {today} complete.", flush=True)
            else:
                print(f"[exdividend-scheduler] report failed with exit code {returncode}; will retry.", flush=True)
            last_notice = None

        time_mod.sleep(poll_seconds)


if __name__ == "__main__":
    main()
