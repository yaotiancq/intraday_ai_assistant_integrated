import sys
from pathlib import Path

from app.earnings_system.config import EarningsConfig
from scripts import run_daily_earnings_scheduler as scheduler


def _config(tmp_path: Path) -> EarningsConfig:
    return EarningsConfig(
        fmp_api_key="fmp",
        alphavantage_api_key="alpha",
        discord_webhook_url="",
        earnings_lookahead_days=7,
        universe_mode="calendar_all_limited",
        watchlist_symbols=["AAPL"],
        max_deep_analysis_candidates=25,
        request_timeout_seconds=20,
        request_retry_count=2,
        request_throttle_seconds=0.2,
        timezone_user="America/Los_Angeles",
        timezone_market="America/New_York",
        bmo_notification_time_pt="04:00",
        amc_notification_time_pt="12:45",
        morning_report_time_pt="05:30",
        pre_close_amc_report_time_pt="12:45",
        post_market_report_time_pt="15:30",
        publish_state_ttl_days=14,
        market_reaction_update_threshold_pct=1.5,
        news_limit=20,
        news_digest_max_items=3,
        output_dir=tmp_path / "earnings",
        dry_run=False,
    )


def test_build_earnings_command_uses_module_entrypoint():
    cmd = scheduler.build_earnings_command(
        "run-morning-earnings-report",
        dry_run=True,
        skip_discord=True,
    )

    assert cmd[:3] == [sys.executable, "-m", "earnings_system.cli"]
    assert cmd[3] == "run-morning-earnings-report"
    assert "--dry-run" in cmd
    assert "--skip-discord" in cmd


def test_scheduled_jobs_use_earnings_config_times(tmp_path):
    jobs = scheduler.scheduled_jobs(_config(tmp_path))

    assert [job.command for job in jobs] == [
        "run-morning-earnings-report",
        "run-pre-close-amc-report",
        "run-post-market-earnings-report",
    ]
    assert [job.run_time.strftime("%H:%M") for job in jobs] == ["05:30", "12:45", "15:30"]


def test_earnings_flag_path_uses_output_dir_date_and_command():
    assert scheduler._flag_path(Path("data/earnings"), "2026-06-04", "run-morning-earnings-report") == Path(
        "data/earnings/.scheduler/2026-06-04_run-morning-earnings-report.done"
    )


def test_startup_run_does_not_mark_scheduled_jobs_complete(tmp_path, monkeypatch):
    calls = []
    jobs = scheduler.scheduled_jobs(_config(tmp_path))

    def fake_run_once(command, **kwargs):
        calls.append((command, kwargs))
        return 0

    monkeypatch.setattr(scheduler, "_run_once", fake_run_once)

    ok = scheduler.run_startup_once_if_needed(
        dry_run=False,
        skip_discord=False,
    )

    today = scheduler.datetime.now().date().isoformat()
    assert ok is True
    assert calls == [("run-daily-earnings-workflow", {"dry_run": False, "skip_discord": False})]
    for job in jobs:
        assert not scheduler._flag_path(tmp_path, today, job.command).exists()


def test_startup_dry_run_does_not_mark_jobs_complete(tmp_path, monkeypatch):
    jobs = scheduler.scheduled_jobs(_config(tmp_path))

    def fake_run_once(command, **kwargs):
        return 0

    monkeypatch.setattr(scheduler, "_run_once", fake_run_once)

    ok = scheduler.run_startup_once_if_needed(
        dry_run=True,
        skip_discord=False,
    )

    today = scheduler.datetime.now().date().isoformat()
    assert ok is True
    for job in jobs:
        assert not scheduler._flag_path(tmp_path, today, job.command).exists()
