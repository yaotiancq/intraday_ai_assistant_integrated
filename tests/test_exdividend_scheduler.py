import sys
from pathlib import Path

from scripts import run_daily_exdividend_scheduler as scheduler


def test_build_exdividend_command_includes_requested_options():
    cmd = scheduler.build_exdividend_command(
        dry_run=True,
        top=7,
        max_candidates=15,
        delay_seconds=0.05,
    )

    assert cmd[:2] == [sys.executable, "scripts/run_get_exdividend_date.py"]
    assert "--top" in cmd
    assert cmd[cmd.index("--top") + 1] == "7"
    assert "--max-candidates" in cmd
    assert cmd[cmd.index("--max-candidates") + 1] == "15"
    assert "--delay-seconds" in cmd
    assert cmd[cmd.index("--delay-seconds") + 1] == "0.05"
    assert "--dry-run" in cmd


def test_build_exdividend_command_omits_unlimited_candidate_limit():
    cmd = scheduler.build_exdividend_command(
        dry_run=False,
        top=20,
        max_candidates=0,
        delay_seconds=0.2,
    )

    assert "--max-candidates" not in cmd
    assert "--dry-run" not in cmd


def test_exdividend_flag_path_uses_data_dir_and_date():
    assert scheduler._flag_path(Path("data"), "2026-06-04") == Path("data/.exdividend_ran_2026-06-04")
