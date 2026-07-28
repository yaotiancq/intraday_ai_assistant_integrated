from __future__ import annotations

"""Deterministic scheduler decisions for the staged opening workflow."""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any

from app.models.run_models import AnalysisStage, StageContext, StageResult
from app.persistence.run_repository import DuplicateRunError, RunRepository
from app.scheduling.trading_calendar import TradingCalendar, TradingCalendarError, TradingSession


Dispatch = Callable[[AnalysisStage, str, datetime, datetime, bool], StageResult | Mapping[str, Any] | bool | None]


STAGE_ORDER = (
    AnalysisStage.UNIVERSE_VALIDATION,
    AnalysisStage.PREMARKET,
    AnalysisStage.OPENING_5M,
    AnalysisStage.OPENING_15M,
)


class SchedulerError(RuntimeError):
    """Raised for invalid scheduler configuration or dispatch results."""


class ScheduleState(str, Enum):
    NOT_DUE = "NOT_DUE"
    DUE = "DUE"
    MISSED = "MISSED"
    COMPLETE = "COMPLETE"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class ScheduledStage:
    stage: AnalysisStage
    trade_date: str
    scheduled_at: datetime
    evidence_cutoff: datetime
    latest_start: datetime
    state: ScheduleState
    late_start: bool
    completed: bool

    @property
    def run_key_stage(self) -> str:
        return self.stage.value

    def to_context(self, *, strategy_version: str, actual_started_at: datetime) -> StageContext:
        return StageContext(
            trade_date=self.trade_date,
            stage=self.stage,
            strategy_version=strategy_version,
            scheduled_cutoff=self.evidence_cutoff,
            actual_started_at=actual_started_at,
            late_start=actual_started_at > self.evidence_cutoff,
        )


@dataclass(frozen=True)
class SchedulerDecision:
    stage: AnalysisStage
    trade_date: str
    outcome: str
    reason_codes: tuple[str, ...]
    context: StageContext
    session: TradingSession

    @property
    def dispatched(self) -> bool:
        return self.outcome == "DISPATCHED"

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.context.to_dict(),
            "outcome": self.outcome,
            "reason_codes": list(self.reason_codes),
            "session": self.session.to_dict(),
        }


