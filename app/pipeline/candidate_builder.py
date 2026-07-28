from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


def build_candidates(
    configured_stock_symbols: Iterable[str],
    news_items: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    min_abs_move_pct: float = 1.5,
) -> dict[str, Any]:
    """Return configured stocks only; news and movers can only annotate them.

    The former implementation used a union and could promote arbitrary RSS
    tickers and benchmark ETFs into the tradable candidate set. This function
    intentionally fails closed: observations outside the allowlist are recorded
    as ignored and can never become a stock candidate.
    """

    ordered: list[str] = []
    seen: set[str] = set()
    for value in configured_stock_symbols:
        symbol = str(value).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            ordered.append(symbol)
    allowed = set(ordered)

    news_symbols: set[str] = set()
    ignored_news_symbols: set[str] = set()
    for item in news_items:
        if item.get("is_error"):
            continue
        for value in item.get("related_symbols", []):
            symbol = str(value).strip().upper()
            if symbol in allowed:
                news_symbols.add(symbol)
            elif symbol:
                ignored_news_symbols.add(symbol)

    movers: set[str] = set()
    ignored_snapshot_symbols: set[str] = set()
    for snapshot in snapshots:
        symbol = str(snapshot.get("symbol", "")).strip().upper()
        if symbol not in allowed:
            if symbol:
                ignored_snapshot_symbols.add(symbol)
            continue
        try:
            change = float(snapshot.get("effective_change_pct", snapshot.get("change_pct")) or 0)
        except (TypeError, ValueError):
            change = 0.0
        if abs(change) >= min_abs_move_pct:
            movers.add(symbol)

    reasons: dict[str, list[str]] = {}
    for symbol in ordered:
        values = ["fixed_universe"]
        if symbol in news_symbols:
            values.append("news_catalyst")
        if symbol in movers:
            values.append("premarket_mover")
        reasons[symbol] = values

    return {
        "symbols": ordered,
        "reason": reasons,
        "counts": {
            "configured": len(ordered),
            "news_annotations": len(news_symbols),
            "mover_annotations": len(movers),
            "total": len(ordered),
        },
        "ignored_unconfigured_symbols": sorted(ignored_news_symbols | ignored_snapshot_symbols),
        "containment_enforced": True,
    }


def narrow_candidates(
    candidates: list[dict[str, Any]],
    *,
    minimum_score: float,
    maximum_candidates: int,
    maximum_per_sector: int,
) -> list[dict[str, Any]]:
    """Deterministically rank candidates without filling unused capacity."""

    ranked = sorted(
        (item for item in candidates if float(item.get("premarket_score", 0) or 0) >= minimum_score),
        key=lambda item: (-float(item.get("premarket_score", 0) or 0), str(item.get("symbol", ""))),
    )
    selected: list[dict[str, Any]] = []
    sector_counts: Counter[str] = Counter()
    for item in ranked:
        sector = str(item.get("sector", "UNKNOWN"))
        if sector_counts[sector] >= maximum_per_sector:
            continue
        selected.append(item)
        sector_counts[sector] += 1
        if len(selected) >= maximum_candidates:
            break
    return selected
