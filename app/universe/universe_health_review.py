from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.data_sources.market_data import MarketDataSource
from app.models.universe_models import FixedUniverseSnapshot


KEEP = "KEEP"
REVIEW = "REVIEW"
POSSIBLE_REPLACEMENT = "POSSIBLE_REPLACEMENT"


@dataclass(frozen=True)
class UniverseReviewRecommendation:
    symbol: str
    recommendation: str
    metrics: dict[str, Any]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "recommendation": self.recommendation,
            "metrics": deepcopy(self.metrics),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class UniverseReviewReport:
    trade_date: str
    as_of: str
    strategy_version: str
    configured_stock_count: int
    configuration_mutated: bool
    recommendations: tuple[UniverseReviewRecommendation, ...]

    def to_dict(self) -> dict[str, Any]:
        counts = {KEEP: 0, REVIEW: 0, POSSIBLE_REPLACEMENT: 0}
        for item in self.recommendations:
            counts[item.recommendation] = counts.get(item.recommendation, 0) + 1
        return {
            "trade_date": self.trade_date,
            "as_of": self.as_of,
            "strategy_version": self.strategy_version,
            "configured_stock_count": self.configured_stock_count,
            "configuration_mutated": self.configuration_mutated,
            "recommendation_counts": counts,
            "recommendations": [item.to_dict() for item in self.recommendations],
        }


