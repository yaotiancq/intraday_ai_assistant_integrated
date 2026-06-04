from __future__ import annotations

from typing import Any

from .fmp_client import FMPClient
from .models import EarningsCalendarEvent, MarketReactionAnalysis
from .normalization import pick_value, safe_float


def build_market_reaction_analysis(
    client: FMPClient,
    event: EarningsCalendarEvent,
    *,
    threshold_pct: float = 1.5,
) -> MarketReactionAnalysis:
    warnings = list(event.warnings)
    quote = _as_dict(client.safe_get("quote", {"symbol": event.symbol}))
    reaction_price = safe_float(pick_value(quote, ["price", "last", "close"]))
    reference_price = safe_float(pick_value(quote, ["previousClose", "previous_close", "prevClose"]))
    reaction_pct = None
    if reaction_price is not None and reference_price not in (None, 0):
        reaction_pct = (reaction_price - reference_price) / abs(reference_price) * 100.0
    else:
        warnings.append("market reaction price/reference unavailable")

    classification = classify_market_reaction(reaction_pct, threshold_pct=threshold_pct)
    next_day_bias, medium_bias, confirmation, failure = conditional_reaction_notes(classification)

    return MarketReactionAnalysis(
        symbol=event.symbol,
        report_date=event.report_date,
        reference_price=reference_price,
        reaction_price=reaction_price,
        reaction_pct=reaction_pct,
        reaction_session="premarket_or_afterhours_snapshot",
        reaction_classification=classification,
        next_day_conditional_bias=next_day_bias,
        medium_term_conditional_bias=medium_bias,
        confirmation_conditions=confirmation,
        failure_conditions=failure,
        warnings=warnings,
        summary=f"{event.symbol} reaction={classification}",
    )


def classify_market_reaction(reaction_pct: float | None, *, threshold_pct: float = 1.5) -> str:
    if reaction_pct is None:
        return "unavailable"
    if reaction_pct >= 5:
        return "strong_positive"
    if reaction_pct >= threshold_pct:
        return "positive"
    if reaction_pct <= -5:
        return "strong_negative"
    if reaction_pct <= -threshold_pct:
        return "negative"
    return "neutral"


def conditional_reaction_notes(classification: str) -> tuple[str, str, list[str], list[str]]:
    if classification in {"strong_positive", "positive"}:
        return (
            "Bullish continuation watch if price holds VWAP and the opening range.",
            "Medium-term constructive only if the gap holds for several trading days.",
            ["Price holds VWAP", "Opening range high is reclaimed or defended", "Volume confirms the move"],
            ["Gap fades below VWAP", "Opening range low fails", "Market breadth turns defensive"],
        )
    if classification in {"strong_negative", "negative"}:
        return (
            "Sell-the-news or downside continuation risk if the gap cannot reclaim VWAP.",
            "Medium-term cautious unless price repairs the earnings gap over several trading days.",
            ["VWAP reclaim with improving breadth", "Gap-down low holds after first hour"],
            ["Gap fades further below VWAP", "Lower high forms after the open"],
        )
    if classification == "neutral":
        return (
            "Neutral reaction watch; let opening range and VWAP decide direction.",
            "Medium-term view needs several closes after the report.",
            ["Clean break from opening range with volume", "VWAP acceptance"],
            ["Choppy two-sided trade with weak volume"],
        )
    return (
        "Reaction unavailable; wait for liquid premarket, regular-session, or after-hours price discovery.",
        "No medium-term read until reaction data and several trading sessions are available.",
        ["Reaction price becomes available", "VWAP and opening range can be evaluated"],
        ["Data remains unavailable"],
    )


def reaction_publish_payload(analysis: MarketReactionAnalysis) -> dict[str, Any]:
    return {
        "symbol": analysis.symbol,
        "report_date": analysis.report_date,
        "reference_price": analysis.reference_price,
        "reaction_price": analysis.reaction_price,
        "reaction_pct": analysis.reaction_pct,
        "reaction_classification": analysis.reaction_classification,
        "next_day_conditional_bias": analysis.next_day_conditional_bias,
        "medium_term_conditional_bias": analysis.medium_term_conditional_bias,
        "warnings": sorted(set(analysis.warnings)),
    }


def has_meaningful_reaction_change(previous: dict[str, Any] | None, current: dict[str, Any], threshold_pct: float) -> bool:
    if previous is None:
        return True
    if previous.get("reaction_classification") != current.get("reaction_classification"):
        return True
    old = safe_float(previous.get("reaction_pct"))
    new = safe_float(current.get("reaction_pct"))
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    return abs(new - old) >= threshold_pct


def _as_dict(data: Any) -> dict[str, Any]:
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return {}

