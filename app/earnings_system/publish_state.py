from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.utils.file_io import read_json, write_json

from .models import PublishStateItem


def empty_publish_state() -> dict[str, Any]:
    return {"version": 1, "last_cleanup_at": None, "items": {}}


def load_publish_state(path: str | Path) -> dict[str, Any]:
    state = read_json(path, default=None)
    if not isinstance(state, dict):
        return empty_publish_state()
    if "items" not in state or not isinstance(state["items"], dict):
        state["items"] = {}
    state.setdefault("version", 1)
    state.setdefault("last_cleanup_at", None)
    return state


def save_publish_state(path: str | Path, state: dict[str, Any]) -> None:
    write_json(path, state)


def cleanup_expired_items(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    items = state.setdefault("items", {})
    expired = []
    for key, item in items.items():
        expires_at = item.get("expires_at") if isinstance(item, dict) else None
        if expires_at and _parse_dt(expires_at) <= current:
            expired.append(key)
    for key in expired:
        items.pop(key, None)
    state["last_cleanup_at"] = current.isoformat()
    return state


def build_publish_key(symbol: str, report_date: str, content_type: str, content_scope: str) -> str:
    return "|".join([symbol.upper(), report_date, content_type, content_scope])


def compute_content_hash(normalized_payload: dict[str, Any]) -> str:
    payload = _strip_ignored_fields(normalized_payload)
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def should_publish(state: dict[str, Any], key: str, content_hash: str) -> bool:
    item = state.get("items", {}).get(key)
    if not isinstance(item, dict):
        return True
    return item.get("content_hash") != content_hash


def mark_published(state: dict[str, Any], item: PublishStateItem) -> dict[str, Any]:
    state.setdefault("version", 1)
    state.setdefault("items", {})[item.key] = item.to_dict()
    return state


def make_publish_item(
    *,
    key: str,
    symbol: str,
    report_date: str,
    content_type: str,
    content_scope: str,
    content_hash: str,
    summary: str,
    ttl_days: int,
    payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> PublishStateItem:
    current = now or datetime.now(timezone.utc)
    expires_at = datetime.combine(
        date.fromisoformat(report_date[:10]) + timedelta(days=ttl_days),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    return PublishStateItem(
        key=key,
        symbol=symbol.upper(),
        report_date=report_date,
        content_type=content_type,
        content_scope=content_scope,
        content_hash=content_hash,
        last_published_at=current.isoformat(),
        expires_at=expires_at.isoformat(),
        summary=summary,
        payload=payload,
    )


def previous_payload(state: dict[str, Any], key: str) -> dict[str, Any] | None:
    item = state.get("items", {}).get(key)
    if not isinstance(item, dict):
        return None
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else None


def _strip_ignored_fields(value: Any) -> Any:
    ignored = {"raw", "generated_at", "as_of", "last_published_at", "expires_at", "summary"}
    if isinstance(value, dict):
        return {k: _strip_ignored_fields(v) for k, v in sorted(value.items()) if k not in ignored}
    if isinstance(value, list):
        return [_strip_ignored_fields(v) for v in value]
    return value


def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt

