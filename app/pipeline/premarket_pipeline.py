from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Mapping

from app.data_sources.market_data import MarketDataSource
from app.models.universe_models import FixedUniverseSnapshot
from app.pipeline.candidate_builder import narrow_candidates
from app.pipeline.news_processor import best_catalyst, classify_news_catalysts
from app.pipeline.premarket_features import apply_premarket_eligibility, build_premarket_features
from app.scoring.market_regime import compute_market_regime
from app.strategy import run_deterministic_strategy


def run_premarket_pipeline(
    *,
    trade_date: str,
    as_of: datetime,
    evidence_cutoff: datetime,
    universe: FixedUniverseSnapshot,
    config: Mapping[str, Any],
    data_source: MarketDataSource,
    universe_validation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate all configured stocks and produce a bounded opening watchlist."""

    if trade_date != universe.trade_date:
        raise ValueError("trade_date must match the fixed-universe snapshot")
    allowed_stocks = tuple(universe.stock_symbols)
    stock_by_symbol = universe.stock_by_symbol()
    benchmark_symbols = tuple(universe.benchmark_symbols)
    warnings: list[str] = []

    stock_snapshots = _safe_snapshots(data_source, allowed_stocks, evidence_cutoff, warnings)
    benchmark_rows = _safe_snapshots(data_source, benchmark_symbols, evidence_cutoff, warnings)
    stock_snapshot_by_symbol = {str(row.get("symbol", "")).upper(): row for row in stock_snapshots}
    benchmark_by_symbol = {str(row.get("symbol", "")).upper(): row for row in benchmark_rows}

    benchmark_category = {item.symbol: item.category for item in universe.benchmarks}
    broad = [row for symbol, row in benchmark_by_symbol.items() if benchmark_category.get(symbol) == "broad_market"]
    sectors = [row for symbol, row in benchmark_by_symbol.items() if benchmark_category.get(symbol) in {"sector", "industry"}]
    volatility = next((row for symbol, row in benchmark_by_symbol.items() if benchmark_category.get(symbol) == "volatility"), None)
    market_regime = compute_market_regime(
        broad,
        sectors,
        volatility,
        as_of=evidence_cutoff,
        maximum_data_age_seconds=int(config["risk_gates"]["maximum_data_age_seconds"]),
    )

    try:
        raw_news = data_source.news(allowed_stocks, evidence_cutoff)
    except Exception as exc:
        warnings.append(f"NEWS_ACQUISITION_FAILED:{_safe_error(exc)}")
        raw_news = []
    catalysts = classify_news_catalysts(
        raw_news,
        allowed_symbols=allowed_stocks,
        as_of=evidence_cutoff,
        config=config.get("news", {}),
    )
    health_by_symbol = _health_map(universe_validation)
    filters = config["premarket_filters"]
    risk_config = config["risk_gates"]
    start_date = (date.fromisoformat(trade_date) - timedelta(days=140)).isoformat()

    evaluated: list[dict[str, Any]] = []
    for symbol in allowed_stocks:
        definition = stock_by_symbol[symbol]
        snapshot = stock_snapshot_by_symbol.get(symbol, {})
        try:
            daily = data_source.daily(symbol, start_date=start_date, end_date=trade_date, as_of=evidence_cutoff)
        except Exception as exc:
            daily = []
            warnings.append(f"{symbol}:DAILY_BARS_FAILED:{_safe_error(exc)}")
        try:
            premarket = data_source.premarket(symbol, trade_date, evidence_cutoff)
        except Exception as exc:
            premarket = []
            warnings.append(f"{symbol}:PREMARKET_BARS_FAILED:{_safe_error(exc)}")

        catalyst_items = catalysts.get(symbol, [])
        catalyst = best_catalyst(catalyst_items)
        features = build_premarket_features(
            symbol=symbol,
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoff,
            snapshot=snapshot,
            daily_bars=daily,
            premarket_bars=premarket,
            benchmark_snapshots=benchmark_by_symbol,
            comparison_etfs=definition.comparison_etfs,
            catalyst=catalyst,
            maximum_data_age_seconds=int(risk_config["maximum_data_age_seconds"]),
        )
        features["market_regime"] = market_regime
        features["market_regime_score"] = market_regime["score"]
        eligibility = apply_premarket_eligibility(features, filters)
        features["premarket_candidate_type"] = eligibility["candidate_type"]
        features["premarket_eligible"] = eligibility["eligible"]
        features["premarket_eligibility_reason_codes"] = eligibility["reason_codes"]
        strategy_result = run_deterministic_strategy(
            features,
            config,
            trade_date=trade_date,
            evidence_cutoff=evidence_cutoff,
            stage="premarket",
        )
        if not strategy_result["risk_gates"]["passed"]:
            eligibility["eligible"] = False
            eligibility["qualified"] = False
            eligibility["reason_codes"] = sorted(set([
                *eligibility["reason_codes"],
                *strategy_result["risk_gates"]["failures"],
            ]))
        eligibility["qualified"] = bool(strategy_result["eligible"])
        health_state = health_by_symbol.get(symbol, "ACTIVE")
        health_ok = health_state == "ACTIVE"
        if not health_ok:
            eligibility["eligible"] = False
            eligibility["qualified"] = False
            eligibility["reason_codes"] = sorted(set([*eligibility["reason_codes"], f"UNIVERSE_HEALTH_{health_state}"]))

        score = strategy_result["premarket_score"]
        row = {
            "symbol": symbol,
            "company_name": definition.company_name,
            "sector": definition.sector,
            "sector_zh": definition.sector_zh,
            "industry": definition.industry,
            "comparison_etfs": list(definition.comparison_etfs),
            "health_state": health_state,
            "eligible": eligibility["eligible"],
            "qualified": eligibility["qualified"],
            "candidate_type": eligibility["candidate_type"],
            "direction": score["direction"],
            "directional_conflict": score["directional_conflict"],
            "premarket_score": score["premarket_score"],
            "long_score": score["long_score"],
            "short_score": score["short_score"],
            "score_factors": score["factor_breakdown"],
            "long_score_factors": score["long_factor_breakdown"],
            "short_score_factors": score["short_factor_breakdown"],
            "reason_codes": sorted(set([
                *eligibility["reason_codes"],
                *score["reason_codes"],
                *strategy_result["reason_codes"],
            ])),
            "catalyst": catalyst,
            "catalysts": catalyst_items,
            "features": features,
            "technical_levels": {
                key: features.get(key)
                for key in ("previous_day_high", "previous_day_low", "premarket_high", "premarket_low", "atr")
            },
            "strategy_result": strategy_result,
            "disposition": "ELIGIBLE" if eligibility["qualified"] else "REJECTED",
        }
        evaluated.append(row)

    selection = config["candidate_selection"]
    qualified = [item for item in evaluated if item["qualified"]]
    premarket_candidates = narrow_candidates(
        qualified,
        minimum_score=float(selection["minimum_premarket_score"]),
        maximum_candidates=int(selection["maximum_premarket_candidates"]),
        maximum_per_sector=int(selection["maximum_candidates_per_sector"]),
    )
    opening_watchlist = narrow_candidates(
        premarket_candidates,
        minimum_score=float(selection["minimum_premarket_score"]),
        maximum_candidates=int(selection["maximum_opening_watchlist"]),
        maximum_per_sector=int(selection["maximum_candidates_per_sector"]),
    )
    candidate_symbols = {item["symbol"] for item in premarket_candidates}
    watch_symbols = {item["symbol"] for item in opening_watchlist}
    for item in evaluated:
        if item["symbol"] in watch_symbols:
            item["disposition"] = "OPENING_WATCHLIST"
        elif item["symbol"] in candidate_symbols:
            item["disposition"] = "PREMARKET_CANDIDATE"
        else:
            item["disposition"] = "REJECTED"

    return {
        "status": "COMPLETED",
        "stage": "premarket",
        "trade_date": trade_date,
        "as_of": as_of.isoformat(),
        "evidence_cutoff": evidence_cutoff.isoformat(),
        "strategy_version": universe.strategy_version,
        "data_source": data_source.source_name,
        "universe_snapshot": universe.to_dict(),
        "configured_stock_count": len(allowed_stocks),
        "configured_benchmark_count": len(benchmark_symbols),
        "analyzed_symbols": list(allowed_stocks),
        "outside_symbols_analyzed": [],
        "market_regime": market_regime,
        "benchmark_snapshots": benchmark_by_symbol,
        "evaluated_stocks": evaluated,
        "eligible_candidates": premarket_candidates,
        "opening_watchlist": opening_watchlist,
        "rejected_candidates": [item for item in evaluated if item["disposition"] == "REJECTED"],
        "data_quality_warnings": sorted(set(warnings)),
        "no_trade": not opening_watchlist,
        "reason_codes": ["NO_QUALIFIED_CANDIDATES"] if not opening_watchlist else ["OPENING_WATCHLIST_SELECTED"],
    }


def _safe_snapshots(
    source: MarketDataSource,
    symbols: tuple[str, ...],
    as_of: datetime,
    warnings: list[str],
) -> list[dict[str, Any]]:
    try:
        return source.snapshots(symbols, as_of)
    except Exception as exc:
        warnings.append(f"SNAPSHOT_ACQUISITION_FAILED:{_safe_error(exc)}")
        return []


def _health_map(report: Mapping[str, Any] | None) -> dict[str, str]:
    if not report:
        return {}
    values = report.get("results", report.get("symbols", [])) or []
    return {str(item.get("symbol", "")).upper(): str(item.get("state", "ACTIVE")) for item in values}


def _safe_error(exc: Exception) -> str:
    return (str(exc).replace("\n", " ").strip() or exc.__class__.__name__)[:160]
