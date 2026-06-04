from __future__ import annotations

from statistics import mean
from typing import Any

from .fmp_client import FMPClient
from .models import EarningsCalendarEvent, PreEarningsPreview
from .normalization import normalize_rating, pct_change, pick_value, safe_float, safe_int, safe_str


def build_pre_earnings_preview(client: FMPClient, event: EarningsCalendarEvent) -> PreEarningsPreview:
    warnings = list(event.warnings)
    earnings_history = _as_list(client.safe_get("earnings", {"symbol": event.symbol}))
    analyst_estimates = _fetch_analyst_estimates(client, event.symbol, warnings)
    target_summary = _as_dict(client.safe_get("price-target-summary", {"symbol": event.symbol}))
    target_consensus = _as_dict(client.safe_get("price-target-consensus", {"symbol": event.symbol}))

    estimate_row = _nearest_estimate(analyst_estimates, event.report_date)
    eps_estimate = event.eps_estimate
    revenue_estimate = event.revenue_estimate
    analyst_count: int | None = None
    if estimate_row:
        eps_estimate = eps_estimate if eps_estimate is not None else safe_float(
            pick_value(estimate_row, ["epsAvg", "estimatedEps", "epsEstimated", "eps_estimate"])
        )
        revenue_estimate = revenue_estimate if revenue_estimate is not None else safe_float(
            pick_value(estimate_row, ["revenueAvg", "estimatedRevenue", "revenueEstimated", "revenue_estimate"])
        )
        analyst_count = safe_int(pick_value(estimate_row, ["numberAnalystEstimatedRevenue", "analystCount", "analysts"]))

    price_target_low = safe_float(pick_value(target_consensus, ["targetLow", "priceTargetLow", "low"]))
    price_target_mean = safe_float(pick_value(target_consensus, ["targetConsensus", "targetMean", "priceTargetMean", "mean"]))
    price_target_high = safe_float(pick_value(target_consensus, ["targetHigh", "priceTargetHigh", "high"]))
    rating_consensus = normalize_rating(
        pick_value(target_summary, ["rating", "ratingConsensus", "consensus", "recommendation"])
        or pick_value(target_consensus, ["rating", "ratingConsensus", "consensus"])
    )

    historical_beat_rate = _historical_beat_rate(earnings_history)
    prior_surprise = _prior_quarter_eps_surprise(earnings_history)
    risk_level = classify_expectation_risk(
        price_target_mean=price_target_mean,
        rating_consensus=rating_consensus,
        historical_beat_rate=historical_beat_rate,
        prior_quarter_eps_surprise_pct=prior_surprise,
    )
    if eps_estimate is None:
        warnings.append("missing EPS estimate")
    if revenue_estimate is None:
        warnings.append("missing revenue estimate")

    summary = (
        f"{event.symbol} {event.report_date} {event.timing_bucket.upper()} "
        f"consensus risk={risk_level}, rating={rating_consensus or 'unavailable'}"
    )

    return PreEarningsPreview(
        symbol=event.symbol,
        report_date=event.report_date,
        timing_bucket=event.timing_bucket,
        notification_time_pt=event.notification_time_pt,
        timing_confidence=event.timing_confidence,
        eps_estimate=eps_estimate,
        revenue_estimate=revenue_estimate,
        analyst_count=analyst_count,
        price_target_low=price_target_low,
        price_target_mean=price_target_mean,
        price_target_high=price_target_high,
        rating_consensus=rating_consensus,
        historical_beat_rate=historical_beat_rate,
        prior_quarter_eps_surprise_pct=prior_surprise,
        expectation_risk_level=risk_level,
        warnings=warnings,
        summary=summary,
    )


def classify_expectation_risk(
    *,
    price_target_mean: float | None,
    rating_consensus: str | None,
    historical_beat_rate: float | None,
    prior_quarter_eps_surprise_pct: float | None,
) -> str:
    score = 0
    if rating_consensus in {"strong_buy", "buy"}:
        score += 1
    if historical_beat_rate is not None and historical_beat_rate >= 0.7:
        score += 1
    if prior_quarter_eps_surprise_pct is not None and prior_quarter_eps_surprise_pct >= 8:
        score += 1
    if price_target_mean is not None:
        score += 1
    if score >= 3:
        return "elevated_expectations"
    if score <= 1:
        return "low_visibility"
    return "balanced"


