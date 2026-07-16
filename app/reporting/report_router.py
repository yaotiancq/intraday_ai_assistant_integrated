from __future__ import annotations

from dataclasses import dataclass, field
from time import sleep
from typing import Any, Callable, Dict, List

from app.llm.openai_client import OpenAIReportClient
from app.reporting.premarket_rule_report import RuleBasedPremarketReportGenerator
from app.reporting.report_types import PremarketReportResult
from app.validators.output_validator import validate_report, validate_rule_based_report


OpenAIClientFactory = Callable[[str, str], Any]


@dataclass
class PremarketReportRouter:
    api_key: str
    model: str
    llm_enabled: bool = True
    rule_fallback_enabled: bool = True
    max_retries: int = 1
    retry_delay_seconds: float = 0.5
    openai_client_factory: OpenAIClientFactory | None = None
    rule_generator: RuleBasedPremarketReportGenerator = field(default_factory=RuleBasedPremarketReportGenerator)

    def generate(
        self,
        evidence_pack: Dict[str, Any],
        evidence_errors: List[str] | None = None,
    ) -> PremarketReportResult:
        evidence_errors = list(evidence_errors or [])
        llm_attempted = False
        llm_validation_errors: List[str] = []

        if not self.llm_enabled:
            return self._fallback(
                evidence_pack=evidence_pack,
                evidence_errors=evidence_errors,
                reason='llm_disabled',
                llm_attempted=False,
                llm_validation_errors=[],
            )

        if not (self.api_key or '').strip():
            return self._fallback(
                evidence_pack=evidence_pack,
                evidence_errors=evidence_errors,
                reason='missing_openai_api_key',
                llm_attempted=False,
                llm_validation_errors=[],
            )

        try:
            client = self._make_openai_client()
        except Exception as exc:
            return self._fallback(
                evidence_pack=evidence_pack,
                evidence_errors=evidence_errors,
                reason=classify_openai_exception(exc),
                llm_attempted=False,
                llm_validation_errors=[],
            )

        attempts = max(1, int(self.max_retries) + 1)
        last_reason = 'openai_failed'
        for attempt in range(attempts):
            llm_attempted = True
            try:
                report = client.generate_premarket_report(evidence_pack)
            except Exception as exc:
                last_reason = classify_openai_exception(exc)
                if attempt + 1 < attempts:
                    self._sleep_before_retry()
                    continue
                break

            if report is None:
                last_reason = 'openai_empty_response'
                break
            if not isinstance(report, str):
                last_reason = 'openai_malformed_response'
                break
            if not report.strip():
                last_reason = 'openai_blank_response'
                break
            if looks_truncated(report):
                last_reason = 'openai_truncated_response'
                break

            llm_validation_errors = validate_report(report)
            if llm_validation_errors:
                last_reason = 'openai_report_validation_failed'
                break

            return PremarketReportResult(
                report=report,
                mode='llm',
                fallback_reason=None,
                warnings=[],
                llm_validation_errors=[],
                llm_attempted=True,
                llm_succeeded=True,
                safe_mode=False,
            )

        return self._fallback(
            evidence_pack=evidence_pack,
            evidence_errors=evidence_errors,
            reason=last_reason,
            llm_attempted=llm_attempted,
            llm_validation_errors=llm_validation_errors,
        )

    def _fallback(
        self,
        evidence_pack: Dict[str, Any],
        evidence_errors: List[str],
        reason: str,
        llm_attempted: bool,
        llm_validation_errors: List[str],
    ) -> PremarketReportResult:
        if not self.rule_fallback_enabled:
            result = self.rule_generator.generate_safe_mode(
                evidence_pack=evidence_pack,
                reasons=['rule_fallback_disabled'],
                fallback_reason=reason,
            )
        else:
            try:
                result = self.rule_generator.generate(
                    evidence_pack=evidence_pack,
                    evidence_errors=evidence_errors,
                    fallback_reason=reason,
                )
            except Exception as exc:
                result = self.rule_generator.generate_safe_mode(
                    evidence_pack=evidence_pack,
                    reasons=[f'rule_based_generation_failed:{sanitize_reason(exc)}'],
                    fallback_reason=reason,
                )

        rule_errors = validate_rule_based_report(result.report, safe_mode=result.safe_mode)
        warnings = list(result.warnings)
        if rule_errors and not result.safe_mode:
            result = self.rule_generator.generate_safe_mode(
                evidence_pack=evidence_pack,
                reasons=[f'rule_based_validation_failed:{error}' for error in rule_errors],
                fallback_reason=reason,
            )
            warnings = list(result.warnings)
        elif rule_errors:
            warnings.extend(f'rule_based_validation_warning:{error}' for error in rule_errors)

        return PremarketReportResult(
            report=result.report,
            mode=result.mode,
            fallback_reason=reason,
            warnings=warnings,
            llm_validation_errors=list(llm_validation_errors),
            llm_attempted=llm_attempted,
            llm_succeeded=False,
            safe_mode=result.safe_mode,
        )

    def _make_openai_client(self) -> Any:
        if self.openai_client_factory is not None:
            return self.openai_client_factory(self.api_key, self.model)
        return OpenAIReportClient(self.api_key, self.model, fallback_on_error=False)

    def _sleep_before_retry(self) -> None:
        if self.retry_delay_seconds > 0:
            sleep(self.retry_delay_seconds)


def looks_truncated(report: str) -> bool:
    stripped = report.strip()
    if len(stripped) < 100:
        return True
    if stripped.endswith(('...', '…')):
        return True
    return False


def classify_openai_exception(exc: Exception) -> str:
    status_code = getattr(exc, 'status_code', None) or getattr(getattr(exc, 'response', None), 'status_code', None)
    if status_code:
        try:
            code = int(status_code)
        except Exception:
            code = 0
        if code in {401, 403, 408, 429} or code >= 500:
            return f'openai_http_{code}'
    name = exc.__class__.__name__.lower()
    message = sanitize_reason(exc).lower()
    if 'timeout' in name or 'timeout' in message or 'timed out' in message:
        return 'openai_timeout'
    if 'connection' in name or 'network' in message or 'dns' in message or 'resolve' in message:
        return 'openai_network_error'
    return f'openai_exception:{sanitize_reason(exc)}'


def sanitize_reason(exc: Exception) -> str:
    text = str(exc).replace('\n', ' ').strip()
    if len(text) > 160:
        text = text[:157] + '...'
    return text or exc.__class__.__name__
