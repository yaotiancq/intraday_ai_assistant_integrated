from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.models.run_models import AnalysisStage
from app.pipeline.scheduled_pipeline import MarketAnalysisService


@dataclass
class StageDispatcher:
    """Thin adapter keeping scheduler and manual runners on one pipeline."""

    service: MarketAnalysisService

    def dispatch(
        self,
        stage: AnalysisStage,
        trade_date: str,
        evidence_cutoff: datetime,
        actual_started_at: datetime,
        force: bool = False,
    ) -> dict[str, Any]:
        return self.service.scheduler_dispatch(stage, trade_date, evidence_cutoff, actual_started_at, force)

    __call__ = dispatch

