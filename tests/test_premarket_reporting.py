from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

import pytest

from app.config import load_market_config
from app.data_sources.market_data import DeterministicMarketDataSource, SymbolNotAllowedError
from app.pipeline.premarket_pipeline import run_premarket_pipeline
from app.reporting.market_report_builder import build_premarket_markdown
from app.universe.fixed_universe import build_fixed_universe


TRADE_DATE = "2026-07-16"
MARKET_TIMEZONE = ZoneInfo("America/New_York")
OUTSIDE_SYMBOL = "ZZZZ"


class OutsideNoiseSource(DeterministicMarketDataSource):
    """Simulate a provider returning an unrequested ticker in otherwise valid data."""

    def __init__(self, universe) -> None:
        super().__init__(universe)
        self.stock_symbols = frozenset(universe.stock_symbols)

    def snapshots(self, symbols: Iterable[str], as_of):
        requested = tuple(symbols)
        rows = super().snapshots(requested, as_of)
        if frozenset(requested) == self.stock_symbols:
            rows.append(
                {
                    "symbol": OUTSIDE_SYMBOL,
                    "timestamp": as_of.isoformat(),
                    "last": 99.0,
                    "prev_close": 90.0,
                    "spread_bps": 1.0,
                }
            )
        return rows

    def news(self, symbols: Iterable[str], as_of):
        super().news(symbols, as_of)
        return [
            {
                "title": "Outside company reports earnings",
                "url": "https://www.sec.gov/example/outside-company",
                "published_at": as_of.isoformat(),
                "related_symbols": [OUTSIDE_SYMBOL],
                "sentiment": "positive",
            }
        ]


@pytest.fixture(scope="module")
def premarket_case() -> dict:
    config = load_market_config("config/market_strategy.json")
    universe = build_fixed_universe(TRADE_DATE, config)
    cutoff = datetime(2026, 7, 16, 8, 45, tzinfo=MARKET_TIMEZONE)
    source = DeterministicMarketDataSource(universe)
    payload = run_premarket_pipeline(
        trade_date=TRADE_DATE,
        as_of=cutoff,
        evidence_cutoff=cutoff,
        universe=universe,
        config=config,
        data_source=source,
    )
    return {
        "config": config,
        "universe": universe,
        "cutoff": cutoff,
        "payload": payload,
        "markdown": build_premarket_markdown(payload),
    }


def test_report_shows_all_30_fixed_universe_stocks(premarket_case: dict) -> None:
    universe = premarket_case["universe"]
    payload = premarket_case["payload"]
    markdown = premarket_case["markdown"]

    expected_symbols = list(universe.stock_symbols)
    assert len(expected_symbols) == 30
    assert payload["configured_stock_count"] == 30
    assert payload["analyzed_symbols"] == expected_symbols
    assert [item["symbol"] for item in payload["evaluated_stocks"]] == expected_symbols

    all_rows = _stock_rows(_section(markdown, "## All 30 configured stocks", "## Score breakdowns"))
    assert list(all_rows) == expected_symbols
    assert len(all_rows) == 30


def test_rejected_stocks_show_stable_reason_codes(premarket_case: dict) -> None:
    payload = premarket_case["payload"]
    all_rows = _stock_rows(
        _section(premarket_case["markdown"], "## All 30 configured stocks", "## Score breakdowns")
    )
    rejected = payload["rejected_candidates"]

    assert rejected
    assert any(not item["qualified"] for item in rejected)
    for item in rejected:
        assert item["disposition"] == "REJECTED"
        assert item["reason_codes"]
        rendered = all_rows[item["symbol"]]
        assert rendered[7] == "REJECTED"
        for reason_code in item["reason_codes"]:
            assert reason_code in rendered[8]


def test_report_renders_english_and_chinese_sector_labels(premarket_case: dict) -> None:
    evaluated = premarket_case["payload"]["evaluated_stocks"]
    all_rows = _stock_rows(
        _section(premarket_case["markdown"], "## All 30 configured stocks", "## Score breakdowns")
    )
    sector_pairs = {(item["sector"], item["sector_zh"]) for item in evaluated}

    assert len(sector_pairs) == 11
    assert all(english and chinese for english, chinese in sector_pairs)
    for item in evaluated:
        assert all_rows[item["symbol"]][2] == f'{item["sector"]} / {item["sector_zh"]}'


def test_opening_watchlist_is_a_bounded_candidate_subset(premarket_case: dict) -> None:
    config = premarket_case["config"]
    payload = premarket_case["payload"]
    selection = config["candidate_selection"]
    fixed_symbols = set(premarket_case["universe"].stock_symbols)
    candidate_symbols = {item["symbol"] for item in payload["eligible_candidates"]}
    watchlist_symbols = [item["symbol"] for item in payload["opening_watchlist"]]

    assert 0 < len(watchlist_symbols) <= selection["maximum_opening_watchlist"]
    assert len(candidate_symbols) <= selection["maximum_premarket_candidates"]
    assert set(watchlist_symbols) <= candidate_symbols <= fixed_symbols
    assert all(
        item["premarket_score"] >= selection["minimum_premarket_score"]
        for item in payload["opening_watchlist"]
    )
    sector_counts = Counter(item["sector"] for item in payload["opening_watchlist"])
    assert max(sector_counts.values()) <= selection["maximum_candidates_per_sector"]

    watchlist_section = _section(
        premarket_case["markdown"], "## Opening watchlist", "## All 30 configured stocks"
    )
    rendered_symbols = [
        cells[1]
        for line in watchlist_section.splitlines()
        if (cells := _cells(line)) and cells[0].isdigit()
    ]
    assert rendered_symbols == watchlist_symbols


def test_unconfigured_symbol_cannot_enter_pipeline_or_report() -> None:
    config = load_market_config("config/market_strategy.json")
    universe = build_fixed_universe(TRADE_DATE, config)
    cutoff = datetime(2026, 7, 16, 8, 45, tzinfo=MARKET_TIMEZONE)
    source = OutsideNoiseSource(universe)

    payload = run_premarket_pipeline(
        trade_date=TRADE_DATE,
        as_of=cutoff,
        evidence_cutoff=cutoff,
        universe=universe,
        config=config,
        data_source=source,
    )
    markdown = build_premarket_markdown(payload)

    assert payload["outside_symbols_analyzed"] == []
    for key in ("evaluated_stocks", "eligible_candidates", "opening_watchlist", "rejected_candidates"):
        assert OUTSIDE_SYMBOL not in {item["symbol"] for item in payload[key]}
    assert OUTSIDE_SYMBOL not in payload["analyzed_symbols"]
    assert OUTSIDE_SYMBOL not in markdown
    with pytest.raises(SymbolNotAllowedError, match=OUTSIDE_SYMBOL):
        source.snapshots([OUTSIDE_SYMBOL], cutoff)


def _section(markdown: str, heading: str, next_heading: str) -> str:
    return markdown.split(heading, 1)[1].split(next_heading, 1)[0]


def _stock_rows(section: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        cells = _cells(line)
        if len(cells) == 9 and cells[0] not in {"Symbol", "---"}:
            rows[cells[0]] = cells
    return rows


def _cells(line: str) -> list[str]:
    if not line.startswith("|"):
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]