class UniverseHealthReview:
    """Produce recommendations only; this class has no persistence or mutation API."""

    def __init__(
        self,
        data_source: MarketDataSource,
        *,
        minimum_average_daily_dollar_volume: float = 50_000_000.0,
        maximum_regular_spread_bps: float = 25.0,
        maximum_premarket_spread_bps: float = 35.0,
        minimum_data_completeness: float = 0.95,
        minimum_filter_pass_frequency: float = 0.10,
        minimum_opening_session_dollar_volume: float = 1_000_000.0,
        minimum_atr_percent: float = 0.30,
        maximum_atr_percent: float = 15.0,
        minimum_watchlist_frequency: float = 0.01,
    ) -> None:
        self.data_source = data_source
        self.minimum_average_daily_dollar_volume = float(minimum_average_daily_dollar_volume)
        self.maximum_regular_spread_bps = float(maximum_regular_spread_bps)
        self.maximum_premarket_spread_bps = float(maximum_premarket_spread_bps)
        self.minimum_data_completeness = float(minimum_data_completeness)
        self.minimum_filter_pass_frequency = float(minimum_filter_pass_frequency)
        self.minimum_opening_session_dollar_volume = float(minimum_opening_session_dollar_volume)
        self.minimum_atr_percent = float(minimum_atr_percent)
        self.maximum_atr_percent = float(maximum_atr_percent)
        self.minimum_watchlist_frequency = float(minimum_watchlist_frequency)

    def run(
        self,
        universe: FixedUniverseSnapshot,
        as_of: datetime | str,
    ) -> UniverseReviewReport:
        # Frozen universe models plus copied metrics ensure this code cannot
        # rewrite membership or metadata, even if a provider returns mutable maps.
        before = universe.to_dict()
        try:
            raw_metrics = self.data_source.review(universe.stock_symbols, as_of)
            metrics_by_symbol = raw_metrics if isinstance(raw_metrics, Mapping) else {}
        except Exception:
            metrics_by_symbol = {}
        recommendations = tuple(
            self._recommend(symbol, deepcopy(dict(metrics_by_symbol.get(symbol, {}))))
            for symbol in universe.stock_symbols
        )
        after = universe.to_dict()
        timestamp = as_of.isoformat() if isinstance(as_of, datetime) else str(as_of)
        return UniverseReviewReport(
            trade_date=universe.trade_date,
            as_of=timestamp,
            strategy_version=universe.strategy_version,
            configured_stock_count=len(universe.stock_symbols),
            configuration_mutated=before != after,
            recommendations=recommendations,
        )

    def _recommend(self, symbol: str, metrics: dict[str, Any]) -> UniverseReviewRecommendation:
        severe: list[str] = []
        review: list[str] = []
        adv20 = _metric(metrics, "average_daily_dollar_volume_20d", "avg_daily_dollar_volume_20d")
        adv60 = _metric(metrics, "average_daily_dollar_volume_60d", "avg_daily_dollar_volume_60d")
        completeness = _metric(metrics, "data_completeness")
        regular_spread = _metric(metrics, "median_regular_spread_bps")
        premarket_spread = _metric(metrics, "median_premarket_spread_bps")
        pass_frequency = _metric(metrics, "premarket_filter_pass_frequency", "filter_pass_frequency")
        watchlist_frequency = _metric(metrics, "opening_watchlist_frequency", "watchlist_frequency")
        atr_percent = _metric(metrics, "atr_percent", "atr_pct")
        opening_dollar_volume = _metric(
            metrics, "opening_session_dollar_volume", "average_opening_session_dollar_volume"
        )

        if not metrics:
            severe.append("REVIEW_METRICS_UNAVAILABLE")
        if completeness is not None and completeness < 0.80:
            severe.append("PERSISTENT_DATA_INCOMPLETENESS")
        elif completeness is not None and completeness < self.minimum_data_completeness:
            review.append("DATA_COMPLETENESS_BELOW_TARGET")
        if adv20 is not None and adv20 < self.minimum_average_daily_dollar_volume * 0.5:
            severe.append("20D_DOLLAR_VOLUME_SEVERELY_LOW")
        elif adv20 is not None and adv20 < self.minimum_average_daily_dollar_volume:
            review.append("20D_DOLLAR_VOLUME_BELOW_TARGET")
        if adv60 is not None and adv60 < self.minimum_average_daily_dollar_volume * 0.5:
            severe.append("60D_DOLLAR_VOLUME_SEVERELY_LOW")
        elif adv60 is not None and adv60 < self.minimum_average_daily_dollar_volume:
            review.append("60D_DOLLAR_VOLUME_BELOW_TARGET")
        if regular_spread is not None and regular_spread > self.maximum_regular_spread_bps:
            review.append("REGULAR_SPREAD_ABOVE_TARGET")
        if premarket_spread is not None and premarket_spread > self.maximum_premarket_spread_bps:
            review.append("PREMARKET_SPREAD_ABOVE_TARGET")
        if pass_frequency is None:
            review.append("PREMARKET_FILTER_FREQUENCY_UNAVAILABLE")
        elif pass_frequency < self.minimum_filter_pass_frequency:
            review.append("INFREQUENTLY_PASSES_PREMARKET_FILTERS")
        if watchlist_frequency is None:
            review.append("OPENING_WATCHLIST_FREQUENCY_UNAVAILABLE")
        elif watchlist_frequency < self.minimum_watchlist_frequency:
            review.append("INFREQUENTLY_ENTERS_OPENING_WATCHLIST")
        if opening_dollar_volume is not None and opening_dollar_volume < self.minimum_opening_session_dollar_volume:
            review.append("OPENING_DOLLAR_VOLUME_BELOW_TARGET")
        if atr_percent is not None and atr_percent < self.minimum_atr_percent:
            review.append("ATR_PERCENT_TOO_LOW")
        elif atr_percent is not None and atr_percent > self.maximum_atr_percent:
            review.append("ATR_PERCENT_TOO_HIGH")

        if severe:
            recommendation = POSSIBLE_REPLACEMENT
            reasons = severe + review
        elif review:
            recommendation = REVIEW
            reasons = review
        else:
            recommendation = KEEP
            reasons = ["MEETS_INITIAL_REVIEW_THRESHOLDS"]
        return UniverseReviewRecommendation(
            symbol=symbol,
            recommendation=recommendation,
            metrics=metrics,
            reason_codes=tuple(sorted(set(reasons))),
        )


def review_fixed_universe(
    universe: FixedUniverseSnapshot,
    data_source: MarketDataSource,
    as_of: datetime | str,
    **kwargs: Any,
) -> UniverseReviewReport:
    return UniverseHealthReview(data_source, **kwargs).run(universe, as_of)


def _metric(metrics: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        value = metrics.get(name)
        try:
            if value is not None:
                return float(value)
        except (TypeError, ValueError):
            return None
    return None
