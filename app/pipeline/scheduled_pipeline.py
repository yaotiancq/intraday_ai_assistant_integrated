from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Callable, Mapping

from app.data_sources.market_data import MarketDataSource
from app.models.run_models import AnalysisStage, StageContext
from app.persistence.run_repository import DuplicateRunError, RunRepository
from app.persistence.snapshot_loader import SnapshotLoader
from app.pipeline.opening_confirmation_pipeline import run_opening_confirmation_pipeline
from app.pipeline.premarket_pipeline import run_premarket_pipeline
from app.reporting.market_report_builder import (
    build_opening_markdown,
    build_premarket_markdown,
    build_universe_validation_markdown,
)
from app.scheduling.job_lock import LockUnavailable
from app.scheduling.trading_calendar import TradingCalendar
from app.universe import build_fixed_universe, validate_universe_health


NotificationHook = Callable[[AnalysisStage, Mapping[str, Any], str, str], None]


class MarketAnalysisService:
    """Shared staged runner used by manual, replay, and scheduled execution."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any],
        data_source: MarketDataSource,
        repository: RunRepository,
        calendar: TradingCalendar | None = None,
        notification_hook: NotificationHook | None = None,
    ) -> None:
        self.config = deepcopy(dict(config))
        self.data_source = data_source
        self.repository = repository
        self.calendar = calendar or TradingCalendar(self.config["scheduler"])
        self.snapshots = SnapshotLoader(repository)
        self.notification_hook = notification_hook

    def run_stage(
        self,
        stage: AnalysisStage | str,
        trade_date: str,
        evidence_cutoff: datetime,
        actual_started_at: datetime,
        force: bool = False,
        *,
        acquire_lock: bool = True,
        allow_non_trading_day: bool = False,
    ) -> dict[str, Any]:
        parsed_stage = AnalysisStage.parse(stage) if isinstance(stage, str) else stage
        context = StageContext(
            trade_date=trade_date,
            stage=parsed_stage,
            strategy_version=str(self.config["strategy_version"]),
            scheduled_cutoff=evidence_cutoff,
            actual_started_at=actual_started_at.astimezone(self.calendar.tz),
            late_start=actual_started_at.astimezone(self.calendar.tz) > evidence_cutoff,
        )
        self.repository.initialize_run(trade_date, self.config)
        if self.repository.is_complete(trade_date, parsed_stage, context.strategy_version) and not force:
            raise DuplicateRunError(f"logical run already completed: {context.run_key}")

        session = self.calendar.session(trade_date)
        if not session.is_trading_day and not allow_non_trading_day:
            payload = {
                **context.to_dict(),
                "status": "SKIPPED",
                "reason_codes": ["NON_TRADING_DAY", *session.reason_codes],
                "trading_session": session.to_dict(),
            }
            self.repository.save_stage(trade_date, parsed_stage, payload, force=force)
            return payload

        lock = self.repository.job_lock(trade_date, parsed_stage, context.strategy_version)
        if acquire_lock and not lock.acquire():
            raise LockUnavailable(f"concurrent logical run: {context.run_key}")
        try:
            payload, markdown, final_report = self._execute(context)
            payload = {
                **payload,
                **context.to_dict(),
                "status": payload.get("status", "COMPLETED"),
                "trading_session": session.to_dict(),
            }
            if final_report is not None:
                final_report = deepcopy(payload)
            destination = self.repository.save_stage(
                trade_date,
                parsed_stage,
                payload,
                markdown=markdown,
                final_report=final_report,
                force=force,
                strategy_version=context.strategy_version,
            )
        finally:
            if acquire_lock:
                lock.release()

        if self.notification_hook is not None:
            try:
                self.notification_hook(parsed_stage, payload, markdown, str(destination))
            except Exception as exc:
                # Persistence is the source of truth; notification failure does
                # not roll back or invalidate the analysis snapshot.
                payload["notification_status"] = "FAILED"
                payload["notification_error"] = _safe_error(exc)
        return payload

    def scheduler_dispatch(
        self,
        stage: AnalysisStage,
        trade_date: str,
        evidence_cutoff: datetime,
        actual_started_at: datetime,
        force: bool,
    ) -> dict[str, Any]:
        return self.run_stage(
            stage,
            trade_date,
            evidence_cutoff,
            actual_started_at,
            force,
            acquire_lock=False,
        )

    def _execute(self, context: StageContext) -> tuple[dict[str, Any], str, Mapping[str, Any] | None]:
        universe = build_fixed_universe(context.trade_date, self.config)
        if context.stage is AnalysisStage.UNIVERSE_VALIDATION:
            report = validate_universe_health(
                universe,
                self.data_source,
                context.scheduled_cutoff,
                maximum_quote_age_seconds=int(self.config["risk_gates"]["maximum_data_age_seconds"]),
            ).to_dict()
            payload = _enrich_universe_report(report, universe)
            return payload, build_universe_validation_markdown(payload), None

        if context.stage is AnalysisStage.PREMARKET:
            try:
                health = self.snapshots.load(context.trade_date, AnalysisStage.UNIVERSE_VALIDATION)
            except Exception:
                health = validate_universe_health(
                    universe,
                    self.data_source,
                    context.scheduled_cutoff,
                    maximum_quote_age_seconds=int(self.config["risk_gates"]["maximum_data_age_seconds"]),
                ).to_dict()
                health.setdefault("data_quality_warnings", []).append("UNIVERSE_VALIDATION_SNAPSHOT_MISSING")
            payload = run_premarket_pipeline(
                trade_date=context.trade_date,
                as_of=context.scheduled_cutoff,
                evidence_cutoff=context.scheduled_cutoff,
                universe=universe,
                config=self.config,
                data_source=self.data_source,
                universe_validation=health,
            )
            return payload, build_premarket_markdown(payload), None

        predecessors = self.snapshots.load_for_stage(context.trade_date, context.stage)
        premarket = predecessors[AnalysisStage.PREMARKET.value]
        early = predecessors.get(AnalysisStage.OPENING_5M.value)
        payload = run_opening_confirmation_pipeline(
            trade_date=context.trade_date,
            as_of=context.scheduled_cutoff,
            evidence_cutoff=context.scheduled_cutoff,
            stage=context.stage.value,
            universe=universe,
            config=self.config,
            data_source=self.data_source,
            premarket_snapshot=premarket,
            opening_5m_snapshot=early,
        )
        is_final = context.stage is AnalysisStage.OPENING_15M
        markdown = build_opening_markdown(payload, final=is_final)
        return payload, markdown, payload if is_final else None


def _enrich_universe_report(report: Mapping[str, Any], universe: Any) -> dict[str, Any]:
    result = deepcopy(dict(report))
    health = {str(item.get("symbol", "")).upper(): item for item in result.get("results", [])}
    symbols: list[dict[str, Any]] = []
    for stock in universe.stocks:
        state = health.get(stock.symbol, {"state": "TEMPORARILY_UNAVAILABLE", "reason_codes": ["HEALTH_RESULT_MISSING"]})
        symbols.append({
            **stock.to_dict(),
            "state": state.get("state"),
            "reason_codes": list(state.get("reason_codes", [])),
            "warnings": list(state.get("warnings", [])),
        })
    distribution: list[dict[str, Any]] = []
    for sector in sorted({stock.sector for stock in universe.stocks}):
        members = [stock for stock in universe.stocks if stock.sector == sector]
        distribution.append({"sector": sector, "sector_zh": members[0].sector_zh, "count": len(members)})
    etf_count = sum(benchmark.category != "volatility" for benchmark in universe.benchmarks)
    result.update({
        "status": "COMPLETED",
        "stage": AnalysisStage.UNIVERSE_VALIDATION.value,
        "strategy_version": universe.strategy_version,
        "configured_symbol_count": len(universe.stock_symbols),
        "unique_symbol_count": len(set(universe.stock_symbols)),
        "benchmark_count": etf_count,
        "benchmark_proxy_count": len(universe.benchmark_symbols),
        "duplicate_symbols": [],
        "sector_distribution": distribution,
        "symbols": symbols,
        "reason_codes": ["UNAVAILABLE_SYMBOLS_PRESENT"] if result.get("unavailable_symbols") else ["UNIVERSE_VALID"],
    })
    return result


def _safe_error(exc: Exception) -> str:
    return (str(exc).replace("\n", " ").strip() or exc.__class__.__name__)[:160]
