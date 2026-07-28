import json

import pytest

from app.models.run_models import AnalysisStage
from app.persistence import (
    DuplicateRunError,
    InvalidSnapshotError,
    RunRepository,
    SnapshotLoader,
    SnapshotNotFoundError,
)


def _config():
    return {
        "strategy_version": "test-v1",
        "scheduler": {
            "timezone": "America/New_York",
            "stages": {
                "universe_validation": {"time": "08:20"},
                "premarket": {"time": "08:45", "evidence_cutoff": "08:45"},
                "opening_5m": {"time": "09:35", "evidence_cutoff": "09:35"},
                "opening_15m": {"time": "09:45", "evidence_cutoff": "09:45"},
            },
        },
    }


def test_repository_uses_exact_dated_layout_and_force_is_explicit(tmp_path):
    repository = RunRepository(tmp_path / "runs", "test-v1")
    repository.initialize_run("2026-07-16", _config())
    value = {
        "trade_date": "2026-07-16",
        "stage": "premarket",
        "strategy_version": "test-v1",
        "status": "COMPLETED",
        "scheduled_cutoff": "2026-07-16T08:45:00-04:00",
        "evidence_cutoff": "2026-07-16T08:45:00-04:00",
        "opening_watchlist": ["NVDA"],
    }
    repository.save_stage(value, markdown="# Premarket")

    with pytest.raises(DuplicateRunError):
        repository.save_stage(value)
    repository.save_stage(value, force=True)

    run_dir = tmp_path / "runs" / "2026-07-16"
    assert {path.name for path in run_dir.iterdir()} == {
        "run_manifest.json",
        "config_snapshot.json",
        "premarket.json",
        "premarket.md",
    }
    snapshot = json.loads((run_dir / "premarket.json").read_text(encoding="utf-8"))
    assert snapshot["run_key"] == "2026-07-16:premarket:test-v1"
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["stages"]["premarket"]["attempts"] == 2
    assert manifest["stages"]["premarket"]["forced"] is True


def test_job_lock_blocks_a_concurrent_owner(tmp_path):
    repository = RunRepository(tmp_path, "test-v1")
    first = repository.job_lock("2026-07-16", AnalysisStage.PREMARKET)
    second = repository.job_lock("2026-07-16", AnalysisStage.PREMARKET)
    assert first.acquire()
    try:
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()


def test_snapshot_loader_requires_persisted_predecessor_without_recomputation(tmp_path):
    repository = RunRepository(tmp_path, "test-v1")
    repository.initialize_run("2026-07-16", _config())
    loader = SnapshotLoader(repository)
    with pytest.raises(SnapshotNotFoundError):
        loader.load_for_stage("2026-07-16", AnalysisStage.OPENING_5M)

    repository.save_stage(
        {
            "trade_date": "2026-07-16",
            "stage": "premarket",
            "strategy_version": "test-v1",
            "status": "COMPLETED",
            "scheduled_cutoff": "2026-07-16T08:45:00-04:00",
            "evidence_cutoff": "2026-07-16T08:45:00-04:00",
            "opening_watchlist": ["NVDA"],
        }
    )
    prior = loader.load_for_stage("2026-07-16", AnalysisStage.OPENING_15M)
    assert prior["premarket"]["opening_watchlist"] == ["NVDA"]
    assert "opening_5m" not in prior


def test_snapshot_loader_rejects_post_cutoff_or_non_completed_predecessors(tmp_path):
    repository = RunRepository(tmp_path, "test-v1")
    repository.initialize_run("2026-07-16", _config())
    repository.save_stage(
        {
            "trade_date": "2026-07-16",
            "stage": "premarket",
            "strategy_version": "test-v1",
            "status": "COMPLETED",
            "scheduled_cutoff": "2026-07-16T08:45:00-04:00",
            "evidence_cutoff": "2026-07-16T09:00:00-04:00",
        }
    )

    with pytest.raises(InvalidSnapshotError, match="post-cutoff"):
        SnapshotLoader(repository).load_premarket("2026-07-16")

    payload = repository.load_stage("2026-07-16", "premarket")
    payload["evidence_cutoff"] = "2026-07-16T08:45:00-04:00"
    payload["status"] = "SKIPPED"
    repository.stage_path("2026-07-16", "premarket").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    with pytest.raises(InvalidSnapshotError, match="not completed"):
        SnapshotLoader(repository).load_premarket("2026-07-16")
