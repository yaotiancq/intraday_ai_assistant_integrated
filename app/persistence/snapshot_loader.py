from __future__ import annotations

"""Read-only access to earlier point-in-time stage snapshots."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, time
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.models.run_models import AnalysisStage
from app.persistence.run_repository import RunRepository


class SnapshotError(RuntimeError):
    """Base class for snapshot load failures."""


class SnapshotNotFoundError(SnapshotError, FileNotFoundError):
    """Raised when a required earlier point-in-time snapshot is absent."""


class InvalidSnapshotError(SnapshotError, ValueError):
    """Raised when persisted data is malformed or belongs to another run."""


class SnapshotLoader:
    """Load persisted evidence; this class never invokes data acquisition."""

    def __init__(self, repository: RunRepository) -> None:
        self.repository = repository

    def load(self, trade_date: str, stage: AnalysisStage | str) -> dict[str, Any]:
        parsed_stage = AnalysisStage.parse(stage) if isinstance(stage, str) else stage
        path = self.repository.stage_path(trade_date, parsed_stage)
        if not path.is_file():
            raise SnapshotNotFoundError(
                f"required {parsed_stage.value} snapshot is missing for {trade_date}: {path}"
            )
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except json.JSONDecodeError as exc:
            raise InvalidSnapshotError(f"snapshot is not valid JSON: {path}") from exc
        if not isinstance(value, dict):
            raise InvalidSnapshotError(f"snapshot must be a JSON object: {path}")
        if value.get("trade_date") != trade_date:
            raise InvalidSnapshotError(f"snapshot trade_date mismatch: {path}")
        if value.get("stage") != parsed_stage.value:
            raise InvalidSnapshotError(f"snapshot stage mismatch: {path}")
        config = self.repository.load_config_snapshot(trade_date)
        strategy_version = str(config.get("strategy_version", "")).strip()
        if value.get("strategy_version") != strategy_version:
            raise InvalidSnapshotError(f"snapshot strategy_version mismatch: {path}")
        expected_key = self.repository.logical_run_key(trade_date, parsed_stage, strategy_version)
        if value.get("run_key") != expected_key:
            raise InvalidSnapshotError(f"snapshot run_key mismatch: {path}")
        if str(value.get("status", "")).upper() != "COMPLETED":
            raise InvalidSnapshotError(f"snapshot is not completed: {path}")
        expected_cutoff = _expected_cutoff(config, trade_date, parsed_stage)
        scheduled_cutoff = _aware_datetime(value.get("scheduled_cutoff"), path, "scheduled_cutoff")
        if scheduled_cutoff != expected_cutoff:
            raise InvalidSnapshotError(f"snapshot scheduled_cutoff mismatch: {path}")
        if value.get("evidence_cutoff") is not None:
            evidence_cutoff = _aware_datetime(value.get("evidence_cutoff"), path, "evidence_cutoff")
            if evidence_cutoff > scheduled_cutoff:
                raise InvalidSnapshotError(f"snapshot contains post-cutoff evidence: {path}")
        return deepcopy(value)

    load_stage = load

    def load_premarket(self, trade_date: str) -> dict[str, Any]:
        return self.load(trade_date, AnalysisStage.PREMARKET)

    def load_opening_5m(self, trade_date: str, *, required: bool = True) -> dict[str, Any] | None:
        try:
            return self.load(trade_date, AnalysisStage.OPENING_5M)
        except SnapshotNotFoundError:
            if required:
                raise
            return None

    def load_for_stage(
        self,
        trade_date: str,
        stage: AnalysisStage | str,
    ) -> dict[str, dict[str, Any]]:
        """Return only persisted predecessors required by *stage*.

        The five-minute snapshot is optional for the final stage by contract;
        premarket is always required for either opening confirmation.
        """

        parsed_stage = AnalysisStage.parse(stage) if isinstance(stage, str) else stage
        if parsed_stage in (AnalysisStage.UNIVERSE_VALIDATION, AnalysisStage.PREMARKET):
            return {}
        snapshots = {AnalysisStage.PREMARKET.value: self.load_premarket(trade_date)}
        if parsed_stage == AnalysisStage.OPENING_15M:
            opening_5m = self.load_opening_5m(trade_date, required=False)
            if opening_5m is not None:
                snapshots[AnalysisStage.OPENING_5M.value] = opening_5m
        return snapshots

    # Explicit name communicates that this never recomputes absent evidence.
    load_prior_snapshots = load_for_stage


def load_snapshot(path: str | Path) -> dict[str, Any]:
    """Load a standalone JSON snapshot for replay tools."""

    snapshot_path = Path(path)
    if not snapshot_path.is_file():
        raise SnapshotNotFoundError(str(snapshot_path))
    try:
        value = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidSnapshotError(f"snapshot is not valid JSON: {snapshot_path}") from exc
    if not isinstance(value, Mapping):
        raise InvalidSnapshotError(f"snapshot must be a JSON object: {snapshot_path}")
    return deepcopy(dict(value))


def _expected_cutoff(
    config: Mapping[str, Any],
    trade_date: str,
    stage: AnalysisStage,
) -> datetime:
    scheduler = config.get("scheduler")
    if not isinstance(scheduler, Mapping):
        raise InvalidSnapshotError("configuration snapshot has no scheduler")
    stages = scheduler.get("stages")
    stage_config = stages.get(stage.value) if isinstance(stages, Mapping) else None
    if not isinstance(stage_config, Mapping):
        raise InvalidSnapshotError(f"configuration snapshot has no {stage.value} stage")
    clock = str(stage_config.get("evidence_cutoff", stage_config.get("time", "")))
    try:
        hour, minute = (int(part) for part in clock.split(":"))
        timezone = ZoneInfo(str(scheduler["timezone"]))
        return datetime.combine(date.fromisoformat(trade_date), time(hour, minute), timezone)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidSnapshotError(f"invalid configured cutoff for {stage.value}") from exc


def _aware_datetime(value: Any, path: Path, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InvalidSnapshotError(f"snapshot {field} is invalid: {path}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InvalidSnapshotError(f"snapshot {field} must be timezone-aware: {path}")
    return parsed
