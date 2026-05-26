from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List
import requests


def normalize_monitor_symbol(symbol: str, market_prefix: str = "US") -> str:
    s = str(symbol or "").strip().upper()
    if not s:
        raise ValueError("empty symbol")
    if "." not in s:
        s = f"{market_prefix.upper()}.{s}"
    market, ticker = s.split(".", 1)
    if market != market_prefix.upper():
        raise ValueError(f"unsupported market prefix: {market}; expected {market_prefix.upper()}")
    if not ticker.replace("-", "").replace(".", "").isalnum():
        raise ValueError(f"invalid ticker: {ticker}")
    return s


def extract_recommended_symbols(
    evidence_pack: Dict[str, Any],
    tiers: Iterable[str] = ("A", "B"),
    max_symbols: int = 12,
    market_prefix: str = "US",
) -> List[str]:
    """
    Deterministically extract the symbols the AI premarket assistant is expected
    to discuss: the top A/B-tier candidates already selected by the evidence pack.

    This avoids brittle parsing of LLM text.
    """
    result: List[str] = []
    seen = set()
    candidates = evidence_pack.get("candidates", {}) or {}
    for tier in tiers:
        for row in candidates.get(str(tier).upper(), []) or []:
            raw = row.get("symbol") if isinstance(row, dict) else row
            try:
                code = normalize_monitor_symbol(str(raw), market_prefix=market_prefix)
            except Exception:
                continue
            if code not in seen:
                seen.add(code)
                result.append(code)
            if len(result) >= max_symbols:
                return result
    return result


@dataclass
class MonitorAdminClient:
    base_url: str
    token: str = ""
    timeout: int = 15

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["X-Admin-Token"] = self.token
        return h

    def add_symbols(self, symbols: List[str]) -> Dict[str, Any]:
        current: Dict[str, Any] = {}
        for symbol in symbols:
            resp = requests.post(
                f"{self.base_url.rstrip('/')}/watchlist/add",
                json={"symbol": symbol},
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code >= 300:
                raise RuntimeError(f"monitor add failed for {symbol}: {resp.status_code} {resp.text[:300]}")
            current = resp.json()
        return current or {"status": "ok", "symbols": []}

    def set_symbols(self, symbols: List[str]) -> Dict[str, Any]:
        resp = requests.post(
            f"{self.base_url.rstrip('/')}/watchlist/set",
            json={"symbols": symbols},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"monitor set failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()

    def list_symbols(self) -> Dict[str, Any]:
        resp = requests.get(
            f"{self.base_url.rstrip('/')}/watchlist",
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"monitor list failed: {resp.status_code} {resp.text[:300]}")
        return resp.json()


def publish_recommendations_to_monitor(
    evidence_pack: Dict[str, Any],
    admin_url: str,
    admin_token: str,
    update_mode: str = "add",
    tiers: Iterable[str] = ("A", "B"),
    max_symbols: int = 12,
    market_prefix: str = "US",
) -> Dict[str, Any]:
    symbols = extract_recommended_symbols(
        evidence_pack=evidence_pack,
        tiers=tiers,
        max_symbols=max_symbols,
        market_prefix=market_prefix,
    )
    if not symbols:
        return {"status": "skipped", "reason": "no_recommended_symbols", "symbols": []}

    client = MonitorAdminClient(base_url=admin_url, token=admin_token)
    mode = update_mode.strip().lower()
    if mode == "set":
        result = client.set_symbols(symbols)
    elif mode == "add":
        result = client.add_symbols(symbols)
    else:
        raise ValueError("update_mode must be 'add' or 'set'")

    return {
        "status": "ok",
        "mode": mode,
        "recommended_symbols": symbols,
        "monitor_result": result,
    }