def preview_publish_payload(preview: PreEarningsPreview) -> dict[str, Any]:
    return {
        "symbol": preview.symbol,
        "report_date": preview.report_date,
        "timing_bucket": preview.timing_bucket,
        "eps_estimate": preview.eps_estimate,
        "revenue_estimate": preview.revenue_estimate,
        "analyst_count": preview.analyst_count,
        "price_target_low": preview.price_target_low,
        "price_target_mean": preview.price_target_mean,
        "price_target_high": preview.price_target_high,
        "rating_consensus": preview.rating_consensus,
        "historical_beat_rate": preview.historical_beat_rate,
        "prior_quarter_eps_surprise_pct": preview.prior_quarter_eps_surprise_pct,
        "expectation_risk_level": preview.expectation_risk_level,
        "warnings": sorted(set(preview.warnings)),
    }


def has_meaningful_consensus_change(previous: dict[str, Any] | None, current: dict[str, Any]) -> bool:
    if previous is None:
        return True
    checks = [
        _relative_changed(previous.get("eps_estimate"), current.get("eps_estimate"), 0.01),
        _relative_changed(previous.get("revenue_estimate"), current.get("revenue_estimate"), 0.01),
        _relative_changed(previous.get("price_target_mean"), current.get("price_target_mean"), 0.02),
        previous.get("analyst_count") != current.get("analyst_count"),
        previous.get("rating_consensus") != current.get("rating_consensus"),
        previous.get("expectation_risk_level") != current.get("expectation_risk_level"),
    ]
    return any(checks)


def _relative_changed(old: Any, new: Any, threshold: float) -> bool:
    old_f = safe_float(old)
    new_f = safe_float(new)
    if old_f is None and new_f is None:
        return False
    if old_f is None or new_f is None:
        return True
    if old_f == 0:
        return old_f != new_f
    return abs(new_f - old_f) / abs(old_f) >= threshold


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _as_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def _fetch_analyst_estimates(client: FMPClient, symbol: str, warnings: list[str]) -> list[dict[str, Any]]:
    quarterly = _as_list(
        client.safe_get("analyst-estimates", {"symbol": symbol, "period": "quarter"}, log_errors=False)
    )
    if quarterly:
        return quarterly

    annual = _as_list(
        client.safe_get("analyst-estimates", {"symbol": symbol, "period": "annual"}, log_errors=False)
    )
    if annual:
        warnings.append("quarterly analyst estimates unavailable; using annual estimates fallback")
        return annual

    warnings.append("analyst estimates unavailable; using calendar estimates when available")
    return []


def _nearest_estimate(rows: list[dict[str, Any]], report_date: str) -> dict[str, Any] | None:
    for row in rows:
        row_date = safe_str(pick_value(row, ["date", "reportDate", "fiscalDateEnding"]))
        if row_date and row_date[:10] == report_date:
            return row
    return rows[0] if rows else None


def _historical_beat_rate(rows: list[dict[str, Any]], limit: int = 8) -> float | None:
    outcomes: list[float] = []
    for row in rows[:limit]:
        actual = safe_float(pick_value(row, ["epsActual", "actualEps", "eps_actual"]))
        estimate = safe_float(pick_value(row, ["epsEstimated", "estimatedEps", "eps_estimate"]))
        if actual is not None and estimate is not None:
            outcomes.append(1.0 if actual >= estimate else 0.0)
    if not outcomes:
        return None
    return mean(outcomes)


def _prior_quarter_eps_surprise(rows: list[dict[str, Any]]) -> float | None:
    for row in rows:
        actual = safe_float(pick_value(row, ["epsActual", "actualEps", "eps_actual"]))
        estimate = safe_float(pick_value(row, ["epsEstimated", "estimatedEps", "eps_estimate"]))
        surprise = pct_change(actual, estimate)
        if surprise is not None:
            return surprise
    return None
