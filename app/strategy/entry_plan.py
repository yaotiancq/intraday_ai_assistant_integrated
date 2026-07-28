"""Analysis-only conditional entries, structural stops, targets, and sizing."""

from __future__ import annotations

from datetime import date, datetime, time
import math
from typing import Any, Mapping

from app.indicators._bars import DEFAULT_TIMEZONE, finite_float, parse_cutoff
from app.strategy.setup_classifier import FAILURE_SETUPS, LONG_SETUPS, SHORT_SETUPS


def _number(metrics: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = finite_float(metrics.get(key))
        if value is not None:
            return value
    return None


def _first_level(metrics: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[float | None, str | None]:
    for key in keys:
        value = finite_float(metrics.get(key))
        if value is not None:
            return value, key.upper()
    return None, None


def build_entry_plan(
    metrics: Mapping[str, Any],
    setup: Mapping[str, Any] | str,
    config: Mapping[str, Any] | None = None,
    *,
    trade_date: str | date | datetime,
    evidence_cutoff: str | time | datetime,
    direction: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> dict[str, Any]:
    """Build a non-executable plan from observable levels and risk multiples."""

    config = config or {}
    setup_type = str(setup.get("setup_type", setup.get("primary_setup", "NO_VALID_SETUP")) if isinstance(setup, Mapping) else setup).upper()
    setup_direction = str(setup.get("direction", "") if isinstance(setup, Mapping) else "").upper()
    selected_direction = str(direction or setup_direction).upper()
    if selected_direction not in {"LONG", "SHORT"}:
        if setup_type in LONG_SETUPS:
            selected_direction = "LONG"
        elif setup_type in SHORT_SETUPS:
            selected_direction = "SHORT"
    cutoff = parse_cutoff(trade_date, evidence_cutoff, timezone)
    base = {
        "trade_date": str(trade_date)[:10],
        "evidence_cutoff": cutoff.isoformat(),
        "direction": selected_direction or None,
        "setup_type": setup_type,
        "analysis_only": True,
        "order_submission_enabled": False,
    }
    if setup_type in FAILURE_SETUPS or setup_type == "NO_VALID_SETUP" or selected_direction not in {"LONG", "SHORT"}:
        return {
            **base,
            "status": "NO_TRADE",
            "valid_plan": False,
            "valid_stop": False,
            "entry_valid": False,
            "reason_codes": ["FAILED_SETUP_OVERRIDE" if setup_type in FAILURE_SETUPS else "NO_VALID_SETUP"],
        }

    risk_gates = config.get("risk_gates", {}) if isinstance(config.get("risk_gates"), Mapping) else {}
    risk_management = config.get("risk_management", {}) if isinstance(config.get("risk_management"), Mapping) else {}
    atr = _number(metrics, "atr", "atr14")
    max_extension_atr = finite_float(risk_gates.get("maximum_entry_extension_atr")) or 0.35
    minimum_rr = finite_float(risk_gates.get("minimum_reward_risk_ratio")) or 1.5
    max_risk_dollars = finite_float(risk_management.get("maximum_risk_per_trade_dollars")) or 150.0
    max_notional = finite_float(risk_management.get("maximum_position_notional_dollars")) or 10_000.0
    slippage_bps = finite_float(risk_management.get("estimated_slippage_bps")) or 5.0

    long_entry_keys: dict[str, tuple[str, ...]] = {
        "OPENING_RANGE_BREAKOUT": ("initial_5m_high", "opening_range_high"),
        "PREMARKET_HIGH_BREAKOUT": ("premarket_high",),
        "VWAP_RECLAIM": ("regular_session_vwap", "vwap"),
        "FIRST_PULLBACK_LONG": ("recent_swing_high", "last_high", "current_price"),
        "GAP_AND_GO_LONG": ("last_high", "current_price", "opening_range_high"),
        "OPENING_DRIVE_LONG": ("last_high", "current_price", "opening_range_high"),
    }
    short_entry_keys: dict[str, tuple[str, ...]] = {
        "OPENING_RANGE_BREAKDOWN": ("initial_5m_low", "opening_range_low"),
        "PREMARKET_LOW_BREAKDOWN": ("premarket_low",),
        "VWAP_REJECTION": ("regular_session_vwap", "vwap"),
        "FIRST_PULLBACK_SHORT": ("recent_swing_low", "last_low", "current_price"),
        "GAP_AND_GO_SHORT": ("last_low", "current_price", "opening_range_low"),
        "OPENING_DRIVE_SHORT": ("last_low", "current_price", "opening_range_low"),
    }
    explicit_entry = _number(metrics, "entry_reference_level", "entry_level")
    if explicit_entry is not None:
        entry, entry_basis = explicit_entry, "EXPLICIT_OBSERVABLE_ENTRY_LEVEL"
    else:
        keys = (long_entry_keys if selected_direction == "LONG" else short_entry_keys).get(setup_type, ("current_price", "last_price"))
        entry, entry_basis = _first_level(metrics, keys)
    if entry is None or entry <= 0:
        return {
            **base,
            "status": "NO_TRADE",
            "valid_plan": False,
            "valid_stop": False,
            "entry_valid": False,
            "reason_codes": ["NO_VALID_ENTRY_REFERENCE"],
        }

    explicit_stop = _number(metrics, "initial_stop_reference", "stop_reference", "stop_level")
    if explicit_stop is not None:
        stop, stop_basis = explicit_stop, "EXPLICIT_STRUCTURAL_STOP"
    elif selected_direction == "LONG":
        stop_candidates = [
            (value, key.upper())
            for key in ("recent_swing_low", "regular_session_vwap", "vwap", "initial_5m_low", "opening_range_low", "premarket_low")
            if (value := finite_float(metrics.get(key))) is not None and 0 < value < entry
        ]
        stop, stop_basis = max(stop_candidates, default=(None, None), key=lambda item: item[0] or 0.0)
    else:
        stop_candidates = [
            (value, key.upper())
            for key in ("recent_swing_high", "regular_session_vwap", "vwap", "initial_5m_high", "opening_range_high", "premarket_high")
            if (value := finite_float(metrics.get(key))) is not None and value > entry
        ]
        stop, stop_basis = min(stop_candidates, default=(None, None), key=lambda item: item[0] or float("inf"))

    valid_stop = stop is not None and stop > 0 and ((stop < entry) if selected_direction == "LONG" else (stop > entry))
    if not valid_stop:
        return {
            **base,
            "status": "NO_TRADE",
            "valid_plan": False,
            "valid_stop": False,
            "entry_reference_level": entry,
            "entry_reference_basis": entry_basis,
            "entry_valid": False,
            "reason_codes": ["NO_VALID_STOP_LOCATION"],
        }

    structural_risk = abs(entry - stop)
    estimated_slippage_per_share = entry * slippage_bps / 10_000.0
    risk_per_share = structural_risk + estimated_slippage_per_share
    explicit_target = _number(metrics, "target_reference", "target_1", "first_target_reference")
    target_basis: str
    if explicit_target is not None:
        target_1 = explicit_target
        target_2 = _number(metrics, "target_2", "second_target_reference")
        target_basis = "EXPLICIT_OBSERVABLE_TARGET"
    else:
        if selected_direction == "LONG":
            target_candidates = sorted({
                value
                for key in ("opening_range_high", "premarket_high", "previous_day_high", "recent_swing_high")
                if (value := finite_float(metrics.get(key))) is not None and value > entry
            })
        else:
            target_candidates = sorted({
                value
                for key in ("opening_range_low", "premarket_low", "previous_day_low", "recent_swing_low")
                if (value := finite_float(metrics.get(key))) is not None and 0 < value < entry
            }, reverse=True)
        target_1 = target_candidates[0] if target_candidates else None
        target_2 = target_candidates[1] if len(target_candidates) > 1 else None
        target_basis = "OBSERVABLE_RESISTANCE" if selected_direction == "LONG" else "OBSERVABLE_SUPPORT"
        if target_1 is None:
            sign = 1.0 if selected_direction == "LONG" else -1.0
            target_1 = entry + sign * structural_risk
            target_2 = entry + sign * structural_risk * max(2.0, minimum_rr)
            target_basis = "RISK_MULTIPLES_FROM_STRUCTURAL_STOP"

    def reward_ratio(target: float | None) -> float | None:
        if target is None or structural_risk <= 0:
            return None
        reward = target - entry if selected_direction == "LONG" else entry - target
        return reward / risk_per_share if reward > 0 else 0.0

    target_1_rr = reward_ratio(target_1)
    target_2_rr = reward_ratio(target_2)
    expected_rr = max(value for value in (target_1_rr, target_2_rr) if value is not None) if any(value is not None for value in (target_1_rr, target_2_rr)) else None
    theoretical_quantity = max(0, math.floor(max_risk_dollars / risk_per_share)) if risk_per_share > 0 else 0
    notional_quantity = max(0, math.floor(max_notional / entry)) if entry > 0 else 0
    quantity = min(theoretical_quantity, notional_quantity)
    extension_dollars = atr * max_extension_atr if atr else None
    maximum_entry_level = entry + extension_dollars if extension_dollars is not None and selected_direction == "LONG" else entry - extension_dollars if extension_dollars is not None else None
    explicit_trigger_state = metrics.get("entry_trigger_completed", metrics.get("trigger_completed"))
    current_price = _number(metrics, "current_price", "last_price", "last_close")
    if explicit_trigger_state is not None:
        trigger_completed = bool(explicit_trigger_state)
    elif setup_type in {"OPENING_RANGE_BREAKOUT", "PREMARKET_HIGH_BREAKOUT"}:
        trigger_completed = current_price is not None and current_price > entry
    elif setup_type in {"OPENING_RANGE_BREAKDOWN", "PREMARKET_LOW_BREAKDOWN"}:
        trigger_completed = current_price is not None and current_price < entry
    elif setup_type == "VWAP_RECLAIM":
        trigger_completed = bool(metrics.get("crossed_above_vwap")) and current_price is not None and current_price > entry
    elif setup_type == "VWAP_REJECTION":
        trigger_completed = bool(metrics.get("crossed_below_vwap")) and current_price is not None and current_price < entry
    else:
        trigger_completed = False
    trigger = {
        "OPENING_RANGE_BREAKOUT": "TRADE_ABOVE_INITIAL_5M_HIGH_AND_HOLD_ABOVE_VWAP",
        "OPENING_RANGE_BREAKDOWN": "TRADE_BELOW_INITIAL_5M_LOW_AND_HOLD_BELOW_VWAP",
        "PREMARKET_HIGH_BREAKOUT": "TRADE_ABOVE_PREMARKET_HIGH",
        "PREMARKET_LOW_BREAKDOWN": "TRADE_BELOW_PREMARKET_LOW",
        "VWAP_RECLAIM": "CLOSE_ABOVE_REGULAR_SESSION_VWAP_AFTER_CROSSING_FROM_BELOW",
        "VWAP_REJECTION": "CLOSE_BELOW_REGULAR_SESSION_VWAP_AFTER_REJECTION_FROM_ABOVE",
        "FIRST_PULLBACK_LONG": "BREAK_ABOVE_FIRST_PULLBACK_SWING_HIGH_WHILE_HOLDING_VWAP",
        "FIRST_PULLBACK_SHORT": "BREAK_BELOW_FIRST_PULLBACK_SWING_LOW_WHILE_BELOW_VWAP",
        "GAP_AND_GO_LONG": "BREAK_OPENING_SWING_HIGH_WITH_GAP_RETAINED",
        "GAP_AND_GO_SHORT": "BREAK_OPENING_SWING_LOW_WITH_GAP_RETAINED",
        "OPENING_DRIVE_LONG": "BREAK_OPENING_DRIVE_HIGH_WITH_VOLUME_CONFIRMATION",
        "OPENING_DRIVE_SHORT": "BREAK_OPENING_DRIVE_LOW_WITH_VOLUME_CONFIRMATION",
    }.get(setup_type, "COMPLETE_CONFIGURED_PRICE_TRIGGER")
    cancellation = [
        "HARD_RISK_GATE_FAILURE",
        "DATA_BECOMES_STALE_OR_INCOMPLETE",
        "SPREAD_EXCEEDS_HARD_MAXIMUM",
        "ENTRY_EXCEEDS_MAXIMUM_EXTENSION",
        "SETUP_TRIGGER_NOT_COMPLETED",
        "REWARD_RISK_FALLS_BELOW_MINIMUM",
    ]
    invalidation = (
        f"PRICE_AT_OR_BELOW_{stop_basis}" if selected_direction == "LONG" else f"PRICE_AT_OR_ABOVE_{stop_basis}"
    )
    return {
        **base,
        "status": "CONDITIONAL",
        "valid_plan": True,
        "valid_stop": True,
        "entry_trigger": trigger,
        "entry_trigger_completed": trigger_completed,
        "entry_valid": trigger_completed,
        "entry_reference_level": entry,
        "entry_reference_basis": entry_basis,
        "maximum_acceptable_entry_extension_atr": max_extension_atr,
        "maximum_acceptable_entry_extension_dollars": extension_dollars,
        "maximum_acceptable_entry_level": maximum_entry_level,
        "invalidation_condition": invalidation,
        "initial_stop_reference": stop,
        "initial_stop_basis": stop_basis,
        "structural_risk_per_share": structural_risk,
        "estimated_slippage_bps": slippage_bps,
        "estimated_slippage_per_share": estimated_slippage_per_share,
        "risk_per_share": risk_per_share,
        "first_target_reference": target_1,
        "first_target_reward_risk_ratio": target_1_rr,
        "second_target_reference": target_2,
        "second_target_reward_risk_ratio": target_2_rr,
        "target_basis": target_basis,
        "expected_reward_risk_ratio": expected_rr,
        "minimum_required_reward_risk_ratio": minimum_rr,
        "cancellation_conditions": cancellation,
        "position_sizing": {
            "maximum_risk_per_trade_dollars": max_risk_dollars,
            "maximum_position_notional_dollars": max_notional,
            "maximum_theoretical_share_quantity": theoretical_quantity,
            "maximum_quantity_after_notional_limit": notional_quantity,
            "analysis_share_quantity": quantity,
            "estimated_position_notional": quantity * entry,
            "estimated_total_risk": quantity * risk_per_share,
            "estimated_total_slippage": quantity * estimated_slippage_per_share,
        },
        "reason_codes": ["CONDITIONAL_ENTRY_PLAN_CREATED", "TRIGGER_COMPLETED" if trigger_completed else "TRIGGER_NOT_COMPLETED"],
    }


generate_entry_plan = build_entry_plan


__all__ = ["build_entry_plan", "generate_entry_plan"]
