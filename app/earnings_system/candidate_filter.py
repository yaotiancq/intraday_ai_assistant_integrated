from __future__ import annotations

from .config import normalize_symbols
from .models import EarningsCalendarEvent


def filter_candidates(
    events: list[EarningsCalendarEvent],
    *,
    universe_mode: str,
    watchlist_symbols: list[str],
    max_candidates: int,
) -> list[EarningsCalendarEvent]:
    watchlist = set(normalize_symbols(watchlist_symbols))
    mode = (universe_mode or "watchlist_only").strip().lower()

    selected: list[EarningsCalendarEvent] = []
    seen: set[tuple[str, str]] = set()
    for event in sorted(events, key=lambda e: (e.report_date, e.symbol)):
        if mode == "watchlist_only" and event.symbol not in watchlist:
            continue
        key = (event.symbol, event.report_date)
        if key in seen:
            continue
        seen.add(key)
        selected.append(event)
        if max_candidates > 0 and len(selected) >= max_candidates:
            break

    return selected

