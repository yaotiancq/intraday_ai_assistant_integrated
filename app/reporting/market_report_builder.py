from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping


DISCLAIMER = (
    "Research and decision-support only. No orders are submitted and profitability is not guaranteed. "
    "Delayed data must not be treated as real-time execution data."
)


def build_universe_validation_markdown(report: Mapping[str, Any]) -> str:
    rows = report.get("symbols", []) or report.get("results", []) or []
    lines = [
        f"# Universe Validation — {report.get('trade_date', 'unknown')}",
        "",
        f"- Status: **{report.get('status', 'UNKNOWN')}**",
        f"- Configured stocks: **{report.get('configured_symbol_count', len(rows))}**",
        f"- Unique symbols: **{report.get('unique_symbol_count', 0)}**",
        f"- Benchmarks excluded from stock count: **{report.get('benchmark_count', 0)}**",
        f"- Benchmark symbols including volatility proxy: **{report.get('configured_benchmark_count', report.get('benchmark_proxy_count', 0))}**",
        f"- Duplicate check: **{'PASS' if not report.get('duplicate_symbols') else 'FAIL'}**",
        f"- Unavailable symbols: **{_codes(report.get('unavailable_symbols'))}**",
        "",
        "## Sector distribution / 板块分布",
        "",
        "| Sector | 中文 | Count |",
        "|---|---|---:|",
    ]
    for item in report.get("sector_distribution", []):
        lines.append(f"| {_md(item.get('sector'))} | {_md(item.get('sector_zh'))} | {item.get('count', 0)} |")
    lines += [
        "",
        "## Symbol health",
        "",
        "| Symbol | Company | Sector / 板块 | State | Reason codes |",
        "|---|---|---|---|---|",
    ]
    for item in rows:
        sector = item.get("sector", "")
        sector_zh = item.get("sector_zh", "")
        lines.append(
            f"| {_md(item.get('symbol'))} | {_md(item.get('company_name'))} | "
            f"{_md(sector)} / {_md(sector_zh)} | {_md(item.get('state', 'UNKNOWN'))} | "
            f"{_codes(item.get('reason_codes'))} |"
        )
    lines += [
        "",
        "## Benchmark mappings",
        "",
        "| Stock | Comparison ETFs |",
        "|---|---|",
    ]
    for symbol, benchmarks in (report.get("benchmark_mappings", {}) or {}).items():
        lines.append(f"| {_md(symbol)} | {_codes(benchmarks)} |")
    warnings = report.get("data_quality_warnings", []) or report.get("warnings", []) or []
    lines += ["", "## Data-quality warnings", ""]
    lines += [f"- {_md(value)}" for value in warnings] or ["- None"]
    lines += ["", DISCLAIMER]
    return "\n".join(lines) + "\n"


