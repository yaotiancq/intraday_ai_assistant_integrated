from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.earnings_system.alpha_vantage_news import AlphaVantageNewsProvider, normalize_alpha_vantage_news
from app.earnings_system.candidate_filter import filter_candidates
from app.earnings_system.config import EarningsConfig
from app.earnings_system.earnings_calendar_scanner import normalize_earnings_calendar_event
from app.earnings_system.fmp_client import FMPClient
from app.earnings_system.market_reaction_analyzer import classify_market_reaction
from app.earnings_system.media_update_analyzer import (
    MediaUpdate,
    aggregate_news_sentiment,
    compute_earnings_relevance_score,
    fetch_media_updates,
    label_aggregate_sentiment,
    select_relevant_media_updates,
)
from app.earnings_system.models import PreEarningsPreview
from app.earnings_system.notification_formatter import format_media_digest, format_pre_earnings_preview
from app.earnings_system.post_earnings_analyzer import classify_actual_vs_estimate
from app.earnings_system.pre_earnings_analyzer import has_meaningful_consensus_change
from app.earnings_system.publish_state import (
    build_publish_key,
    cleanup_expired_items,
    compute_content_hash,
    load_publish_state,
    make_publish_item,
    mark_published,
    should_publish,
)
from app.earnings_system.workflow import run_earnings_workflow


def test_fmp_response_normalization_accepts_alternative_fields():
    event = normalize_earnings_calendar_event({
        "ticker": "US.NVDA",
        "reportDate": "2026-07-30",
        "fiscalDateEnding": "2026-06-30",
        "estimatedEps": "1.25",
        "actualEps": "1.30",
        "estimatedRevenue": "30000000000",
        "actualRevenue": "32000000000",
        "timing": "BMO",
    })

    assert event.symbol == "NVDA"
    assert event.report_date == "2026-07-30"
    assert event.fiscal_date_ending == "2026-06-30"
    assert event.eps_estimate == 1.25
    assert event.eps_actual == 1.30
    assert event.revenue_estimate == 30_000_000_000
    assert event.revenue_actual == 32_000_000_000


def test_missing_fields_are_warned_and_continue():
    event = normalize_earnings_calendar_event({"date": "2026-07-30"})

    assert event.symbol == "UNKNOWN"
    assert "missing symbol" in event.warnings


def test_bmo_timing_inference_does_not_invent_exact_time():
    event = normalize_earnings_calendar_event({"symbol": "AAPL", "date": "2026-07-30", "time": "bmo"})

    assert event.timing_bucket == "bmo"
    assert event.exact_release_time_et is None
    assert event.notification_time_pt.startswith("2026-07-30T04:00:00")
    assert event.timing_confidence == "inferred_bucket"


def test_amc_timing_inference_does_not_invent_exact_time():
    event = normalize_earnings_calendar_event({"symbol": "MSFT", "date": "2026-07-30", "time": "AMC"})

    assert event.timing_bucket == "amc"
    assert event.exact_release_time_et is None
    assert event.notification_time_pt.startswith("2026-07-30T12:45:00")
    assert event.timing_confidence == "inferred_bucket"


def test_exact_datetime_handling():
    event = normalize_earnings_calendar_event({
        "symbol": "META",
        "date": "2026-07-30",
        "releaseTime": "2026-07-30 16:05:00",
    })

    assert event.exact_release_time_et is not None
    assert event.timing_confidence == "exact"
    assert event.timing_bucket == "amc"


def test_candidate_filter_watchlist_and_limit():
    events = [
        normalize_earnings_calendar_event({"symbol": "AAPL", "date": "2026-07-30"}),
        normalize_earnings_calendar_event({"symbol": "MSFT", "date": "2026-07-30"}),
        normalize_earnings_calendar_event({"symbol": "TSLA", "date": "2026-07-30"}),
    ]

    selected = filter_candidates(
        events,
        universe_mode="watchlist_only",
        watchlist_symbols=["US.MSFT", "TSLA"],
        max_candidates=1,
    )

    assert [x.symbol for x in selected] == ["MSFT"]


def test_pre_earnings_content_hash_stability_ignores_order_and_raw():
    a = {"symbol": "AAPL", "raw": {"x": 1}, "eps_estimate": 1.23, "generated_at": "one"}
    b = {"generated_at": "two", "eps_estimate": 1.23, "symbol": "AAPL", "raw": {"x": 2}}

    assert compute_content_hash(a) == compute_content_hash(b)


