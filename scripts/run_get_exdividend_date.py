from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.delivery.discord import DiscordWebhookClient
from app.utils.file_io import write_json, write_text


BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_TIMEZONE = "America/New_York"


def fmp_get(endpoint: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Any:
    if not api_key:
        raise RuntimeError("FMP_API_KEY is empty")

    query = dict(params or {})
    query["apikey"] = api_key

    url = f"{BASE_URL}/{endpoint.lstrip('/')}"
    resp = requests.get(url, params=query, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"FMP error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    if isinstance(data, dict) and "Error Message" in data:
        raise RuntimeError(str(data["Error Message"]))
    return data


def next_us_trading_day(now: datetime | None = None, tz_name: str = DEFAULT_TIMEZONE) -> str:
    local_now = now or datetime.now(ZoneInfo(tz_name))
    today = local_now.date()
    end = today + timedelta(days=14)

    try:
        import pandas_market_calendars as mcal

        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=today.isoformat(), end_date=end.isoformat())
        for d in schedule.index.date:
            if d > today:
                return d.isoformat()
    except Exception:
        # Resilient fallback for environments missing the exchange calendar.
        pass

    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def pick_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower_map = {str(c).lower(): c for c in df.columns}
    for name in candidates:
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


def safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "" or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def normalize_ratio(value: Any) -> Optional[float]:
    ratio = safe_float(value)
    if ratio is None:
        return None
    if ratio > 1:
        ratio = ratio / 100.0
    return ratio


def get_next_ex_dividend_candidates(target_date: str, api_key: str) -> pd.DataFrame:
    data = fmp_get(
        "dividends-calendar",
        api_key=api_key,
        params={"from": target_date, "to": target_date},
    )

    df = pd.DataFrame(data)
    if df.empty:
        return df

    symbol_col = pick_col(df, ["symbol", "ticker"])
    ex_date_col = pick_col(df, ["date", "exDate", "exDividendDate", "ex_dividend_date"])
    dividend_col = pick_col(df, ["dividend", "adjDividend", "amount"])
    payment_col = pick_col(df, ["paymentDate", "payDate", "dividendPayableDate"])
    record_col = pick_col(df, ["recordDate"])
    declaration_col = pick_col(df, ["declarationDate"])

    if not symbol_col or not ex_date_col:
        raise RuntimeError(f"Cannot identify symbol/ex-date columns. Columns: {list(df.columns)}")

    out = pd.DataFrame()
    out["symbol"] = df[symbol_col].astype(str).str.upper().str.strip()
    out["ex_date"] = df[ex_date_col]
    out["dividend"] = df[dividend_col] if dividend_col else None
    out["payment_date"] = df[payment_col] if payment_col else None
    out["record_date"] = df[record_col] if record_col else None
    out["declaration_date"] = df[declaration_col] if declaration_col else None
    out = out[out["symbol"].str.len() > 0]
    out = out.drop_duplicates(subset=["symbol", "ex_date"])
    return out


def get_profile(symbol: str, api_key: str) -> Dict[str, Any]:
    data = fmp_get("profile", api_key=api_key, params={"symbol": symbol})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def get_ratios_ttm(symbol: str, api_key: str) -> Dict[str, Any]:
    data = fmp_get("ratios-ttm", api_key=api_key, params={"symbol": symbol})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def get_key_metrics_ttm(symbol: str, api_key: str) -> Dict[str, Any]:
    data = fmp_get("key-metrics-ttm", api_key=api_key, params={"symbol": symbol})
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def get_dividend_history(symbol: str, api_key: str) -> pd.DataFrame:
    data = fmp_get("dividends", api_key=api_key, params={"symbol": symbol})
    df = pd.DataFrame(data)
    if df.empty:
        return df

    date_col = pick_col(df, ["date", "exDate", "exDividendDate"])
    div_col = pick_col(df, ["adjDividend", "dividend", "amount"])
    if not date_col or not div_col:
        return pd.DataFrame()

    out = df[[date_col, div_col]].copy()
    out.columns = ["date", "dividend"]
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["dividend"] = pd.to_numeric(out["dividend"], errors="coerce")
    out = out.dropna(subset=["date", "dividend"])
    out = out[out["dividend"] > 0]
    return out.sort_values("date")


def analyze_dividend_history(df: pd.DataFrame, years: int = 5) -> Dict[str, Any]:
    if df.empty:
        return {
            "years_paid": 0,
            "annual_dividend_cagr": None,
            "dividend_stability_score": 0,
        }

    cutoff = pd.Timestamp.today() - pd.DateOffset(years=years)
    recent = df[df["date"] >= cutoff].copy()
    if recent.empty:
        return {
            "years_paid": 0,
            "annual_dividend_cagr": None,
            "dividend_stability_score": 0,
        }

    recent["year"] = recent["date"].dt.year
    annual = recent.groupby("year")["dividend"].sum().sort_index()
    years_paid = int((annual > 0).sum())

    cagr = None
    if len(annual) >= 3 and annual.iloc[0] > 0:
        cagr = (annual.iloc[-1] / annual.iloc[0]) ** (1 / (len(annual) - 1)) - 1

    no_major_cut = True
    if len(annual) >= 2:
        yoy = annual.pct_change().dropna()
        no_major_cut = bool((yoy > -0.2).all())

    stability_score = 0
    if years_paid >= 5:
        stability_score += 2
    elif years_paid >= 3:
        stability_score += 1
    if no_major_cut:
        stability_score += 1
    if cagr is not None and cagr > 0:
        stability_score += 1

    return {
        "years_paid": years_paid,
        "annual_dividend_cagr": cagr,
        "dividend_stability_score": stability_score,
    }


def quality_tier(score: int) -> str:
    if score >= 8:
        return "A"
    if score >= 6:
        return "B"
    if score >= 4:
        return "C"
    return "D"


def score_candidate(row: pd.Series, api_key: str, delay_seconds: float = 0.2) -> Dict[str, Any]:
    symbol = str(row["symbol"]).upper()

    profile = get_profile(symbol, api_key)
    time.sleep(delay_seconds)
    ratios = get_ratios_ttm(symbol, api_key)
    time.sleep(delay_seconds)
    metrics = get_key_metrics_ttm(symbol, api_key)
    time.sleep(delay_seconds)
    hist = get_dividend_history(symbol, api_key)
    time.sleep(delay_seconds)

    hist_stats = analyze_dividend_history(hist, years=5)

    sector = profile.get("sector")
    industry = profile.get("industry")
    market_cap = safe_float(profile.get("marketCap"))
    price = safe_float(profile.get("price"))
    last_div = safe_float(profile.get("lastDiv"))

    dividend_yield = (
        normalize_ratio(profile.get("dividendYield"))
        or normalize_ratio(metrics.get("dividendYieldTTM"))
        or normalize_ratio(ratios.get("dividendYieldTTM"))
    )
    if dividend_yield is None and last_div is not None and price and price > 0:
        dividend_yield = last_div / price

    payout_ratio = (
        normalize_ratio(ratios.get("payoutRatioTTM"))
        or normalize_ratio(ratios.get("dividendPayoutRatioTTM"))
        or normalize_ratio(metrics.get("payoutRatioTTM"))
    )

    score = 0
    warnings: List[str] = []

    if dividend_yield is not None:
        if 0.02 <= dividend_yield <= 0.08:
            score += 2
        elif 0.08 < dividend_yield <= 0.12:
            score += 1
            warnings.append("High dividend yield; check sustainability")
        elif dividend_yield > 0.12:
            warnings.append("Very high dividend yield; possible yield trap")

    if payout_ratio is not None:
        if 0 < payout_ratio <= 0.75:
            score += 2
        elif 0.75 < payout_ratio <= 1.0:
            score += 1
            warnings.append("High payout ratio")
        elif payout_ratio > 1.0:
            warnings.append("Payout ratio above 100%")

    score += int(hist_stats["dividend_stability_score"])

    if market_cap is not None:
        if market_cap >= 10_000_000_000:
            score += 2
        elif market_cap >= 2_000_000_000:
            score += 1
        else:
            warnings.append("Small-cap dividend stock; higher risk")

    return {
        "symbol": symbol,
        "ex_date": row.get("ex_date"),
        "dividend": safe_float(row.get("dividend")),
        "payment_date": row.get("payment_date"),
        "record_date": row.get("record_date"),
        "declaration_date": row.get("declaration_date"),
        "sector": sector,
        "industry": industry,
        "market_cap": market_cap,
        "dividend_yield": dividend_yield,
        "payout_ratio": payout_ratio,
        "years_paid_5y": hist_stats["years_paid"],
        "dividend_cagr_5y": hist_stats["annual_dividend_cagr"],
        "score": int(score),
        "tier": quality_tier(int(score)),
        "warnings": "; ".join(warnings),
    }


def format_money(value: Any) -> str:
    num = safe_float(value)
    if num is None:
        return "N/A"
    return f"${num:.4g}"


def format_market_cap(value: Any) -> str:
    num = safe_float(value)
    if num is None:
        return "N/A"
    if num >= 1_000_000_000_000:
        return f"${num / 1_000_000_000_000:.2f}T"
    if num >= 1_000_000_000:
        return f"${num / 1_000_000_000:.2f}B"
    if num >= 1_000_000:
        return f"${num / 1_000_000:.2f}M"
    return f"${num:,.0f}"


def format_pct(value: Any) -> str:
    num = safe_float(value)
    if num is None:
        return "N/A"
    return f"{num * 100:.2f}%"


def build_discord_report(target_date: str, result_df: pd.DataFrame, top_n: int = 20) -> str:
    if result_df.empty:
        return "\n".join([
            "Ex-dividend watchlist",
            f"Target ex-dividend date: {target_date}",
            "No scored candidates found.",
            "",
            "Not investment advice.",
        ])

    rows = result_df.head(top_n).to_dict("records")
    lines = [
        "Ex-dividend watchlist",
        f"Target ex-dividend date: {target_date}",
        f"Scored candidates: {len(result_df)}",
        "Scoring: yield, payout ratio, dividend stability, market cap.",
        "",
    ]

    for idx, row in enumerate(rows, start=1):
        warnings = str(row.get("warnings") or "").strip()
        lines.extend([
            (
                f"{idx}. {row.get('symbol')} | Tier {row.get('tier')} | "
                f"Score {row.get('score')} | Div {format_money(row.get('dividend'))}"
            ),
            (
                f"   Yield {format_pct(row.get('dividend_yield'))} | "
                f"Payout {format_pct(row.get('payout_ratio'))} | "
                f"Market cap {format_market_cap(row.get('market_cap'))}"
            ),
            (
                f"   Sector {row.get('sector') or 'N/A'} | "
                f"5y paid {row.get('years_paid_5y')} yrs | "
                f"Pay date {row.get('payment_date') or 'N/A'}"
            ),
        ])
        if warnings:
            lines.append(f"   Warnings: {warnings}")
        lines.append("")

    if len(result_df) > top_n:
        lines.append(f"Only top {top_n} shown in Discord; full output saved locally.")
    lines.append("Not investment advice.")
    return "\n".join(lines)


def sort_results(result_df: pd.DataFrame) -> pd.DataFrame:
    return result_df.sort_values(
        by=["score", "market_cap"],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Find and score next-trading-day ex-dividend stocks.")
    parser.add_argument("--env-file", default=None, help="Path to .env file")
    parser.add_argument("--target-date", default=None, help="Override ex-dividend date, YYYY-MM-DD")
    parser.add_argument("--top", type=int, default=20, help="Number of rows to include in Discord output")
    parser.add_argument("--max-candidates", type=int, default=0, help="Limit candidates scored; 0 means no limit")
    parser.add_argument("--delay-seconds", type=float, default=0.2, help="Delay between per-symbol FMP calls")
    parser.add_argument("--dry-run", action="store_true", help="Do not send Discord message")
    parser.add_argument("--skip-discord", action="store_true", help="Do not send Discord message")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    else:
        load_dotenv(override=False)

    api_key = os.getenv("FMP_API_KEY", "").strip()
    webhook_url = os.getenv("DISCORD_EXDIVIDEND_WEBHOOK_URL", "").strip()
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    target_date = args.target_date or next_us_trading_day()

    print(f"Scanning ex-dividend candidates for next US trading day: {target_date}")
    candidates = get_next_ex_dividend_candidates(target_date, api_key)
    if args.max_candidates and len(candidates) > args.max_candidates:
        candidates = candidates.head(args.max_candidates)

    if candidates.empty:
        report = build_discord_report(target_date, pd.DataFrame(), top_n=args.top)
        print(report)
        write_text(data_dir / f"ex_dividend_report_{target_date}.md", report)
        if webhook_url and not args.dry_run and not args.skip_discord:
            DiscordWebhookClient(webhook_url).send_message(report)
        return 0

    results: List[Dict[str, Any]] = []
    failures: List[str] = []
    for _, row in candidates.iterrows():
        symbol = str(row["symbol"]).upper()
        try:
            print(f"Analyzing {symbol}...")
            results.append(score_candidate(row, api_key=api_key, delay_seconds=args.delay_seconds))
        except Exception as exc:
            warning = f"{symbol}: {exc}"
            failures.append(warning)
            print(f"[WARN] Failed to analyze {warning}")

    result_df = pd.DataFrame(results)
    if not result_df.empty:
        result_df = sort_results(result_df)

    report = build_discord_report(target_date, result_df, top_n=args.top)
    if failures:
        report += "\n\nAnalysis warnings:\n" + "\n".join(f"- {w}" for w in failures[:20])

    data_dir.mkdir(parents=True, exist_ok=True)
    csv_path = data_dir / f"ex_dividend_quality_{target_date}.csv"
    json_path = data_dir / f"ex_dividend_quality_{target_date}.json"
    report_path = data_dir / f"ex_dividend_report_{target_date}.md"

    result_df.to_csv(csv_path, index=False)
    write_json(json_path, result_df.to_dict("records"))
    write_text(report_path, report)

    print("\nTop candidates:")
    if result_df.empty:
        print("No scored result.")
    else:
        print(result_df.head(args.top).to_string(index=False))
    print(f"\nSaved to: {csv_path}")
    print(f"Saved report to: {report_path}")

    if webhook_url and not args.dry_run and not args.skip_discord:
        DiscordWebhookClient(webhook_url).send_message(report)
        print("Sent Discord ex-dividend report.")
    elif not webhook_url:
        print("DISCORD_EXDIVIDEND_WEBHOOK_URL is empty; skipped Discord send.")
    else:
        print("Discord send skipped by dry-run/skip-discord.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