def build_premarket_markdown(report: Mapping[str, Any]) -> str:
    regime = report.get("market_regime", {}) or {}
    rows = report.get("evaluated_stocks", []) or []
    watchlist = report.get("opening_watchlist", []) or []
    lines = [
        f"# Premarket Analysis — {report.get('trade_date', 'unknown')}",
        "",
        f"- Evidence cutoff: `{report.get('evidence_cutoff', report.get('scheduled_cutoff', 'unknown'))}`",
        f"- Market regime: **{regime.get('classification', 'UNKNOWN')}** (score {regime.get('score', 0)})",
        f"- Evaluated fixed-universe stocks: **{len(rows)}**",
        f"- Eligible candidates: **{len(report.get('eligible_candidates', []) or [])}**",
        f"- Opening watchlist: **{len(watchlist)}**",
        "",
        "## Leadership",
        "",
        f"- Strong sectors/industries: {_codes(regime.get('strong_sectors'))}",
        f"- Weak sectors/industries: {_codes(regime.get('weak_sectors'))}",
        f"- Regime reasons: {_codes(regime.get('reason_codes'))}",
        "",
        "## Opening watchlist",
        "",
        "| Rank | Symbol | Sector / 板块 | Type | Direction | Score | Reasons |",
        "|---:|---|---|---|---|---:|---|",
    ]
    for rank, item in enumerate(watchlist, 1):
        lines.append(
            f"| {rank} | {_md(item.get('symbol'))} | {_sector(item)} | {_md(item.get('candidate_type'))} | "
            f"{_md(item.get('direction'))} | {_num(item.get('premarket_score'))} | {_codes(item.get('reason_codes'))} |"
        )
    if not watchlist:
        lines.append("| — | — | — | — | — | — | NO_QUALIFIED_CANDIDATES |")

    lines += [
        "",
        "## All 30 configured stocks",
        "",
        "| Symbol | Company | Sector / 板块 | Health | Eligible | Type | Score | Disposition | Reasons |",
        "|---|---|---|---|---|---|---:|---|---|",
    ]
    for item in rows:
        lines.append(
            f"| {_md(item.get('symbol'))} | {_md(item.get('company_name'))} | {_sector(item)} | "
            f"{_md(item.get('health_state', 'UNKNOWN'))} | {'YES' if item.get('eligible') else 'NO'} | "
            f"{_md(item.get('candidate_type', 'NONE'))} | {_num(item.get('premarket_score'))} | "
            f"{_md(item.get('disposition', 'REJECTED'))} | {_codes(item.get('reason_codes'))} |"
        )

    lines += ["", "## Score breakdowns", ""]
    for item in rows:
        if not item.get("score_factors"):
            continue
        lines += [
            f"### {item.get('symbol')}",
            "",
            "| Factor | Raw | Normalized | Weight | Contribution | Reasons |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for name, factor in item["score_factors"].items():
            lines.append(
                f"| {_md(name)} | {_md(factor.get('raw_value'))} | {_num(factor.get('normalized_score'))} | "
                f"{_num(factor.get('weight'))} | {_num(factor.get('weighted_contribution'))} | "
                f"{_codes(factor.get('reason_codes'))} |"
            )
        lines.append("")

    lines += [
        "## Catalysts and technical levels",
        "",
        "| Symbol | Catalyst | Previous-day high / low | Premarket high / low |",
        "|---|---|---|---|",
    ]
    for item in rows:
        catalyst = item.get("catalyst", {}) or {}
        levels = item.get("technical_levels", {}) or {}
        lines.append(
            f"| {_md(item.get('symbol'))} | {_md(catalyst.get('catalyst_type', 'NO_CONFIRMED_CATALYST'))} | "
            f"{_num(levels.get('previous_day_high'))} / {_num(levels.get('previous_day_low'))} | "
            f"{_num(levels.get('premarket_high'))} / {_num(levels.get('premarket_low'))} |"
        )

    _append_warnings(lines, report)
    lines += ["", DISCLAIMER]
    return "\n".join(lines) + "\n"


def build_opening_markdown(report: Mapping[str, Any], *, final: bool = False) -> str:
    label = "Fifteen-Minute Final Confirmation" if final else "Five-Minute Opening Confirmation"
    rows = report.get("candidates", []) or []
    lines = [
        f"# {label} — {report.get('trade_date', 'unknown')}",
        "",
        f"- Evidence cutoff: `{report.get('evidence_cutoff', report.get('scheduled_cutoff', 'unknown'))}`",
        f"- Candidates analyzed: **{len(rows)}** (persisted premarket watchlist only)",
    ]
    if final:
        lines += [
            f"- Confirmed longs: **{_symbol_list(report.get('confirmed_longs'))}**",
            f"- Confirmed shorts: **{_symbol_list(report.get('confirmed_shorts'))}**",
            f"- Watch candidates: **{_symbol_list(report.get('watch_candidates'))}**",
            f"- No-trade candidates: **{_symbol_list(report.get('no_trade_candidates'))}**",
            f"- Rejected / insufficient data: **{_symbol_list(report.get('rejected_candidates'))}**",
        ]
    else:
        lines += [
            f"- Early confirmations: **{_symbols_for_decisions(rows, {'EARLY_CONFIRMED_LONG', 'EARLY_CONFIRMED_SHORT'})}**",
            f"- Watches: **{_symbols_for_decisions(rows, {'WATCH_LONG', 'WATCH_SHORT'})}**",
            f"- Early rejected / insufficient data: **{_symbols_for_decisions(rows, {'EARLY_REJECTED', 'INSUFFICIENT_DATA'})}**",
        ]
    lines += [
        "",
        "| Symbol | Decision | Direction | Setup | OR High | OR Low | VWAP | Opening RVOL | Premarket | Opening | Combined | Hard gates |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in rows:
        metrics = item.get("opening_metrics", {}) or {}
        gates = item.get("risk_gates", {}) or {}
        lines.append(
            f"| {_md(item.get('symbol'))} | {_md(item.get('decision'))} | {_md(item.get('direction'))} | "
            f"{_md(item.get('setup_type'))} | {_num(metrics.get('opening_range_high'))} | "
            f"{_num(metrics.get('opening_range_low'))} | "
            f"{_num(metrics.get('regular_session_vwap', metrics.get('vwap')))} | "
            f"{_num(metrics.get('opening_relative_volume'))} | {_num(item.get('premarket_score'))} | "
            f"{_num(item.get('opening_score'))} | {_num(item.get('combined_score'))} | "
            f"{'PASS' if gates.get('passed') else _codes(gates.get('failed_gates'))} |"
        )
    if not rows:
        lines.append("| — | NO_TRADE | — | NO_VALID_SETUP | — | — | — | — | — | — | — | NO_CANDIDATES |")

    lines += ["", "## Candidate detail", ""]
    for item in rows:
        lines += [
            f"### {item.get('symbol')} — {item.get('decision')}",
            "",
            f"- Setup: `{item.get('setup_type', 'NO_VALID_SETUP')}`",
            f"- Long score / short score: {_num(item.get('long_score'))} / {_num(item.get('short_score'))}",
            f"- Risk gates: {_codes((item.get('risk_gates') or {}).get('failed_gates')) if not (item.get('risk_gates') or {}).get('passed') else 'PASS'}",
            f"- Reasons: {_codes(item.get('reason_codes'))}",
        ]
        plan = item.get("entry_plan") or {}
        if plan:
            sizing = plan.get("position_sizing") or {}
            lines += [
                f"- Entry trigger: {_md(plan.get('entry_trigger'))}",
                f"- Invalidation: {_md(plan.get('invalidation_condition'))}",
                f"- Stop / targets: {_num(plan.get('initial_stop_reference'))} / "
                f"{_num(plan.get('first_target_reference'))} / {_num(plan.get('second_target_reference'))}",
                f"- Reward/risk: {_num(plan.get('expected_reward_risk_ratio'))}",
                f"- Analysis-only share quantity: {_md(sizing.get('analysis_share_quantity'))}",
                f"- Theoretical risk quantity / notional-limit quantity: "
                f"{_md(sizing.get('maximum_theoretical_share_quantity'))} / "
                f"{_md(sizing.get('maximum_quantity_after_notional_limit'))}",
                f"- Estimated total risk / slippage: {_num(sizing.get('estimated_total_risk'))} / "
                f"{_num(sizing.get('estimated_total_slippage'))}",
            ]
        lines.append("")
    _append_warnings(lines, report)
    lines += ["", DISCLAIMER]
    return "\n".join(lines) + "\n"


def build_final_report_payload(opening_report: Mapping[str, Any]) -> dict[str, Any]:
    candidates = list(opening_report.get("candidates", []) or [])
    payload = dict(opening_report)
    payload["confirmed_longs"] = [item for item in candidates if item.get("decision") == "CONFIRMED_LONG"]
    payload["confirmed_shorts"] = [item for item in candidates if item.get("decision") == "CONFIRMED_SHORT"]
    payload["watch_candidates"] = [item for item in candidates if item.get("decision") in {"WATCH_LONG", "WATCH_SHORT"}]
    payload["no_trade_candidates"] = [item for item in candidates if item.get("decision") == "NO_TRADE"]
    payload["rejected_candidates"] = [
        item for item in candidates if item.get("decision") in {"REJECTED", "INSUFFICIENT_DATA"}
    ]
    payload["final_decision"] = (
        "OPPORTUNITIES_PRESENT"
        if payload["confirmed_longs"] or payload["confirmed_shorts"]
        else "NO_TRADE"
    )
    return payload


def _append_warnings(lines: list[str], report: Mapping[str, Any]) -> None:
    lines += ["", "## Data limitations and warnings", ""]
    warnings = report.get("data_quality_warnings", []) or report.get("warnings", []) or []
    lines += [f"- {_md(value)}" for value in warnings] or ["- None"]


def _sector(item: Mapping[str, Any]) -> str:
    return f"{_md(item.get('sector'))} / {_md(item.get('sector_zh'))}"


def _symbol_list(values: Any) -> str:
    rows = values if isinstance(values, list) else []
    symbols = [str(item.get("symbol")) for item in rows if isinstance(item, Mapping) and item.get("symbol")]
    return ", ".join(symbols) if symbols else "None"


def _symbols_for_decisions(rows: list[Any], decisions: set[str]) -> str:
    symbols = [
        str(item.get("symbol"))
        for item in rows
        if isinstance(item, Mapping) and item.get("decision") in decisions and item.get("symbol")
    ]
    return ", ".join(symbols) if symbols else "None"


def _codes(values: Any) -> str:
    if not values:
        return "—"
    if isinstance(values, str):
        return _md(values)
    return ", ".join(_md(value) for value in values)


def _num(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _md(value: Any) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")
