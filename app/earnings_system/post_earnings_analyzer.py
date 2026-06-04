from __future__ import annotations

from typing import Any

from .fmp_client import FMPClient
from .models import EarningsCalendarEvent, PostEarningsAnalysis
from .normalization import pct_change, pick_value, safe_float, safe_str


def build_post_earnings_analysis(client: FMPClient, event: EarningsCalendarEvent) -> PostEarningsAnalysis:
    warnings = list(event.warnings)
    rows = _as_list(client.safe_get("earnings", {"symbol": event.symbol}))
    row = _matching_row(rows, event.report_date)

    actual_eps = event.eps_actual
    estimated_eps = event.eps_estimate
    actual_revenue = event.revenue_actual
    estimated_revenue = event.revenue_estimate

    if row:
        actual_eps = actual_eps if actual_eps is not None else safe_float(pick_value(row, ["epsActual", "actualEps", "eps_actual"]))
        estimated_eps = estimated_eps if estimated_eps is not None else safe_float(
            pick_value(row, ["epsEstimated", "estimatedEps", "eps_estimate"])
        )
        actual_revenue = actual_revenue if actual_revenue is not None else safe_float(
            pick_value(row, ["revenueActual", "actualRevenue", "revenue_actual"])
        )
        estimated_revenue = estimated_revenue if estimated_revenue is not None else safe_float(
            pick_value(row, ["revenueEstimated", "estimatedRevenue", "revenue_estimate"])
        )

    eps_surprise = pct_change(actual_eps, estimated_eps)
    revenue_surprise = pct_change(actual_revenue, estimated_revenue)
    classification = classify_actual_vs_estimate(eps_surprise, revenue_surprise)
    if actual_eps is None and actual_revenue is None:
        warnings.append("actual EPS/revenue unavailable")

    summary = f"{event.symbol} {event.report_date} result={classification}"
    return PostEarningsAnalysis(
        symbol=event.symbol,
        report_date=event.report_date,
        actual_eps=actual_eps,
        estimated_eps=estimated_eps,
        eps_surprise_pct=eps_surprise,
        actual_revenue=actual_revenue,
        estimated_revenue=estimated_revenue,
        revenue_surprise_pct=revenue_surprise,
        result_classification=classification,
        warnings=warnings,
        summary=summary,
    )


def classify_actual_vs_estimate(eps_surprise_pct: float | None, revenue_surprise_pct: float | None) -> str:
    values = [v for v in [eps_surprise_pct, revenue_surprise_pct] if v is not None]
    if not values:
        return "unavailable"
    avg = sum(values) / len(values)
    has_positive = any(v > 0 for v in values)
    has_negative = any(v < 0 for v in values)
    if avg >= 8 and not has_negative:
        return "strong_beat"
    if avg > 0 and not has_negative:
        return "mild_beat"
    if has_positive and has_negative:
        return "mixed"
    if avg <= -8 and not has_positive:
        return "strong_miss"
    if avg < 0:
        return "mild_miss"
    return "mixed"


def post_publish_payload(analysis: PostEarningsAnalysis) -> dict[str, Any]:
    return {
        "symbol": analysis.symbol,
        "report_date": analysis.report_date,
        "actual_eps": analysis.actual_eps,
        "estimated_eps": analysis.estimated_eps,
        "eps_surprise_pct": analysis.eps_surprise_pct,
        "actual_revenue": analysis.actual_revenue,
        "estimated_revenue": analysis.estimated_revenue,
        "revenue_surprise_pct": analysis.revenue_surprise_pct,
        "result_classification": analysis.result_classification,
        "warnings": sorted(set(analysis.warnings)),
    }


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _matching_row(rows: list[dict[str, Any]], report_date: str) -> dict[str, Any] | None:
    for row in rows:
        row_date = safe_str(pick_value(row, ["date", "reportDate", "fiscalDateEnding"]))
        if row_date and row_date[:10] == report_date:
            return row
    return rows[0] if rows else None

