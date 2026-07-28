from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from app.data_sources.market_data import DeterministicMarketDataSource
from app.models.universe_models import UniverseHealthState
from app.universe.fixed_universe import build_fixed_universe
from app.universe.universe_health_review import KEEP, POSSIBLE_REPLACEMENT, REVIEW, review_fixed_universe
from app.universe.universe_validator import UniverseValidator, validate_universe_snapshot


ET = ZoneInfo("America/New_York")


class MissingOneSymbolSource(DeterministicMarketDataSource):
    def snapshots(self, symbols, as_of):
        return [row for row in super().snapshots(symbols, as_of) if row["symbol"] != "UNH"]


def test_static_universe_validation_succeeds_for_configured_snapshot():
    universe = build_fixed_universe("2026-07-16")

    assert validate_universe_snapshot(universe) == {}


def test_daily_health_flags_unavailable_symbol_but_continues():
    universe = build_fixed_universe("2026-07-16")
    source = MissingOneSymbolSource(universe)
    report = UniverseValidator(source).validate_daily(
        universe, datetime(2026, 7, 16, 8, 20, tzinfo=ET)
    )
    by_symbol = {result.symbol: result for result in report.results}

    assert by_symbol["UNH"].state is UniverseHealthState.TEMPORARILY_UNAVAILABLE
    assert by_symbol["AAPL"].state is UniverseHealthState.ACTIVE
    assert "UNH" in report.unavailable_symbols
    assert report.configured_symbol_count == 30


def test_universe_review_is_non_mutating_and_returns_all_30_recommendations():
    universe = build_fixed_universe("2026-07-16")
    before = deepcopy(universe.to_dict())
    source = DeterministicMarketDataSource(universe)

    report = review_fixed_universe(
        universe,
        source,
        datetime(2026, 7, 16, 9, 45, tzinfo=ET),
    )

    assert universe.to_dict() == before
    assert report.configuration_mutated is False
    assert len(report.recommendations) == 30
    assert all(item.recommendation == KEEP for item in report.recommendations)


class WeakReviewSource(DeterministicMarketDataSource):
    def review(self, symbols, as_of):
        values = super().review(symbols, as_of)
        values["AAPL"]["average_daily_dollar_volume_20d"] = 10_000_000
        values["AAPL"]["data_completeness"] = 0.50
        return values


def test_universe_review_can_recommend_but_never_replace_membership():
    universe = build_fixed_universe("2026-07-16")
    report = review_fixed_universe(
        universe,
        WeakReviewSource(universe),
        datetime(2026, 7, 16, 9, 45, tzinfo=ET),
    )
    by_symbol = {item.symbol: item for item in report.recommendations}

    assert by_symbol["AAPL"].recommendation == POSSIBLE_REPLACEMENT
    assert len(universe.stock_symbols) == 30
    assert "AAPL" in universe.stock_symbols


class MissingFrequencySource(DeterministicMarketDataSource):
    def review(self, symbols, as_of):
        values = super().review(symbols, as_of)
        values["AAPL"].pop("premarket_filter_pass_frequency")
        values["AAPL"].pop("opening_watchlist_frequency")
        return values


def test_universe_review_does_not_treat_missing_frequency_history_as_perfect():
    universe = build_fixed_universe("2026-07-16")
    report = review_fixed_universe(
        universe,
        MissingFrequencySource(universe),
        datetime(2026, 7, 16, 9, 45, tzinfo=ET),
    )
    item = next(value for value in report.recommendations if value.symbol == "AAPL")

    assert item.recommendation == REVIEW
    assert "PREMARKET_FILTER_FREQUENCY_UNAVAILABLE" in item.reason_codes
    assert "OPENING_WATCHLIST_FREQUENCY_UNAVAILABLE" in item.reason_codes
