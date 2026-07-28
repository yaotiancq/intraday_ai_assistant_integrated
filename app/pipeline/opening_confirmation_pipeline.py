from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.data_sources.market_data import MarketDataSource
from app.indicators._bars import finite_float
from app.models.universe_models import FixedUniverseSnapshot
from app.reporting.market_report_builder import build_final_report_payload
from app.strategy import run_deterministic_strategy
from app.validators.snapshot_validator import validate_bar_evidence


def run_opening_confirmation_pipeline(
    *,
    trade_date: str,
    as_of: datetime,
    evidence_cutoff: datetime,
    stage: str,
    universe: FixedUniverseSnapshot,
    config: Mapping[str, Any],
    data_source: MarketDataSource,
    premarket_snapshot: Mapping[str, Any],
    opening_5m_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Confirm only the immutable watchlist persisted by the premarket stage."""

    normalized_stage = stage.strip().lower().replace("-", "_")
    if normalized_stage not in {"opening_5m", "opening_15m"}:
        raise ValueError(f"unsupported opening stage: {stage}")
    expected_bars = 5 if normalized_stage == "opening_5m" else 15
    watchlist = list(premarket_snapshot.get("opening_watchlist", []) or [])
    configured = set(universe.stock_symbols)
    symbols = [str(item.get("symbol", "")).upper() for item in watchlist]
    outside = sorted(set(symbols) - configured)
    if outside:
        raise ValueError("persisted watchlist contains unconfigured symbols: " + ",".join(outside))
    if len(symbols) != len(set(symbols)):
        raise ValueError("persisted watchlist contains duplicate symbols")
    if len(symbols) > int(config["candidate_selection"]["maximum_opening_watchlist"]):
        raise ValueError("persisted watchlist exceeds configured maximum")

    stock_definitions = universe.stock_by_symbol()
    premarket_by_symbol = {str(item.get("symbol", "")).upper(): item for item in watchlist}
    early_by_symbol = {
        str(item.get("symbol", "")).upper(): item
        for item in (opening_5m_snapshot or {}).get("candidates", []) or []
    }
    required_benchmarks = {"SPY", "QQQ"}
    for symbol in symbols:
        required_benchmarks.update(stock_definitions[symbol].comparison_etfs)
    benchmark_bars: dict[str, list[dict[str, Any]]] = {}
    benchmark_validation: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for benchmark in sorted(required_benchmarks):
        try:
            raw_benchmark_bars = data_source.minute(benchmark, trade_date, evidence_cutoff)
            validation = validate_bar_evidence(
                raw_benchmark_bars,
                trade_date=trade_date,
                evidence_cutoff=evidence_cutoff,
                session_start="09:30",
                expected_bar_count=expected_bars,
            )
            benchmark_validation[benchmark] = {
                key: value for key, value in validation.items() if key != "accepted_bars"
            }
            benchmark_bars[benchmark] = validation["accepted_bars"]
            if not validation["passed"]:
                warnings.append(
                    f"{benchmark}:BENCHMARK_BAR_VALIDATION_FAILED:"
                    + ",".join(validation["reason_codes"])
                )
        except Exception as exc:
            benchmark_bars[benchmark] = []
            benchmark_validation[benchmark] = {
                "passed": False,
                "reason_codes": ["BENCHMARK_MINUTE_BARS_FAILED"],
            }
            warnings.append(f"{benchmark}:BENCHMARK_MINUTE_BARS_FAILED:{_safe_error(exc)}")

    results: list[dict[str, Any]] = []
    for symbol in symbols:
        premarket = premarket_by_symbol[symbol]
        definition = stock_definitions[symbol]
        features = premarket.get("features", {}) or {}
        levels = premarket.get("technical_levels", {}) or {}
        try:
            bars = data_source.minute(symbol, trade_date, evidence_cutoff)
        except Exception as exc:
            bars = []
            warnings.append(f"{symbol}:OPENING_MINUTE_BARS_FAILED:{_safe_error(exc)}")
        validation = validate_bar_evidence(
            bars,
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoff,
            session_start="09:30",
            expected_bar_count=expected_bars,
        )
        accepted_bars = validation["accepted_bars"]
        comparison_symbols = ["SPY", "QQQ", *definition.comparison_etfs]
        comparison_symbols = list(dict.fromkeys(comparison_symbols))
        comparisons = {symbol_: benchmark_bars.get(symbol_, []) for symbol_ in comparison_symbols}
        sector_symbol = _primary_sector_symbol(definition.comparison_etfs)
        industry_symbol = _industry_symbol(definition.comparison_etfs)
        average_daily_dollars = finite_float(features.get("average_daily_dollar_volume"))
        reference_price = finite_float(features.get("last_price"))
        expected_volume = _expected_opening_volume(
            average_daily_dollars=average_daily_dollars,
            reference_price=reference_price,
            window_minutes=expected_bars,
            explicit=finite_float(features.get(f"expected_opening_volume_{expected_bars}m")),
        )
        current_spread = finite_float(accepted_bars[-1].get("spread_bps")) if accepted_bars else None
        atr = finite_float(levels.get("atr", features.get("atr")))
        gap = finite_float(features.get("gap_return"))
        missing_broad = any(
            not benchmark_validation.get(value, {}).get("passed", False)
            for value in ("SPY", "QQQ")
        )
        missing_sector = sector_symbol is not None and not benchmark_validation.get(
            sector_symbol, {}
        ).get("passed", False)
        premarket_scorecard = {
            "premarket_score": premarket.get("premarket_score"),
            "score": premarket.get("premarket_score"),
            "long_score": premarket.get("long_score"),
            "short_score": premarket.get("short_score"),
            "direction": premarket.get("direction"),
            "directional_conflict": premarket.get("directional_conflict", False),
            "factor_breakdown": premarket.get("score_factors", {}),
            "long_factor_breakdown": premarket.get("long_score_factors", {}),
            "short_factor_breakdown": premarket.get("short_score_factors", {}),
            "reason_codes": premarket.get("reason_codes", []),
        }
        evidence = {
            "symbol": symbol,
            "bars": accepted_bars,
            "expected_bar_count": expected_bars,
            "atr": atr,
            "previous_close": features.get("previous_close"),
            "previous_day_high": levels.get("previous_day_high"),
            "previous_day_low": levels.get("previous_day_low"),
            "premarket_high": levels.get("premarket_high"),
            "premarket_low": levels.get("premarket_low"),
            "expected_opening_volume": expected_volume,
            "benchmark_bars": comparisons,
            "sector_symbol": sector_symbol,
            "industry_symbol": industry_symbol,
            "spread_bps": current_spread,
            "average_daily_dollar_volume": average_daily_dollars,
            "premarket_dollar_volume": features.get("premarket_dollar_volume"),
            "gap_atr_ratio": abs(gap * float(features.get("previous_close") or 0.0) / atr) if gap is not None and atr else None,
            "benchmark_data_complete": not missing_broad,
            "sector_data_complete": not missing_sector,
            "missing_benchmark_data": missing_broad,
            "missing_sector_data": missing_sector,
            "premarket_scorecard": premarket_scorecard,
            "market_regime": premarket_snapshot.get("market_regime", {}),
            "validation_reason_codes": validation["reason_codes"],
            "benchmark_bar_validation": benchmark_validation,
        }
        strategy_result = run_deterministic_strategy(
            evidence,
            config,
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoff,
            stage=normalized_stage,
        )
        gates = dict(strategy_result["risk_gates"])
        gates["failed_gates"] = list(gates.get("failures", []))
        entry_plan = strategy_result.get("entry_plan")
        early = early_by_symbol.get(symbol)
        reason_codes = list(strategy_result.get("reason_codes", []))
        if normalized_stage == "opening_15m":
            if early is None:
                reason_codes.append("FIVE_MINUTE_SNAPSHOT_UNAVAILABLE")
            elif _decision_direction(early.get("decision")) == strategy_result.get("direction"):
                reason_codes.append("EARLY_MOVE_PERSISTED")
            else:
                reason_codes.append("EARLY_MOVE_DID_NOT_PERSIST")
        opening_scorecard = strategy_result["opening_score"]
        result = {
            "symbol": symbol,
            "company_name": definition.company_name,
            "sector": definition.sector,
            "sector_zh": definition.sector_zh,
            "industry": definition.industry,
            "comparison_etfs": list(definition.comparison_etfs),
            "decision": strategy_result["decision"],
            "direction": strategy_result["direction"],
            "directional_conflict": strategy_result["directional_conflict"],
            "setup_type": strategy_result["setup"]["setup_type"],
            "setup": strategy_result["setup"],
            "premarket_score": premarket.get("premarket_score"),
            "opening_score": opening_scorecard["opening_score"],
            "combined_score": strategy_result["combined_score"],
            "long_score": strategy_result["combined_long_score"],
            "short_score": strategy_result["combined_short_score"],
            "opening_score_factors": opening_scorecard["factor_breakdown"],
            "opening_penalties": opening_scorecard["penalty_breakdown"],
            "opening_metrics": strategy_result["opening_metrics"],
            "bar_validation": {key: value for key, value in validation.items() if key != "accepted_bars"},
            "risk_gates": gates,
            "entry_plan": entry_plan,
            "reason_codes": list(dict.fromkeys(reason_codes)),
            "early_confirmation": early,
            "strategy_result": strategy_result,
        }
        results.append(result)

    payload: dict[str, Any] = {
        "status": "COMPLETED",
        "stage": normalized_stage,
        "trade_date": trade_date,
        "as_of": as_of.isoformat(),
        "evidence_cutoff": evidence_cutoff.isoformat(),
        "strategy_version": universe.strategy_version,
        "data_source": data_source.source_name,
        "configured_stock_count": len(universe.stock_symbols),
        "analyzed_symbols": symbols,
        "outside_symbols_analyzed": [],
        "source_premarket_run_key": premarket_snapshot.get("run_key"),
        "source_opening_5m_run_key": (opening_5m_snapshot or {}).get("run_key"),
        "candidates": results,
        "data_quality_warnings": sorted(set(warnings)),
        "reason_codes": ["NO_TRADE"] if not any(_is_actionable(item["decision"]) for item in results) else ["CANDIDATES_CLASSIFIED"],
    }
    if normalized_stage == "opening_15m":
        payload = build_final_report_payload(payload)
    return payload


def _expected_opening_volume(
    *,
    average_daily_dollars: float | None,
    reference_price: float | None,
    window_minutes: int,
    explicit: float | None,
) -> float | None:
    if explicit is not None and explicit > 0:
        return explicit
    if average_daily_dollars is None or reference_price is None or reference_price <= 0:
        return None
    average_daily_shares = average_daily_dollars / reference_price
    # Transparent research baseline: 3% of daily volume in the first five
    # minutes, scaled linearly for the 15-minute confirmation window.
    return average_daily_shares * 0.03 * (window_minutes / 5.0)


def _primary_sector_symbol(comparisons: tuple[str, ...]) -> str | None:
    industries = {"SMH", "SOXX", "IGV", "ITA"}
    return next((symbol for symbol in comparisons if symbol not in {"QQQ", *industries}), None)


def _industry_symbol(comparisons: tuple[str, ...]) -> str | None:
    return next((symbol for symbol in comparisons if symbol in {"SMH", "SOXX", "IGV", "ITA"}), None)


def _decision_direction(value: Any) -> str | None:
    text = str(value or "").upper()
    if "LONG" in text:
        return "LONG"
    if "SHORT" in text:
        return "SHORT"
    return None


def _is_actionable(decision: str) -> bool:
    return decision in {"EARLY_CONFIRMED_LONG", "EARLY_CONFIRMED_SHORT", "CONFIRMED_LONG", "CONFIRMED_SHORT", "WATCH_LONG", "WATCH_SHORT"}


def _safe_error(exc: Exception) -> str:
    return (str(exc).replace("\n", " ").strip() or exc.__class__.__name__)[:160]
