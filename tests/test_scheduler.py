from datetime import datetime
from zoneinfo import ZoneInfo

from app.persistence import RunRepository
from app.scheduling.market_scheduler import MarketScheduler, ScheduleState


NY = ZoneInfo("America/New_York")


def _config(*, configured_non_trading_days=None):
    return {
        "strategy_version": "test-v1",
        "scheduler": {
            "enabled": True,
            "timezone": "America/New_York",
            "skip_non_trading_days": True,
            "prevent_duplicate_runs": True,
            "maximum_late_start_minutes": 10,
            "configured_non_trading_days": configured_non_trading_days or [],
            "stages": {
                "universe_validation": {"enabled": True, "time": "08:20"},
                "premarket": {"enabled": True, "time": "08:45", "evidence_cutoff": "08:45"},
                "opening_5m": {"enabled": True, "time": "09:35", "evidence_cutoff": "09:35"},
                "opening_15m": {"enabled": True, "time": "09:45", "evidence_cutoff": "09:45"},
            },
        },
    }


def test_late_tick_dispatches_with_scheduled_cutoff_and_is_durably_idempotent(tmp_path):
    repository = RunRepository(tmp_path, "test-v1")
    scheduler = MarketScheduler(_config(), repository)
    calls = []

    def dispatch(stage, trade_date, cutoff, actual_started_at, force):
        calls.append((stage.value, trade_date, cutoff, actual_started_at, force))
        return {"status": "COMPLETED", "symbols": ["NVDA"]}

    now = datetime(2026, 7, 16, 9, 41, tzinfo=NY)
    scheduled = scheduler.schedule_for(now, stage="opening_5m")
    assert scheduled.state == ScheduleState.DUE
    assert scheduled.late_start
    decisions = scheduler.tick(now, dispatch)

    opening_decision = next(item for item in decisions if item.stage.value == "opening_5m")
    assert opening_decision.outcome == "DISPATCHED"
    assert opening_decision.context.scheduled_cutoff.isoformat() == "2026-07-16T09:35:00-04:00"
    assert opening_decision.context.actual_started_at.isoformat() == "2026-07-16T09:41:00-04:00"
    assert opening_decision.context.late_start
    opening_calls = [item for item in calls if item[0] == "opening_5m"]
    assert len(opening_calls) == 1

    scheduler.tick(now, dispatch)
    assert len([item for item in calls if item[0] == "opening_5m"]) == 1


def test_maximum_lateness_is_persisted_as_skipped(tmp_path):
    repository = RunRepository(tmp_path, "test-v1")
    scheduler = MarketScheduler(_config(), repository)
    decisions = scheduler.tick(
        datetime(2026, 7, 16, 9, 0, tzinfo=NY),
        lambda *args: {"status": "COMPLETED"},
    )
    premarket = next(item for item in decisions if item.stage.value == "premarket")
    assert premarket.outcome == "SKIPPED"
    assert premarket.reason_codes == ("MAXIMUM_LATENESS_EXCEEDED",)
    persisted = repository.load_stage("2026-07-16", "premarket")
    assert persisted["status"] == "SKIPPED"


def test_configured_non_trading_day_persists_non_trading_skip(tmp_path):
    repository = RunRepository(tmp_path, "test-v1")
    scheduler = MarketScheduler(
        _config(configured_non_trading_days=["2026-07-16"]),
        repository,
    )
    called = False

    def dispatch(*args):
        nonlocal called
        called = True

    decisions = scheduler.tick(datetime(2026, 7, 16, 8, 45, tzinfo=NY), dispatch)
    assert not called
    assert decisions
    assert all(item.outcome == "SKIPPED" for item in decisions)
    assert all("NON_TRADING_DAY" in item.reason_codes for item in decisions)