class MarketScheduler:
    """Evaluate configured stages and dispatch each logical run at most once.

    ``tick`` is intentionally side-effect bounded: the caller supplies the
    stage dispatcher while this class supplies timing, locks, cutoffs, skipped
    records, and durable duplicate prevention.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        repository: RunRepository,
        dispatcher: Dispatch | None = None,
        *,
        calendar: TradingCalendar | None = None,
    ) -> None:
        if not isinstance(config, Mapping):
            raise SchedulerError("market/scheduler configuration must be a mapping")
        self.market_config = deepcopy(dict(config))
        scheduler_value = config.get("scheduler", config)
        if not isinstance(scheduler_value, Mapping):
            raise SchedulerError("scheduler configuration must be a mapping")
        self.config = deepcopy(dict(scheduler_value))
        self.repository = repository
        self.dispatcher = dispatcher
        self.calendar = calendar or TradingCalendar(self.config)
        self.enabled = bool(self.config.get("enabled", True))
        self.skip_non_trading_days = bool(self.config.get("skip_non_trading_days", True))
        self.prevent_duplicate_runs = bool(self.config.get("prevent_duplicate_runs", True))
        try:
            maximum_lateness = float(self.config.get("maximum_late_start_minutes", 10))
        except (TypeError, ValueError) as exc:
            raise SchedulerError("maximum_late_start_minutes must be numeric") from exc
        if maximum_lateness < 0:
            raise SchedulerError("maximum_late_start_minutes must be non-negative")
        self.maximum_lateness = timedelta(minutes=maximum_lateness)
        stages = self.config.get("stages")
        if not isinstance(stages, Mapping):
            raise SchedulerError("scheduler.stages must be a mapping")
        self.stage_config: dict[AnalysisStage, dict[str, Any]] = {}
        for stage in STAGE_ORDER:
            value = stages.get(stage.value)
            if not isinstance(value, Mapping):
                raise SchedulerError(f"missing scheduler stage: {stage.value}")
            if "time" not in value:
                raise SchedulerError(f"scheduler stage has no time: {stage.value}")
            # Validate schedule and cutoff strings immediately.
            self.calendar.local_datetime(date(2000, 1, 3), str(value["time"]))
            self.calendar.stage_cutoff(date(2000, 1, 3), value)
            self.stage_config[stage] = deepcopy(dict(value))

        config_version = config.get("strategy_version")
        self.strategy_version = str(config_version or repository.strategy_version or "").strip()
        if not self.strategy_version:
            raise SchedulerError("strategy_version is required by the scheduler logical run key")
        if repository.strategy_version and repository.strategy_version != self.strategy_version:
            raise SchedulerError("scheduler and repository strategy versions differ")

    def schedule_for(
        self,
        now: datetime,
        *,
        stage: AnalysisStage | str,
        force: bool = False,
    ) -> ScheduledStage:
        local_now = self._local_now(now)
        parsed_stage = AnalysisStage.parse(stage) if isinstance(stage, str) else stage
        stage_config = self.stage_config[parsed_stage]
        trade_date = local_now.date().isoformat()
        scheduled_at = self.calendar.local_datetime(trade_date, str(stage_config["time"]))
        evidence_cutoff = self.calendar.stage_cutoff(trade_date, stage_config)
        latest_start = scheduled_at + self.maximum_lateness
        enabled = self.enabled and bool(stage_config.get("enabled", True))
        completed = self.repository.is_complete(trade_date, parsed_stage, self.strategy_version)
        if not enabled:
            state = ScheduleState.DISABLED
        elif local_now < scheduled_at:
            state = ScheduleState.NOT_DUE
        elif local_now > latest_start:
            # A forced rerun does not turn an old completed stage into a missed
            # run or weaken the configured lateness control.
            state = ScheduleState.COMPLETE if completed else ScheduleState.MISSED
        elif completed and self.prevent_duplicate_runs and not force:
            state = ScheduleState.COMPLETE
        else:
            state = ScheduleState.DUE
        return ScheduledStage(
            stage=parsed_stage,
            trade_date=trade_date,
            scheduled_at=scheduled_at,
            evidence_cutoff=evidence_cutoff,
            latest_start=latest_start,
            state=state,
            late_start=local_now > evidence_cutoff,
            completed=completed,
        )

    def pending_stages(self, now: datetime, *, force: bool = False) -> list[ScheduledStage]:
        """Return enabled, incomplete stages inside their allowed start window."""

        if not self.enabled:
            return []
        session = self.calendar.session(self._local_now(now))
        if self.skip_non_trading_days and not session.is_trading_day:
            return []
        return [
            item
            for stage in STAGE_ORDER
            if (item := self.schedule_for(now, stage=stage, force=force)).state == ScheduleState.DUE
        ]

    # Concise alias for polling loops.
    pending = pending_stages

    def tick(
        self,
        now: datetime,
        dispatch: Dispatch | None = None,
        *,
        force: bool = False,
        raise_on_error: bool = False,
    ) -> list[SchedulerDecision]:
        """Process currently due/missed stages and return auditable decisions."""

        if not self.enabled:
            return []
        callback = dispatch or self.dispatcher
        if callback is None:
            raise SchedulerError("tick requires a stage dispatcher")
        local_now = self._local_now(now)
        trade_date = local_now.date().isoformat()
        session = self.calendar.session(local_now)
        decisions: list[SchedulerDecision] = []

        for stage in STAGE_ORDER:
            scheduled = self.schedule_for(local_now, stage=stage, force=force)
            if scheduled.state in (ScheduleState.NOT_DUE, ScheduleState.DISABLED, ScheduleState.COMPLETE):
                continue
            context = scheduled.to_context(
                strategy_version=self.strategy_version,
                actual_started_at=local_now,
            )
            if self.skip_non_trading_days and not session.is_trading_day:
                decisions.append(
                    self._persist_skip(
                        context,
                        session,
                        reason_codes=("NON_TRADING_DAY", *session.reason_codes),
                        force=force,
                    )
                )
                continue
            if scheduled.state == ScheduleState.MISSED:
                decisions.append(
                    self._persist_skip(
                        context,
                        session,
                        reason_codes=("MAXIMUM_LATENESS_EXCEEDED",),
                        force=False,
                    )
                )
                continue

            self._ensure_initialized(trade_date)
            lock = self.repository.job_lock(trade_date, stage, self.strategy_version)
            if not lock.acquire():
                decisions.append(
                    SchedulerDecision(
                        stage=stage,
                        trade_date=trade_date,
                        outcome="LOCKED",
                        reason_codes=("CONCURRENT_RUN",),
                        context=context,
                        session=session,
                    )
                )
                continue
            try:
                if (
                    self.prevent_duplicate_runs
                    and not force
                    and self.repository.is_complete(trade_date, stage, self.strategy_version)
                ):
                    decisions.append(
                        SchedulerDecision(
                            stage=stage,
                            trade_date=trade_date,
                            outcome="DUPLICATE",
                            reason_codes=("DUPLICATE_RUN",),
                            context=context,
                            session=session,
                        )
                    )
                    continue
                before_entry = self._manifest_stage_entry(trade_date, stage)
                dispatch_result = callback(
                    stage,
                    trade_date,
                    scheduled.evidence_cutoff,
                    local_now,
                    force,
                )
                if dispatch_result is False:
                    raise SchedulerError(f"dispatcher reported failure for {context.run_key}")
                # A dispatcher is allowed to own persistence.  Compare the
                # manifest entry so a forced run is neither lost (because an
                # older result was complete) nor accidentally written twice.
                after_entry = self._manifest_stage_entry(trade_date, stage)
                dispatcher_persisted = after_entry != before_entry
                if (
                    not self.repository.is_complete(trade_date, stage, self.strategy_version)
                    or (force and not dispatcher_persisted)
                ):
                    payload = self._result_payload(dispatch_result, context)
                    self.repository.save_stage(
                        trade_date,
                        stage,
                        payload,
                        force=force,
                        strategy_version=self.strategy_version,
                    )
                decisions.append(
                    SchedulerDecision(
                        stage=stage,
                        trade_date=trade_date,
                        outcome="DISPATCHED",
                        reason_codes=(),
                        context=context,
                        session=session,
                    )
                )
            except Exception as exc:
                if raise_on_error:
                    raise
                decisions.append(
                    SchedulerDecision(
                        stage=stage,
                        trade_date=trade_date,
                        outcome="FAILED",
                        reason_codes=(f"DISPATCH_ERROR:{type(exc).__name__}",),
                        context=context,
                        session=session,
                    )
                )
            finally:
                lock.release()
        return decisions

    def _persist_skip(
        self,
        context: StageContext,
        session: TradingSession,
        *,
        reason_codes: tuple[str, ...],
        force: bool,
    ) -> SchedulerDecision:
        self._ensure_initialized(context.trade_date)
        lock = self.repository.job_lock(context.trade_date, context.stage, self.strategy_version)
        if not lock.acquire():
            return SchedulerDecision(
                stage=context.stage,
                trade_date=context.trade_date,
                outcome="LOCKED",
                reason_codes=("CONCURRENT_RUN",),
                context=context,
                session=session,
            )
        try:
            if self.repository.is_complete(context.trade_date, context.stage, self.strategy_version) and not force:
                return SchedulerDecision(
                    stage=context.stage,
                    trade_date=context.trade_date,
                    outcome="DUPLICATE",
                    reason_codes=("DUPLICATE_RUN",),
                    context=context,
                    session=session,
                )
            result = StageResult(
                context=context,
                status="SKIPPED",
                reason_codes=list(dict.fromkeys(reason_codes)),
                payload={"trading_session": session.to_dict()},
            )
            self.repository.save_stage(result, force=force)
        except DuplicateRunError:
            return SchedulerDecision(
                stage=context.stage,
                trade_date=context.trade_date,
                outcome="DUPLICATE",
                reason_codes=("DUPLICATE_RUN",),
                context=context,
                session=session,
            )
        finally:
            lock.release()
        return SchedulerDecision(
            stage=context.stage,
            trade_date=context.trade_date,
            outcome="SKIPPED",
            reason_codes=tuple(dict.fromkeys(reason_codes)),
            context=context,
            session=session,
        )

    def _ensure_initialized(self, trade_date: str) -> None:
        if self.repository.manifest_path(trade_date).is_file():
            return
        snapshot = deepcopy(self.market_config)
        snapshot.setdefault("strategy_version", self.strategy_version)
        # A scheduler-only mapping is accepted in tests and embedded under the
        # same key used by the full production configuration.
        if "scheduler" not in snapshot:
            snapshot = {
                "strategy_version": self.strategy_version,
                "scheduler": snapshot,
            }
        self.repository.initialize_run(trade_date, snapshot)

    def _result_payload(
        self,
        result: StageResult | Mapping[str, Any] | bool | None,
        context: StageContext,
    ) -> dict[str, Any]:
        if isinstance(result, StageResult):
            if result.context != context:
                raise SchedulerError("dispatcher StageResult context does not match scheduled cutoff")
            return result.to_dict()
        if result is None or result is True:
            return StageResult(context=context, status="COMPLETED").to_dict()
        if not isinstance(result, Mapping):
            raise SchedulerError("dispatcher must return StageResult, mapping, bool, or None")
        payload = deepcopy(dict(result))
        expected = context.to_dict()
        for field in (
            "trade_date",
            "stage",
            "strategy_version",
            "run_key",
            "scheduled_cutoff",
            "actual_started_at",
            "late_start",
        ):
            if field in payload and payload[field] != expected[field]:
                raise SchedulerError(f"dispatcher result has conflicting {field}")
            payload[field] = expected[field]
        payload.setdefault("status", "COMPLETED")
        payload.setdefault("reason_codes", [])
        return payload

    def _manifest_stage_entry(self, trade_date: str, stage: AnalysisStage) -> Any:
        try:
            manifest = self.repository.read_manifest(trade_date)
        except Exception:
            return None
        return deepcopy(manifest.get("stages", {}).get(stage.value))

    def _local_now(self, now: datetime) -> datetime:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise SchedulerError("scheduler tick requires a timezone-aware datetime")
        return now.astimezone(self.calendar.tz)


def stage_context(
    config: Mapping[str, Any],
    stage: AnalysisStage | str,
    trade_date: str,
    actual_started_at: datetime,
) -> StageContext:
    """Build a standalone cutoff-safe context for manual and replay runners."""

    scheduler = config.get("scheduler", config)
    if not isinstance(scheduler, Mapping):
        raise SchedulerError("scheduler configuration must be a mapping")
    stages = scheduler.get("stages")
    parsed_stage = AnalysisStage.parse(stage) if isinstance(stage, str) else stage
    if not isinstance(stages, Mapping) or not isinstance(stages.get(parsed_stage.value), Mapping):
        raise SchedulerError(f"missing scheduler stage: {parsed_stage.value}")
    calendar = TradingCalendar(scheduler)
    if actual_started_at.tzinfo is None or actual_started_at.utcoffset() is None:
        raise SchedulerError("actual_started_at must be timezone-aware")
    local_started = actual_started_at.astimezone(calendar.tz)
    cutoff = calendar.stage_cutoff(trade_date, stages[parsed_stage.value])
    version = str(config.get("strategy_version", "")).strip()
    if not version:
        raise SchedulerError("strategy_version is required")
    return StageContext(
        trade_date=trade_date,
        stage=parsed_stage,
        strategy_version=version,
        scheduled_cutoff=cutoff,
        actual_started_at=local_started,
        late_start=local_started > cutoff,
    )