def test_should_publish_true_for_new_content():
    state = load_publish_state(Path("/tmp/does-not-exist-earnings-state.json"))

    assert should_publish(state, "AAPL|2026-07-30|pre|amc", "hash1") is True


def test_should_publish_false_for_unchanged_content():
    state = {"version": 1, "items": {}}
    item = make_publish_item(
        key="AAPL|2026-07-30|pre|amc",
        symbol="AAPL",
        report_date="2026-07-30",
        content_type="pre",
        content_scope="amc",
        content_hash="hash1",
        summary="summary",
        ttl_days=14,
    )
    mark_published(state, item)

    assert should_publish(state, item.key, "hash1") is False


def test_should_publish_true_when_meaningful_fields_change():
    previous = {"eps_estimate": 1.00, "revenue_estimate": 100.0, "analyst_count": 10, "rating_consensus": "buy"}
    current = {"eps_estimate": 1.02, "revenue_estimate": 100.0, "analyst_count": 10, "rating_consensus": "buy"}

    assert has_meaningful_consensus_change(previous, current) is True


def test_expired_state_cleanup():
    now = datetime(2026, 6, 4, tzinfo=timezone.utc)
    state = {
        "version": 1,
        "items": {
            "old": {"expires_at": (now - timedelta(days=1)).isoformat()},
            "new": {"expires_at": (now + timedelta(days=1)).isoformat()},
        },
    }

    cleanup_expired_items(state, now=now)

    assert "old" not in state["items"]
    assert "new" in state["items"]


def test_actual_vs_estimate_classification():
    assert classify_actual_vs_estimate(12.0, 8.0) == "strong_beat"
    assert classify_actual_vs_estimate(2.0, -1.0) == "mixed"
    assert classify_actual_vs_estimate(-10.0, -9.0) == "strong_miss"
    assert classify_actual_vs_estimate(None, None) == "unavailable"


def test_market_reaction_classification():
    assert classify_market_reaction(6.0) == "strong_positive"
    assert classify_market_reaction(2.0) == "positive"
    assert classify_market_reaction(0.5) == "neutral"
    assert classify_market_reaction(-2.0) == "negative"
    assert classify_market_reaction(None) == "unavailable"


