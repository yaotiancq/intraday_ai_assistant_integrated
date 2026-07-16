from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class PremarketReportResult:
    report: str
    mode: str
    fallback_reason: str | None = None
    warnings: List[str] = field(default_factory=list)
    llm_validation_errors: List[str] = field(default_factory=list)
    llm_attempted: bool = False
    llm_succeeded: bool = False
    safe_mode: bool = False

    def to_status_dict(self) -> Dict[str, Any]:
        return {
            'mode': self.mode,
            'llm_attempted': self.llm_attempted,
            'llm_succeeded': self.llm_succeeded,
            'fallback_reason': self.fallback_reason,
            'llm_validation_errors': list(self.llm_validation_errors),
            'safe_mode': self.safe_mode,
            'warnings': list(self.warnings),
        }
