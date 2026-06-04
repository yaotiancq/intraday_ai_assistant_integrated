from __future__ import annotations

from typing import Any


def pick_value(raw: dict[str, Any], names: list[str]) -> Any:
    lower_map = {str(k).lower(): k for k in raw.keys()}
    for name in names:
        key = lower_map.get(name.lower())
        if key is not None:
            return raw.get(key)
    return None


def safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except Exception:
        return None


def safe_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_symbol(value: Any) -> str | None:
    text = safe_str(value)
    if not text:
        return None
    text = text.upper()
    if text.startswith("US."):
        return text.split(".", 1)[1]
    return text


def normalize_report_date(raw: dict[str, Any]) -> str | None:
    value = pick_value(raw, ["date", "reportDate", "report_date", "fiscalDateEnding", "fiscal_date_ending"])
    text = safe_str(value)
    if not text:
        return None
    return text[:10]


def pct_change(actual: float | None, estimate: float | None) -> float | None:
    if actual is None or estimate is None or estimate == 0:
        return None
    return (actual - estimate) / abs(estimate) * 100.0


def normalize_rating(value: Any) -> str | None:
    text = safe_str(value)
    if not text:
        return None
    lower = text.lower()
    if "strong" in lower and "buy" in lower:
        return "strong_buy"
    if "buy" in lower or "outperform" in lower:
        return "buy"
    if "hold" in lower or "neutral" in lower:
        return "hold"
    if "sell" in lower or "underperform" in lower:
        return "sell"
    return lower.replace(" ", "_")


def normalize_title(value: Any) -> str:
    text = safe_str(value) or ""
    return " ".join(text.lower().split())