class FakeResponse:
    def __init__(self, status_code, text="[]", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


class CountingSession:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def get(self, *args, **kwargs):
        self.calls += 1
        return self.response


def test_fmp_client_does_not_retry_non_retryable_4xx():
    session = CountingSession(FakeResponse(404, "[]"))
    client = FMPClient(api_key="test", retry_count=3, throttle_seconds=0, session=session)

    assert client.safe_get("profile", {"symbol": "AAPL"}, log_errors=False) is None
    assert session.calls == 1


def test_alpha_vantage_news_parsing():
    updates = normalize_alpha_vantage_news(
        [
            {
                "title": "DocuSign earnings preview",
                "source": "Example",
                "url": "https://example.com/docu",
                "time_published": "20260604T120000",
                "summary": "Analysts preview revenue and EPS estimates.",
                "overall_sentiment_score": "0.18",
                "overall_sentiment_label": "Somewhat-Bullish",
                "ticker_sentiment": [
                    {
                        "ticker": "DOCU",
                        "relevance_score": "0.92",
                        "ticker_sentiment_score": "0.31",
                        "ticker_sentiment_label": "Bullish",
                    },
                    {
                        "ticker": "CRM",
                        "relevance_score": "0.10",
                        "ticker_sentiment_score": "0.02",
                        "ticker_sentiment_label": "Neutral",
                    },
                ],
            },
            {
                "title": "DocuSign earnings preview",
                "source": "Example",
                "url": "https://example.com/docu",
                "time_published": "20260604T120000",
            },
        ],
        symbol="DOCU",
        report_date="2026-06-04",
    )

    assert len(updates) == 1
    assert updates[0].symbol == "DOCU"
    assert updates[0].published_at == "2026-06-04T12:00:00"
    assert updates[0].overall_sentiment_score == 0.18
    assert updates[0].overall_sentiment_label == "Somewhat-Bullish"
    assert updates[0].ticker_sentiment_score == 0.31
    assert updates[0].ticker_sentiment_label == "Bullish"
    assert updates[0].relevance_score == 0.92
    assert updates[0].ticker_relevance_score == 0.92
    assert updates[0].earnings_relevance_score is not None
    assert 0 < updates[0].earnings_relevance_score < 1.0


def test_alpha_vantage_failure_fallback():
    class FailingProvider:
        def fetch_news(self, **kwargs):
            raise RuntimeError("rate limit")

    event = normalize_earnings_calendar_event({"symbol": "AAPL", "date": "2026-06-04"})
    updates, warnings = fetch_media_updates(FailingProvider(), event)

    assert updates == []
    assert any("Alpha Vantage news unavailable" in warning for warning in warnings)


def test_alpha_vantage_provider_builds_news_sentiment_request():
    calls = []

    class Session:
        def get(self, url, params=None, timeout=None):
            calls.append((url, params, timeout))
            return FakeResponse(
                200,
                payload={
                    "feed": [
                        {
                            "title": "AAPL earnings watch",
                            "source": "Example",
                            "url": "https://example.com/aapl",
                            "time_published": "20260604T120000",
                        }
                    ]
                },
            )

    provider = AlphaVantageNewsProvider(
        api_key="av-test",
        retry_count=0,
        throttle_seconds=0,
        limit=20,
        session=Session(),
    )
    updates = provider.fetch_news(
        symbol="AAPL",
        report_date="2026-06-04",
        time_from=datetime(2026, 6, 1, 0, 0),
        time_to=datetime(2026, 6, 7, 23, 59),
        topics="earnings",
    )

    params = calls[0][1]
    assert params["function"] == "NEWS_SENTIMENT"
    assert params["tickers"] == "AAPL"
    assert params["topics"] == "earnings"
    assert params["time_from"] == "20260601T0000"
    assert params["time_to"] == "20260607T2359"
    assert params["sort"] == "LATEST"
    assert params["limit"] == 20
    assert updates[0].title == "AAPL earnings watch"


def test_earnings_relevance_is_not_raw_ticker_relevance():
    strong = MediaUpdate(
        symbol="DOCU",
        report_date="2026-06-04",
        title="DocuSign Q1 earnings preview: revenue and EPS estimates",
        source="Example",
        url="https://example.com/strong",
        published_at="2026-06-04T12:00:00",
        summary="DOCU earnings preview",
        description="Analysts discuss consensus estimates, guidance, revenue, and EPS.",
        relevance_score=1.0,
        ticker_relevance_score=1.0,
    )
    weak = MediaUpdate(
        symbol="DOCU",
        report_date="2026-06-04",
        title="DocuSign insider selling update",
        source="Example",
        url="https://example.com/weak",
        published_at="2026-06-04T12:00:00",
        summary="DOCU insider selling update",
        description="Insider selling disclosure unrelated to quarterly results.",
        relevance_score=1.0,
        ticker_relevance_score=1.0,
    )

    assert compute_earnings_relevance_score(strong) > compute_earnings_relevance_score(weak)
    assert compute_earnings_relevance_score(weak) < 0.5


def test_earnings_preview_ranks_above_insider_sale():
    selected = select_relevant_media_updates(
        [
            _dated_media("DocuSign insider selling update", published_at="2026-06-03T12:00:00"),
            _dated_media(
                "DocuSign Q1 earnings preview: EPS and revenue estimates",
                published_at="2026-06-03T13:00:00",
                description="Analysts discuss consensus estimates before the report.",
            ),
        ],
        max_items=2,
    )

    assert selected[0].title.startswith("DocuSign Q1 earnings preview")
    assert all("insider selling" not in item.title.lower() for item in selected)


def test_earnings_result_ranks_above_product_announcement():
    selected = select_relevant_media_updates(
        [
            _dated_media("DocuSign announces new AI product", published_at="2026-06-05T12:00:00"),
            _dated_media(
                "DocuSign earnings results beat revenue estimates",
                published_at="2026-06-05T13:00:00",
                description="Shares react after the earnings report.",
            ),
        ],
        max_items=2,
    )

    assert selected[0].title.startswith("DocuSign earnings results")
    assert all("AI product" not in item.title for item in selected)


def test_low_ticker_relevance_is_filtered_out():
    selected = select_relevant_media_updates(
        [
            _dated_media(
                "DocuSign Q1 earnings preview: EPS and revenue estimates",
                published_at="2026-06-03T12:00:00",
                ticker_relevance=0.10,
            ),
            _dated_media(
                "DocuSign Q1 earnings preview: analyst consensus",
                published_at="2026-06-03T13:00:00",
                ticker_relevance=0.80,
            ),
        ],
        max_items=3,
    )

    assert len(selected) == 1
    assert selected[0].ticker_relevance_score == 0.80


def test_timing_boost_works_before_and_after_report_date():
    timely_preview = _dated_media(
        "DocuSign Q1 earnings preview: analyst EPS estimates",
        published_at="2026-06-03T12:00:00",
    )
    old_preview = _dated_media(
        "DocuSign Q1 earnings preview: analyst EPS estimates",
        published_at="2026-05-15T12:00:00",
    )
    timely_result = _dated_media(
        "DocuSign earnings results beat revenue estimates",
        published_at="2026-06-05T12:00:00",
    )
    early_result = _dated_media(
        "DocuSign earnings results beat revenue estimates",
        published_at="2026-05-25T12:00:00",
    )

    assert compute_earnings_relevance_score(timely_preview) > compute_earnings_relevance_score(old_preview)
    assert compute_earnings_relevance_score(timely_result) > compute_earnings_relevance_score(early_result)


def test_duplicate_and_reposted_articles_are_removed():
    selected = select_relevant_media_updates(
        [
            _dated_media(
                "DocuSign Q1 earnings preview: EPS and revenue estimates",
                published_at="2026-06-03T12:00:00",
                source="MSN",
                url="https://msn.com/reposted-docu",
            ),
            _dated_media(
                "DOCU Stock: DocuSign Q1 earnings preview EPS revenue estimates",
                published_at="2026-06-03T12:05:00",
                source="Reuters",
                url="https://reuters.com/original-docu",
            ),
        ],
        max_items=3,
    )

    assert len(selected) == 1
    assert selected[0].source == "Reuters"


def test_fallback_behavior_when_no_highly_relevant_articles_exist():
    selected = select_relevant_media_updates(
        [
            _dated_media("DocuSign quarterly report filed", published_at="2026-06-03T12:00:00"),
            _dated_media("DocuSign stock update before annual meeting", published_at="2026-06-03T13:00:00"),
            _dated_media("DocuSign shareholder proposal results", published_at="2026-06-03T14:00:00"),
        ],
        max_items=3,
    )

    assert 1 <= len(selected) <= 2
    assert all(item.low_earnings_relevance for item in selected)
    assert all(item.earnings_relevance_reason == "low relevance fallback" for item in selected)


def test_sentiment_does_not_inflate_earnings_relevance():
    positive_product = _dated_media("DocuSign launches AI product", published_at="2026-06-03T12:00:00")
    positive_product.ticker_sentiment_score = 0.95
    positive_product.ticker_sentiment_label = "Bullish"
    neutral_preview = _dated_media(
        "DocuSign Q1 earnings preview: EPS and revenue estimates",
        published_at="2026-06-03T12:00:00",
    )
    neutral_preview.ticker_sentiment_score = 0.0
    neutral_preview.ticker_sentiment_label = "Neutral"

    assert compute_earnings_relevance_score(neutral_preview) > compute_earnings_relevance_score(positive_product)


def _media(title: str, description: str | None = None) -> MediaUpdate:
    return MediaUpdate(
        symbol="DOCU",
        report_date="2026-06-04",
        title=title,
        source="Example",
        url=f"https://example.com/{abs(hash(title))}",
        published_at="2026-06-04T12:00:00",
        summary=f"DOCU: {title}",
        description=description,
    )


def _dated_media(
    title: str,
    *,
    published_at: str,
    report_date: str = "2026-06-04",
    description: str | None = None,
    ticker_relevance: float = 1.0,
    source: str = "Example",
    url: str | None = None,
) -> MediaUpdate:
    return MediaUpdate(
        symbol="DOCU",
        report_date=report_date,
        title=title,
        source=source,
        url=url or f"https://example.com/{abs(hash(title + published_at))}",
        published_at=published_at,
        summary=f"DOCU: {title}",
        description=description,
        relevance_score=ticker_relevance,
        ticker_relevance_score=ticker_relevance,
    )


def _sentiment_media(
    title: str,
    *,
    ticker_score=None,
    overall_score=None,
    relevance=None,
    earnings_relevance=None,
    ticker_label=None,
    overall_label=None,
) -> MediaUpdate:
    update = _media(title, "Earnings-related article summary.")
    update.ticker_sentiment_score = ticker_score
    update.overall_sentiment_score = overall_score
    update.relevance_score = relevance
    update.ticker_relevance_score = relevance
    update.earnings_relevance_score = earnings_relevance
    update.ticker_sentiment_label = ticker_label
    update.overall_sentiment_label = overall_label
    return update


def test_weighted_average_sentiment_calculation():
    summary = aggregate_news_sentiment([
        _sentiment_media("DocuSign Q1 earnings preview", ticker_score=0.30, relevance=1.00, earnings_relevance=0.80),
        _sentiment_media("DocuSign revenue guidance report", overall_score=-0.10, relevance=1.00, earnings_relevance=0.20),
    ])

    assert round(summary["score"], 2) == 0.22
    assert summary["label"] == "Slightly Bullish"
    assert summary["sentiment_item_count"] == 2
    assert summary["mixed"] is True


def test_aggregate_sentiment_label_mapping():
    assert label_aggregate_sentiment(0.25) == "Bullish"
    assert label_aggregate_sentiment(0.05) == "Slightly Bullish"
    assert label_aggregate_sentiment(0.0) == "Neutral"
    assert label_aggregate_sentiment(-0.05) == "Slightly Bearish"
    assert label_aggregate_sentiment(-0.25) == "Bearish"
    assert label_aggregate_sentiment(None) == "Unavailable"


def test_digest_output_includes_sentiment_summary():
    digest = format_media_digest(
        "DOCU",
        "2026-06-04",
        [
            _sentiment_media(
                "DocuSign Q1 earnings preview",
                ticker_score=0.30,
                relevance=1.00,
                earnings_relevance=0.80,
                ticker_label="Bullish",
            ),
            _sentiment_media(
                "DocuSign revenue guidance report",
                ticker_score=-0.10,
                relevance=1.00,
                earnings_relevance=0.20,
                ticker_label="Somewhat-Bearish",
            ),
        ],
    )

    assert "Earnings News Sentiment" in digest
    assert "Aggregate tone: `Slightly Bullish`" in digest
    assert "Ticker sentiment: `Bullish` score `0.30` | earnings relevance `0.80` | ticker relevance `1.00`" in digest
    assert "should not be treated as a trading signal by itself" in digest


def test_missing_sentiment_fields_do_not_break_media_fetch():
    class NoSentimentProvider:
        def fetch_news(self, **kwargs):
            return [_media("DocuSign Q1 earnings preview")]

    event = normalize_earnings_calendar_event({"symbol": "DOCU", "date": "2026-06-04"})
    updates, warnings = fetch_media_updates(NoSentimentProvider(), event)

    assert len(updates) == 1
    assert any("returned no sentiment fields" in warning for warning in warnings)


def test_weakly_related_news_is_filtered_and_deprioritized():
    selected = select_relevant_media_updates(
        [
            _media("DocuSign insider selling picks up"),
            _media("DocuSign Q1 earnings preview: revenue and EPS estimates"),
            _media("DocuSign launches unrelated AI product"),
        ],
        max_items=3,
    )

    assert [item.title for item in selected] == ["DocuSign Q1 earnings preview: revenue and EPS estimates"]


def test_max_news_items_per_symbol_is_enforced():
    selected = select_relevant_media_updates(
        [
            _media("DocuSign Q1 earnings preview"),
            _media("DocuSign revenue guidance analysis"),
            _media("DocuSign analyst EPS estimate update"),
            _media("DocuSign consensus results report"),
        ],
        max_items=2,
    )

    assert len(selected) == 2


def test_notification_formatting_includes_conditional_context():
    preview = PreEarningsPreview(
        symbol="AAPL",
        report_date="2026-07-30",
        timing_bucket="amc",
        notification_time_pt="2026-07-30T12:45:00-07:00",
        timing_confidence="inferred_bucket",
        eps_estimate=1.2,
        revenue_estimate=100_000_000_000,
        analyst_count=20,
        price_target_low=150,
        price_target_mean=200,
        price_target_high=250,
        rating_consensus="buy",
        historical_beat_rate=0.75,
        prior_quarter_eps_surprise_pct=5.0,
        expectation_risk_level="elevated_expectations",
    )

    message = format_pre_earnings_preview(preview)

    assert "Pre-earnings consensus: AAPL" in message
    assert "bullish continuation watch only if" in message
    assert "sell-the-news risk if" in message


class FakeFMPClient:
    def get(self, endpoint, params=None):
        assert endpoint == "earnings-calendar"
        return [
            {"symbol": "AAPL", "date": "2026-06-04", "time": "AMC", "epsEstimated": 1.0},
            {"symbol": "FAIL", "date": "2026-06-04", "time": "AMC", "epsEstimated": 1.0},
        ]

    def safe_get(self, endpoint, params=None, **kwargs):
        symbol = (params or {}).get("symbol") or (params or {}).get("symbols")
        if symbol == "FAIL":
            raise RuntimeError("symbol-specific failure")
        if endpoint == "earnings":
            return [{"date": "2026-06-04", "epsActual": 1.2, "epsEstimated": 1.0}]
        if endpoint == "analyst-estimates":
            return [{"date": "2026-06-04", "epsAvg": 1.0, "revenueAvg": 100.0, "analystCount": 5}]
        if endpoint == "price-target-summary":
            return {"rating": "Buy"}
        if endpoint == "price-target-consensus":
            return {"targetLow": 100, "targetMean": 120, "targetHigh": 140}
        if endpoint == "quote":
            return [{"price": 102.0, "previousClose": 100.0}]
        return None


class FakeNewsProvider:
    def fetch_news(self, **kwargs):
        symbol = kwargs["symbol"]
        if symbol == "FAIL":
            raise RuntimeError("Alpha Vantage rate limit")
        return normalize_alpha_vantage_news(
            [{
                "title": f"{symbol} earnings preview",
                "source": "Example",
                "url": f"https://example.com/{symbol.lower()}",
                "time_published": "20260604T120000",
                "overall_sentiment_score": "0.12",
                "overall_sentiment_label": "Somewhat-Bullish",
                "ticker_sentiment": [{
                    "ticker": symbol,
                    "relevance_score": "0.90",
                    "ticker_sentiment_score": "0.20",
                    "ticker_sentiment_label": "Somewhat-Bullish",
                }],
            }],
            symbol=symbol,
            report_date=kwargs["report_date"],
        )


class MultiNewsProvider:
    def __init__(self, titles):
        self.titles = titles

    def fetch_news(self, **kwargs):
        return [
            MediaUpdate(
                symbol=kwargs["symbol"],
                report_date=kwargs["report_date"],
                title=title,
                source="Example",
                url=f"https://example.com/{index}",
                published_at="2026-06-04T12:00:00",
                summary=f"{kwargs['symbol']}: {title}",
                description="Earnings-related article summary.",
                overall_sentiment_score=0.10,
                overall_sentiment_label="Somewhat-Bullish",
                ticker_sentiment_score=0.20,
                ticker_sentiment_label="Somewhat-Bullish",
                relevance_score=0.80,
            )
            for index, title in enumerate(self.titles, start=1)
        ]


def _test_earnings_config(tmp_path, *, news_digest_max_items=3) -> EarningsConfig:
    return EarningsConfig(
        fmp_api_key="test",
        alphavantage_api_key="av-test",
        discord_webhook_url="",
        earnings_lookahead_days=1,
        universe_mode="watchlist_only",
        watchlist_symbols=["AAPL"],
        max_deep_analysis_candidates=10,
        request_timeout_seconds=1,
        request_retry_count=0,
        request_throttle_seconds=0,
        timezone_user="America/Los_Angeles",
        timezone_market="America/New_York",
        bmo_notification_time_pt="04:00",
        amc_notification_time_pt="12:45",
        morning_report_time_pt="05:30",
        pre_close_amc_report_time_pt="12:45",
        post_market_report_time_pt="15:30",
        publish_state_ttl_days=14,
        market_reaction_update_threshold_pct=1.5,
        news_limit=20,
        news_digest_max_items=news_digest_max_items,
        output_dir=tmp_path / "earnings",
        dry_run=True,
    )


def test_workflow_aggregates_multiple_news_items_into_one_digest(tmp_path):
    result = run_earnings_workflow(
        config=_test_earnings_config(tmp_path, news_digest_max_items=3),
        command="run-daily-earnings-workflow",
        as_of=date(2026, 6, 4),
        client=FakeFMPClient(),
        news_provider=MultiNewsProvider([
            "AAPL Q2 earnings preview",
            "AAPL revenue guidance report",
            "AAPL analyst EPS estimate update",
        ]),
        send_discord=False,
    )

    digest_messages = [m for m in result.published_messages if "**Earnings media digest: AAPL**" in m]
    assert len(digest_messages) == 1
    assert "**Earnings media update:" not in "\n".join(result.published_messages)
    assert result.published_news_items == 3


def test_workflow_enforces_max_news_items_per_symbol(tmp_path):
    result = run_earnings_workflow(
        config=_test_earnings_config(tmp_path, news_digest_max_items=2),
        command="run-daily-earnings-workflow",
        as_of=date(2026, 6, 4),
        client=FakeFMPClient(),
        news_provider=MultiNewsProvider([
            "AAPL Q2 earnings preview",
            "AAPL revenue guidance report",
            "AAPL analyst EPS estimate update",
            "AAPL consensus results watch",
        ]),
        send_discord=False,
    )

    digest = next(m for m in result.published_messages if "**Earnings media digest: AAPL**" in m)
    assert "Selected news items: `2`" in digest
    assert result.published_news_items == 2


def test_workflow_does_not_crash_when_one_symbol_fails(tmp_path):
    config = EarningsConfig(
        fmp_api_key="test",
        alphavantage_api_key="av-test",
        discord_webhook_url="",
        earnings_lookahead_days=1,
        universe_mode="watchlist_only",
        watchlist_symbols=["AAPL", "FAIL"],
        max_deep_analysis_candidates=10,
        request_timeout_seconds=1,
        request_retry_count=0,
        request_throttle_seconds=0,
        timezone_user="America/Los_Angeles",
        timezone_market="America/New_York",
        bmo_notification_time_pt="04:00",
        amc_notification_time_pt="12:45",
        morning_report_time_pt="05:30",
        pre_close_amc_report_time_pt="12:45",
        post_market_report_time_pt="15:30",
        publish_state_ttl_days=14,
        market_reaction_update_threshold_pct=1.5,
        news_limit=20,
        news_digest_max_items=3,
        output_dir=tmp_path / "earnings",
        dry_run=True,
    )

    result = run_earnings_workflow(
        config=config,
        command="run-daily-earnings-workflow",
        as_of=date(2026, 6, 4),
        client=FakeFMPClient(),
        news_provider=FakeNewsProvider(),
        send_discord=False,
    )

    assert len(result.candidates) == 2
    assert any("FAIL" in warning for warning in result.warnings)
    assert result.published_messages
    assert (tmp_path / "earnings" / "publish_state.json").exists()


def test_earnings_workflow_continues_when_news_unavailable(tmp_path):
    class FailingNewsProvider:
        def fetch_news(self, **kwargs):
            raise RuntimeError("Alpha Vantage unavailable")

    config = EarningsConfig(
        fmp_api_key="test",
        alphavantage_api_key="av-test",
        discord_webhook_url="",
        earnings_lookahead_days=1,
        universe_mode="watchlist_only",
        watchlist_symbols=["AAPL"],
        max_deep_analysis_candidates=10,
        request_timeout_seconds=1,
        request_retry_count=0,
        request_throttle_seconds=0,
        timezone_user="America/Los_Angeles",
        timezone_market="America/New_York",
        bmo_notification_time_pt="04:00",
        amc_notification_time_pt="12:45",
        morning_report_time_pt="05:30",
        pre_close_amc_report_time_pt="12:45",
        post_market_report_time_pt="15:30",
        publish_state_ttl_days=14,
        market_reaction_update_threshold_pct=1.5,
        news_limit=20,
        news_digest_max_items=3,
        output_dir=tmp_path / "earnings",
        dry_run=True,
    )

    result = run_earnings_workflow(
        config=config,
        command="run-daily-earnings-workflow",
        as_of=date(2026, 6, 4),
        client=FakeFMPClient(),
        news_provider=FailingNewsProvider(),
        send_discord=False,
    )

    assert len(result.candidates) == 1
    assert any("Alpha Vantage news unavailable" in warning for warning in result.warnings)
    assert result.published_messages
