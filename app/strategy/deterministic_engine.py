"""Single deterministic strategy engine for premarket and opening stages."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from typing import Any, Mapping

from app.indicators._bars import (
    DEFAULT_TIMEZONE,
    clean_public_dict,
    finite_float,
    parse_cutoff,
    select_completed_bars,
)
from app.indicators.opening_metrics import compute_opening_metrics
from app.scoring._common import clamp, json_safe, validate_weights
from app.scoring.opening_score import score_opening
from app.scoring.premarket_score import score_premarket
from app.strategy.entry_plan import build_entry_plan
from app.strategy.risk_gates import evaluate_risk_gates
from app.strategy.setup_classifier import FAILURE_SETUPS, classify_setup


STAGE_ALIASES = {
    "premarket": "premarket",
    "opening_5m": "opening_5m",
    "opening-5m": "opening_5m",
    "opening5m": "opening_5m",
    "opening_15m": "opening_15m",
    "opening-15m": "opening_15m",
    "opening15m": "opening_15m",
}
DEFAULT_STAGE_CUTOFFS = {"premarket": "08:45", "opening_5m": "09:35", "opening_15m": "09:45"}
DATA_FAILURES = frozenset({
    "STALE_MARKET_DATA",
    "DELAYED_MARKET_DATA",
    "MISSING_BARS",
    "INCOMPLETE_BARS",
    "FUTURE_TIMESTAMPS",
    "DUPLICATE_BARS",
    "NON_MONOTONIC_TIMESTAMPS",
    "INCORRECT_SESSION_BARS",
    "MISSING_BAR_TIMESTAMPS",
    "MISSING_BENCHMARK_DATA",
    "MISSING_SECTOR_ETF_DATA",
    "INVALID_PRICES",
    "INVALID_VOLUME",
})


def _normalize_stage(stage: str) -> str:
    raw_value = getattr(stage, "value", stage)
    raw = str(raw_value).strip().lower()
    if raw not in STAGE_ALIASES:
        raise ValueError(f"unsupported deterministic strategy stage: {stage!r}")
    return STAGE_ALIASES[raw]


def _stage_cutoff(config: Mapping[str, Any], stage: str) -> str:
    scheduler = config.get("scheduler", {}) if isinstance(config.get("scheduler"), Mapping) else {}
    stages = scheduler.get("stages", {}) if isinstance(scheduler.get("stages"), Mapping) else {}
    stage_config = stages.get(stage, {}) if isinstance(stages.get(stage), Mapping) else {}
    return str(stage_config.get("evidence_cutoff", DEFAULT_STAGE_CUTOFFS[stage]))


def _effective_cutoff(
    trade_date: str | date | datetime,
    requested: str | time | datetime,
    stage: str,
    config: Mapping[str, Any],
    timezone: str,
) -> tuple[datetime, bool]:
    requested_value = parse_cutoff(trade_date, requested, timezone)
    scheduled = parse_cutoff(trade_date, _stage_cutoff(config, stage), timezone)
    return (scheduled, True) if requested_value > scheduled else (requested_value, False)


def _premarket_card(
    evidence: Mapping[str, Any],
    premarket_evidence: Mapping[str, Any] | None,
    config: Mapping[str, Any],
    trade_date: str | date | datetime,
    evidence_cutoff: datetime,
    timezone: str,
) -> dict[str, Any]:
    supplied = evidence.get("premarket_scorecard", evidence.get("premarket_result"))
    if not isinstance(supplied, Mapping) and isinstance(evidence.get("premarket_score"), Mapping):
        supplied = evidence.get("premarket_score")
    if isinstance(supplied, Mapping):
        return dict(supplied)
    if premarket_evidence is not None:
        return score_premarket(
            premarket_evidence,
            config,
            trade_date=trade_date,
            evidence_cutoff=min(evidence_cutoff, parse_cutoff(trade_date, _stage_cutoff(config, "premarket"), timezone)),
            timezone=timezone,
        )
    scalar = finite_float(evidence.get("premarket_score"))
    long_score = finite_float(evidence.get("premarket_long_score"))
    short_score = finite_float(evidence.get("premarket_short_score"))
    direction = str(evidence.get("premarket_direction", "WATCH")).upper()
    if long_score is None and scalar is not None and direction == "LONG":
        long_score, short_score = scalar, short_score if short_score is not None else 100.0 - scalar
    elif short_score is None and scalar is not None and direction == "SHORT":
        short_score, long_score = scalar, long_score if long_score is not None else 100.0 - scalar
    else:
        long_score = long_score if long_score is not None else scalar if scalar is not None else 50.0
        short_score = short_score if short_score is not None else scalar if scalar is not None else 50.0
    if direction not in {"LONG", "SHORT"}:
        difference = abs(long_score - short_score)
        direction = "WATCH" if difference <= 8.0 else "LONG" if long_score > short_score else "SHORT"
    selected = max(long_score, short_score)
    return {
        "premarket_score": scalar if scalar is not None else selected,
        "score": scalar if scalar is not None else selected,
        "long_score": long_score,
        "short_score": short_score,
        "direction": direction,
        "directional_conflict": direction == "WATCH",
        "factor_breakdown": evidence.get("premarket_factor_breakdown", {}),
        "reason_codes": ["SUPPLIED_PREMARKET_SCORE"],
    }


def _premarket_stage(
    evidence: Mapping[str, Any],
    config: Mapping[str, Any],
    trade_date: str | date | datetime,
    cutoff: datetime,
    requested_cutoff: Any,
    cutoff_capped: bool,
    timezone: str,
) -> dict[str, Any]:
    scorecard = score_premarket(
        evidence,
        config,
        trade_date=trade_date,
        evidence_cutoff=cutoff,
        timezone=timezone,
    )
    filters = config.get("premarket_filters", {}) if isinstance(config.get("premarket_filters"), Mapping) else {}
    failures: list[str] = []
    checks = (
        ("price", "minimum_price", "PRICE_BELOW_MINIMUM", lambda value, threshold: value >= threshold),
        ("average_daily_dollar_volume", "minimum_average_daily_dollar_volume", "INSUFFICIENT_AVERAGE_DAILY_DOLLAR_VOLUME", lambda value, threshold: value >= threshold),
        ("premarket_dollar_volume", "minimum_premarket_dollar_volume", "INSUFFICIENT_PREMARKET_DOLLAR_VOLUME", lambda value, threshold: value >= threshold),
        ("spread_bps", "maximum_premarket_spread_bps", "PREMARKET_SPREAD_TOO_WIDE", lambda value, threshold: value <= threshold),
        ("premarket_relative_volume", "minimum_premarket_relative_volume", "INSUFFICIENT_PREMARKET_RELATIVE_VOLUME", lambda value, threshold: value >= threshold),
    )
    for evidence_key, threshold_key, reason, predicate in checks:
        value = finite_float(evidence.get(evidence_key))
        threshold = finite_float(filters.get(threshold_key))
        if value is not None and threshold is not None and not predicate(value, threshold):
            failures.append(reason)
    gap = abs(finite_float(evidence.get("gap_percent")) or 0.0)
    max_gap = finite_float(filters.get("maximum_absolute_gap_percent"))
    if max_gap is not None and gap > max_gap:
        failures.append("PREMARKET_GAP_ABOVE_MAXIMUM")
    minimum_score = finite_float((config.get("candidate_selection") or {}).get("minimum_premarket_score")) if isinstance(config.get("candidate_selection"), Mapping) else None
    minimum_score = minimum_score or 55.0
    candidate_type = str(evidence.get("premarket_candidate_type", "UNSPECIFIED")).upper()
    if candidate_type == "UNSPECIFIED":
        catalyst = evidence.get("catalyst", {}) if isinstance(evidence.get("catalyst"), Mapping) else {}
        gap_percent = abs(finite_float(evidence.get("gap_percent")) or 0.0)
        relative_strength = abs(finite_float(evidence.get("relative_strength_mean")) or 0.0) * 100.0
        relative_volume = finite_float(evidence.get("premarket_relative_volume")) or 0.0
        event_gap = finite_float(filters.get("event_gap_percent")) or 2.0
        event_rvol = finite_float(filters.get("event_relative_volume")) or 1.5
        relative_minimum = finite_float(filters.get("minimum_relative_strength_percent")) or 0.20
        if catalyst.get("confirmed") or gap_percent >= event_gap or relative_volume >= event_rvol:
            candidate_type = "EVENT_DRIVEN"
        elif relative_strength >= relative_minimum:
            candidate_type = "RELATIVE_STRENGTH"
        else:
            candidate_type = "NONE"
    if evidence.get("premarket_eligible") is False:
        failures.extend(str(value) for value in evidence.get("premarket_eligibility_reason_codes", []))
    failures = list(dict.fromkeys(failures))
    candidate_qualified = candidate_type not in {"NONE", "UNSPECIFIED"}
    qualified = not failures and candidate_qualified and scorecard["premarket_score"] >= minimum_score
    decision_reasons = failures
    if not decision_reasons and not candidate_qualified:
        decision_reasons = ["NO_EVENT_OR_RELATIVE_STRENGTH_QUALIFIER"]
    elif not decision_reasons and scorecard["premarket_score"] < minimum_score:
        decision_reasons = ["PREMARKET_SCORE_BELOW_MINIMUM"]
    return {
        "trade_date": str(trade_date)[:10],
        "stage": "premarket",
        "requested_evidence_cutoff": str(requested_cutoff),
        "evidence_cutoff": cutoff.isoformat(),
        "cutoff_capped_to_schedule": cutoff_capped,
        "decision": "PREMARKET_QUALIFIED" if qualified else "PREMARKET_REJECTED",
        "eligible": qualified,
        "candidate_type": candidate_type,
        "direction": scorecard["direction"],
        "premarket_score": scorecard,
        "risk_gates": {
            "passed": not failures,
            "hard_gate_passed": not failures,
            "failures": failures,
            "reason_codes": failures or ["PREMARKET_ELIGIBILITY_PASSED"],
        },
        "reason_codes": decision_reasons or scorecard["reason_codes"],
    }


def run_deterministic_strategy(
    evidence: Mapping[str, Any],
    config: Mapping[str, Any] | None = None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    stage: str = "opening_15m",
    premarket_evidence: Mapping[str, Any] | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Run a deterministic stage and return a JSON-safe, fully audited result.

    Opening input may already contain calculated metrics. If bars are supplied
    and core metrics are absent, the engine calculates them using the capped
    stage cutoff. Extra later bars are excluded before feature and gate use.
    """

    config = config or {}
    normalized_stage = _normalize_stage(stage)
    cutoff, cutoff_capped = _effective_cutoff(trade_date, evidence_cutoff, normalized_stage, config, timezone)
    if normalized_stage == "premarket":
        return json_safe(_premarket_stage(evidence, config, trade_date, cutoff, evidence_cutoff, cutoff_capped, timezone))

    merged: dict[str, Any] = deepcopy(dict(evidence))
    raw_bars_value = merged.get("bars", merged.get("opening_bars"))
    raw_bars = [dict(bar) for bar in raw_bars_value] if isinstance(raw_bars_value, (list, tuple)) else []
    selected_bars = select_completed_bars(
        raw_bars,
        trade_date=trade_date,
        evidence_cutoff=cutoff,
        session_start="09:30",
        timezone=timezone,
    ) if raw_bars_value is not None else []
    risk_bars = select_completed_bars(
        raw_bars,
        trade_date=trade_date,
        evidence_cutoff=cutoff,
        session_start="09:30",
        timezone=timezone,
        sort_bars=False,
    ) if raw_bars_value is not None else []
    public_selected_bars = [clean_public_dict(bar) for bar in selected_bars]

    required_metrics = {"opening_range_high", "opening_range_low", "regular_session_vwap"}
    if raw_bars and not required_metrics.issubset(merged):
        calculated = compute_opening_metrics(
            raw_bars,
            trade_date=trade_date,
            evidence_cutoff=cutoff,
            atr=finite_float(merged.get("atr", merged.get("atr14"))),
            previous_close=finite_float(merged.get("previous_close", merged.get("prev_close"))),
            previous_day_high=finite_float(merged.get("previous_day_high", merged.get("prev_high"))),
            previous_day_low=finite_float(merged.get("previous_day_low", merged.get("prev_low"))),
            premarket_high=finite_float(merged.get("premarket_high")),
            premarket_low=finite_float(merged.get("premarket_low")),
            expected_opening_volume=merged.get("expected_opening_volume"),
            expected_bar_volume=merged.get("expected_bar_volume"),
            benchmark_bars=merged.get("benchmark_bars") if isinstance(merged.get("benchmark_bars"), Mapping) else None,
            sector_symbol=str(merged.get("sector_symbol", "")) or None,
            industry_symbol=str(merged.get("industry_symbol", "")) or None,
            timezone=timezone,
        )
        merged = {**calculated, **merged}
    # Every downstream consumer sees only complete, cutoff-safe bars.
    if raw_bars_value is not None:
        merged["bars"] = public_selected_bars
        merged.pop("opening_bars", None)
    merged["excluded_bar_count"] = len(raw_bars) - len(selected_bars)

    premarket = _premarket_card(merged, premarket_evidence, config, trade_date, cutoff, timezone)
    preliminary_opening = score_opening(
        merged,
        config,
        trade_date=trade_date,
        evidence_cutoff=cutoff,
        timezone=timezone,
    )
    preferred = preliminary_opening["direction"] if preliminary_opening["direction"] in {"LONG", "SHORT"} else premarket.get("direction")
    setup = classify_setup(
        merged,
        config,
        trade_date=trade_date,
        evidence_cutoff=cutoff,
        preferred_direction=preferred,
        timezone=timezone,
    )
    score_evidence = {**merged, **setup}
    opening = score_opening(
        score_evidence,
        config,
        trade_date=trade_date,
        evidence_cutoff=cutoff,
        timezone=timezone,
    )

    combined_section = config.get("combined_scoring", {}) if isinstance(config.get("combined_scoring"), Mapping) else {}
    combined_weights = validate_weights(
        {
            "premarket": finite_float(combined_section.get("premarket_weight")) if combined_section.get("premarket_weight") is not None else 0.40,
            "opening": finite_float(combined_section.get("opening_weight")) if combined_section.get("opening_weight") is not None else 0.60,
        },
        1.0,
        "combined scoring",
    )
    combined_long = clamp(float(premarket.get("long_score", premarket.get("premarket_score", 50.0))) * combined_weights["premarket"] + opening["long_score"] * combined_weights["opening"])
    combined_short = clamp(float(premarket.get("short_score", premarket.get("premarket_score", 50.0))) * combined_weights["premarket"] + opening["short_score"] * combined_weights["opening"])
    conflict_limit = finite_float((config.get("risk_gates") or {}).get("directional_conflict_max_difference")) if isinstance(config.get("risk_gates"), Mapping) else None
    conflict_limit = conflict_limit or 8.0
    combined_difference = abs(combined_long - combined_short)
    combined_direction = "WATCH" if combined_difference <= conflict_limit else "LONG" if combined_long > combined_short else "SHORT"
    setup_direction = setup["direction"] if setup["direction"] in {"LONG", "SHORT"} else None
    selected_direction = setup_direction or (combined_direction if combined_direction in {"LONG", "SHORT"} else opening["direction"])
    directional_conflict = combined_direction == "WATCH" or (setup_direction is not None and combined_direction in {"LONG", "SHORT"} and setup_direction != combined_direction)
    selected_combined = combined_long if selected_direction == "LONG" else combined_short if selected_direction == "SHORT" else max(combined_long, combined_short)

    entry_plan = None
    if normalized_stage == "opening_15m":
        entry_plan = build_entry_plan(
            merged,
            setup,
            config,
            trade_date=trade_date,
            evidence_cutoff=cutoff,
            direction=selected_direction if selected_direction in {"LONG", "SHORT"} else None,
            timezone=timezone,
        )
    gate_evidence = {**merged, **setup}
    if raw_bars_value is not None:
        gate_evidence["bars"] = [clean_public_dict(bar) for bar in risk_bars]
    gate_entry_plan = entry_plan if entry_plan and setup["setup_type"] != "NO_VALID_SETUP" and not setup["failure_override"] else None
    gates = evaluate_risk_gates(
        gate_evidence,
        config,
        trade_date=trade_date,
        evidence_cutoff=cutoff,
        direction=selected_direction if selected_direction in {"LONG", "SHORT"} else None,
        entry_plan=gate_entry_plan,
        timezone=timezone,
    )

    decision_config = config.get("decision_thresholds", {}) if isinstance(config.get("decision_thresholds"), Mapping) else {}
    confirmed_threshold = finite_float(decision_config.get("confirmed_score")) or 65.0
    watch_threshold = finite_float(decision_config.get("watch_score")) or 55.0
    failures = set(gates["failures"])
    data_failure = bool(failures & DATA_FAILURES)
    failed_thesis = bool(setup["failure_override"] or setup["setup_type"] in FAILURE_SETUPS)
    no_valid_setup = setup["setup_type"] == "NO_VALID_SETUP"
    reason_codes: list[str] = []
    if normalized_stage == "opening_5m":
        if data_failure:
            decision = "INSUFFICIENT_DATA"
            reason_codes.extend(sorted(failures & DATA_FAILURES))
        elif failures or failed_thesis:
            decision = "EARLY_REJECTED"
            reason_codes.extend(gates["failures"] or setup["failed_setups"])
        elif directional_conflict:
            decision = "EARLY_REJECTED"
            reason_codes.append("DIRECTIONAL_CONFLICT")
        elif selected_direction in {"LONG", "SHORT"} and not no_valid_setup and selected_combined >= confirmed_threshold:
            decision = f"EARLY_CONFIRMED_{selected_direction}"
            reason_codes.append("EARLY_SETUP_CONFIRMED")
        elif selected_direction in {"LONG", "SHORT"} and selected_combined >= watch_threshold:
            decision = f"WATCH_{selected_direction}"
            reason_codes.append("PROVISIONAL_WATCH")
        else:
            decision = "EARLY_REJECTED"
            reason_codes.append("NO_QUALIFYING_EARLY_SETUP")
    else:
        if data_failure:
            decision = "INSUFFICIENT_DATA"
            reason_codes.extend(sorted(failures & DATA_FAILURES))
        elif failures & {"NO_VALID_STOP_LOCATION", "REWARD_RISK_BELOW_MINIMUM"}:
            decision = "NO_TRADE"
            reason_codes.extend(gates["failures"])
        elif failures or failed_thesis:
            decision = "REJECTED"
            reason_codes.extend(gates["failures"] or setup["failed_setups"])
        elif directional_conflict:
            decision = "NO_TRADE"
            reason_codes.append("DIRECTIONAL_CONFLICT")
        elif no_valid_setup or selected_direction not in {"LONG", "SHORT"}:
            decision = "NO_TRADE"
            reason_codes.append("NO_VALID_SETUP")
        elif selected_combined >= confirmed_threshold:
            decision = f"CONFIRMED_{selected_direction}"
            reason_codes.append("FINAL_SETUP_CONFIRMED")
        elif selected_combined >= watch_threshold:
            decision = f"WATCH_{selected_direction}"
            reason_codes.append("FINAL_WATCH")
        else:
            decision = "NO_TRADE"
            reason_codes.append("COMBINED_SCORE_BELOW_WATCH_THRESHOLD")

    result = {
        "trade_date": str(trade_date)[:10],
        "stage": normalized_stage,
        "requested_evidence_cutoff": str(evidence_cutoff),
        "evidence_cutoff": cutoff.isoformat(),
        "cutoff_capped_to_schedule": cutoff_capped,
        "decision": decision,
        "direction": selected_direction if selected_direction in {"LONG", "SHORT"} else "WATCH",
        "directional_conflict": directional_conflict,
        "premarket_score": premarket,
        "opening_score": opening,
        "combined_score": round(selected_combined, 4),
        "combined_long_score": round(combined_long, 4),
        "combined_short_score": round(combined_short, 4),
        "combined_score_weights": combined_weights,
        "setup": setup,
        "risk_gates": gates,
        "entry_plan": entry_plan,
        "opening_metrics": {key: value for key, value in merged.items() if key not in {"bars", "benchmark_bars"}},
        "completed_bar_count": len(selected_bars) if raw_bars_value is not None else merged.get("completed_bar_count"),
        "excluded_bar_count": len(raw_bars) - len(selected_bars),
        "failed_thesis_override": failed_thesis,
        "hard_gate_override": not gates["passed"],
        "reason_codes": list(dict.fromkeys(reason_codes)),
    }
    return json_safe(result)


class DeterministicStrategyEngine:
    """Thin immutable-config facade over :func:`run_deterministic_strategy`."""

    def __init__(self, config: Mapping[str, Any], *, timezone: str = DEFAULT_TIMEZONE) -> None:
        self._config = deepcopy(dict(config))
        self._timezone = timezone

    def analyze(
        self,
        evidence: Mapping[str, Any],
        *,
        trade_date: str | date | datetime,
        evidence_cutoff: str | time | datetime,
        stage: str = "opening_15m",
        premarket_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return run_deterministic_strategy(
            evidence,
            self._config,
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoff,
            stage=stage,
            premarket_evidence=premarket_evidence,
            timezone=self._timezone,
        )


evaluate_candidate = run_deterministic_strategy


__all__ = ["DeterministicStrategyEngine", "evaluate_candidate", "run_deterministic_strategy"]
