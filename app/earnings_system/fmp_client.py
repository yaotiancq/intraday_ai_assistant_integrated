from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any

import requests

LOG = logging.getLogger(__name__)


@dataclass
class FMPClient:
    api_key: str
    base_url: str = "https://financialmodelingprep.com/stable"
    timeout_seconds: int = 20
    retry_count: int = 2
    throttle_seconds: float = 0.2
    session: requests.Session = field(default_factory=requests.Session)

    class NonRetryableFMPError(RuntimeError):
        pass

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        if not self.api_key:
            raise RuntimeError("FMP_API_KEY is empty")

        query = dict(params or {})
        query["apikey"] = self.api_key
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(self.retry_count + 1):
            if self.throttle_seconds > 0:
                time.sleep(self.throttle_seconds)
            try:
                resp = self.session.get(url, params=query, timeout=self.timeout_seconds)
                if resp.status_code >= 500 and attempt < self.retry_count:
                    last_error = RuntimeError(f"FMP transient error {resp.status_code}: {resp.text[:200]}")
                    continue
                if resp.status_code != 200:
                    error = f"FMP error {resp.status_code}: {resp.text[:300]}"
                    if 400 <= resp.status_code < 500:
                        raise self.NonRetryableFMPError(error)
                    raise RuntimeError(error)
                data = resp.json()
                if isinstance(data, dict) and "Error Message" in data:
                    raise RuntimeError(str(data["Error Message"]))
                return data
            except self.NonRetryableFMPError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.retry_count:
                    break
                LOG.warning("FMP request failed attempt=%s endpoint=%s error=%s", attempt + 1, endpoint, exc)

        raise RuntimeError(f"FMP request failed for {endpoint}: {last_error}")

    def safe_get(self, endpoint: str, params: dict[str, Any] | None = None, *, log_errors: bool = True) -> Any:
        try:
            return self.get(endpoint, params=params)
        except Exception as exc:
            if log_errors:
                LOG.warning("FMP safe_get failed endpoint=%s params=%s error=%s", endpoint, params, exc)
            return None
