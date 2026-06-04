from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class EarningsCalendarEvent:
    symbol: str
    report_date: str
    fiscal_date_ending: str | None
    timing_bucket: str
    exact_release_time_et: str | None
    notification_time_pt: str | None
    timing_confidence: str
    eps_estimate: float | None
    eps_actual: float | None
    revenue_estimate: float | None
    revenue_actual: float | None
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreEarningsPreview:
    symbol: str
    report_date: str
    timing_bucket: str
    notification_time_pt: str | None
    timing_confidence: str
    eps_estimate: float | None
    revenue_estimate: float | None
    analyst_count: int | None
    price_target_low: float | None
    price_target_mean: float | None
    price_target_high: float | None
    rating_consensus: str | None
    historical_beat_rate: float | None
    prior_quarter_eps_surprise_pct: float | None
    expectation_risk_level: str
    warnings: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PostEarningsAnalysis:
    symbol: str
    report_date: str
    actual_eps: float | None
    estimated_eps: float | None
    eps_surprise_pct: float | None
    actual_revenue: float | None
    estimated_revenue: float | None
    revenue_surprise_pct: float | None
    result_classification: str
    warnings: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketReactionAnalysis:
    symbol: str
    report_date: str
    reference_price: float | None
    reaction_price: float | None
    reaction_pct: float | None
    reaction_session: str
    reaction_classification: str
    next_day_conditional_bias: str
    medium_term_conditional_bias: str
    confirmation_conditions: list[str] = field(default_factory=list)
    failure_conditions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PublishStateItem:
    key: str
    symbol: str
    report_date: str
    content_type: str
    content_scope: str
    content_hash: str
    last_published_at: str
    expires_at: str
    summary: str
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

