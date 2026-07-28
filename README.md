# Deterministic Fixed-30 Intraday Market Analysis

This repository runs one deterministic strategy engine over one explicitly configured universe of 30 US-listed large-cap stocks. It validates the universe before the open, produces a point-in-time premarket shortlist, confirms or rejects setups after the first 5 and 15 regular-session minutes, and saves an auditable report for every stage. It does not offer competing analysis modes. The stock list and every threshold are initial research defaults, not universal truths.

> **Research and decision support only.** The fixed-30 workflow does not place, route, modify, or cancel orders. It does not promise signals, trades, fills, or profits. Market data may be delayed, stale, incomplete, or revised; do not treat its output as executable real-time advice.

The core workflow has deliberately narrow boundaries:

- No full-market, gainers, movers, social-media, or arbitrary-symbol scan.
- No symbol may enter the workflow unless it is one of the configured 30 stocks. News collection is also intersected with that allowlist.
- No LLM, OpenAI API, generative model, or free-form AI judgment is used in ranking, setup classification, reporting, or risk gates.
- No broker order API is called. Position sizing and entry plans are analytical examples only.
- Comparison ETFs and `VIX` provide context; they never become stock trade candidates.
- A valid outcome is `NO_TRADE`. The system never fills a quota merely because a stage ran.

## Workflow at a glance

```text
fixed configuration (30 stocks + comparison benchmarks)
  -> 08:20 ET universe validation
  -> 08:45 ET premarket filters and deterministic score
       30 configured -> typically 6-15 eligible (current cap: 12)
  -> 09:35 ET first-five-minute confirmation
       -> 4-8 opening watchlist names (current cap: 8)
  -> 09:45 ET first-fifteen-minute final confirmation
       -> 0-3 actionable research setups, or NO_TRADE
  -> atomic dated artifacts + optional notification
```

Every calculation uses an explicit trade date, `as_of` timestamp, and stage cutoff. An opening stage loads the already persisted premarket snapshot instead of recomputing it with later information.

## Fixed stock universe

Membership lives in [`config/market_strategy.json`](config/market_strategy.json). The configured universe must contain exactly these 30 unique stocks; runtime discovery cannot enlarge it.

| Sector | Count | Configured stocks |
|---|---:|---|
| Information Technology / 信息技术 | 6 | `AAPL` Apple, `MSFT` Microsoft, `NVDA` NVIDIA, `AMD` Advanced Micro Devices, `AVGO` Broadcom, `TSM` Taiwan Semiconductor Manufacturing |
| Communication Services / 通信服务 | 3 | `GOOGL` Alphabet, `META` Meta Platforms, `NFLX` Netflix |
| Consumer Discretionary / 非必需消费 | 4 | `AMZN` Amazon, `TSLA` Tesla, `HD` Home Depot, `MCD` McDonald's |
| Financials / 金融 | 4 | `JPM` JPMorgan Chase, `BAC` Bank of America, `C` Citigroup, `GS` Goldman Sachs |
| Health Care / 医疗保健 | 3 | `LLY` Eli Lilly, `UNH` UnitedHealth Group, `JNJ` Johnson & Johnson |
| Industrials / 工业 | 3 | `CAT` Caterpillar, `GE` GE Aerospace, `BA` Boeing |
| Energy / 能源 | 2 | `XOM` Exxon Mobil, `CVX` Chevron |
| Consumer Staples / 必需消费 | 2 | `WMT` Walmart, `COST` Costco |
| Materials / 原材料 | 1 | `LIN` Linde |
| Utilities / 公用事业 | 1 | `NEE` NextEra Energy |
| Real Estate / 房地产 | 1 | `PLD` Prologis |
| **Total** | **30** | **Fixed; no automatic additions or substitutions** |

### Why these names

The list is a deliberately compact research panel. Its qualitative selection rationale combines large or established market capitalization, historically high trading activity and dollar-volume liquidity, relatively continuous quoted markets, institutional ownership and market relevance, frequent sensitivity to company/sector/macro catalysts, coverage across all 11 GICS-style sectors, and practical suitability for opening-session analysis. A fixed list makes daily outputs comparable, bounds data requests, and prevents a hidden discovery step from changing the sample.

Membership means only that a stock is monitored. It conveys no daily endorsement: every member must independently pass that day's data, liquidity, spread, volume, setup, and risk gates.

This is not a claim that these are the “best” stocks or that membership predicts returns. The list has important biases and limitations:

- It is manually selected and dominated by large and mega-cap companies. Small caps, recent IPOs, less-liquid names, and many industries are underrepresented or absent.
- It has survivorship, availability, US-listing, liquidity, and attention biases. `TSM` also introduces ADR and non-US issuer considerations.
- Sector counts are intentionally uneven, so results must not be interpreted as a sector-neutral market sample.
- A static list can become stale after delistings, symbol changes, corporate actions, structural liquidity deterioration, or business changes.
- Backtests cover this fixed panel, not the investable US equity market. They cannot substantiate claims about a full-market strategy.

Daily validation may mark a symbol unavailable or data-incomplete for that run; it does not replace the symbol. The separate health review emits only `KEEP`, `REVIEW`, or `POSSIBLE_REPLACEMENT` recommendations. A human must intentionally edit and version the configuration before membership changes.

## Comparison benchmarks

Benchmarks are fetched only to describe market regime, sector/industry direction, and relative strength. They are not counted among the 30 stocks and stock-style setup decisions are disabled for them.

| Group | Symbol | English meaning | 中文说明 |
|---|---|---|---|
| Broad market | `SPY` | S&P 500 broad market | 标普 500 大盘 |
| Broad market | `QQQ` | Nasdaq-100 growth | 纳斯达克 100 成长股 |
| Broad market | `IWM` | Small-Cap Equities | 小盘股 |
| Broad market | `DIA` | Dow Jones Large Caps | 道琼斯大盘股 |
| Sector | `XLK` | Technology | 科技 |
| Sector | `XLC` | Communication Services | 通信服务 |
| Sector | `XLY` | Consumer Discretionary | 非必需消费 |
| Sector | `XLP` | Consumer Staples | 必需消费 |
| Sector | `XLE` | Energy | 能源 |
| Sector | `XLF` | Financials | 金融 |
| Sector | `XLV` | Health Care | 医疗保健 |
| Sector | `XLI` | Industrials | 工业 |
| Sector | `XLB` | Materials | 原材料 |
| Sector | `XLU` | Utilities | 公用事业 |
| Sector | `XLRE` | Real Estate | 房地产 |
| Industry | `SMH` | Semiconductor industry | 半导体行业 |
| Industry | `SOXX` | Semiconductor industry | 半导体行业 |
| Industry | `IGV` | Software industry | 软件行业 |
| Industry | `ITA` | Aerospace and defense | 航空航天与国防 |
| Volatility proxy | `VIX` | Implied-volatility context | 隐含波动率环境 |

Each stock's stock-to-ETF mapping is explicit in configuration and is validated before use.

## Trading-day schedule

`America/New_York` is the authoritative timezone. The scheduler uses timezone-aware datetimes and a trading calendar; it does not hardcode UTC offsets. The Pacific examples below remain three hours earlier under the normal US daylight-saving transition because both zones change clocks, but operators should still rely on the named zones rather than fixed UTC arithmetic.

| Stage | Eastern Time | Pacific Time | Point-in-time cutoff and purpose |
|---|---:|---:|---|
| Universe validation | 08:20 ET | 05:20 PT | Validate configuration, quotes, history, symbol status, and data health |
| Premarket | 08:45 ET | 05:45 PT | Use only evidence available through 08:45 ET |
| Opening 5m | 09:35 ET | 06:35 PT | Provisional confirmation using the 09:30-09:35 window |
| Opening 15m | 09:45 ET | 06:45 PT | Final confirmation using the 09:30-09:45 window |

The calendar behavior is explicit:

- Saturdays, Sundays, NYSE holidays, and configured exceptional closures are persisted as `status: SKIPPED` with a stable reason such as `NON_TRADING_DAY`, instead of being treated as empty market sessions.
- Exchange-calendar early closes and configured early closes are recognized. These opening jobs still run at their normal morning cutoffs because they occur before the close; an early close must not shift the opening window.
- A late-but-allowed run retains its original scheduled cutoff. It must not consume bars or news that arrived after that cutoff.
- The default maximum start lateness is 10 minutes. A job outside that window is recorded as missed/skipped rather than silently relabeled on time.
- Durable per-stage locks and the dated manifest prevent two scheduler processes from completing the same logical run concurrently.

## Stage methodology

### 1. Universe validation

The 08:20 ET gate verifies fixed mode, exactly 30 unique stocks, non-overlap with benchmarks, sector and comparison-ETF metadata, symbol format, recent quote validity and freshness, daily-history completeness, and corporate-action consistency. Provider indications become one of `ACTIVE`, `TEMPORARILY_UNAVAILABLE`, `DATA_INCOMPLETE`, `SYMBOL_CHANGED`, `DELISTED`, or `CONFIGURATION_INVALID`, with stable reason codes.

An affected name is excluded or flagged for that date. The validator never discovers a replacement and never rewrites the configured list.

### 2. Premarket analysis

The 08:45 ET stage processes all 30 configured stocks and their mapped benchmarks. It computes price/liquidity checks, gap percentage, premarket dollar volume and relative volume, spread, daily and intraday technical context, stock-versus-market/sector relative strength, market regime, sector confirmation, volatility, and deterministic catalyst evidence. Regime outputs use stable labels: `STRONG_RISK_ON`, `RISK_ON`, `MIXED`, `RISK_OFF`, `STRONG_RISK_OFF`, `HIGH_VOLATILITY`, `LOW_LIQUIDITY`, or `UNKNOWN`.

News handling is rules based. Sources must pass the configured domain allowlist, symbols are intersected with the fixed universe, timestamps are cutoff-aware, and keyword/phrase rules classify catalyst types. There is no generative summarization or semantic model. Missing or untrusted news cannot add a stock to the universe.

Two transparent candidate paths are supported:

- `EVENT_DRIVEN`: a credible catalyst, sufficiently material gap, or abnormal premarket activity.
- `RELATIVE_STRENGTH`: unusual liquidity/volume and stock strength or weakness relative to the broad market and mapped ETFs, even without a news catalyst.

Default premarket eligibility includes a price of at least `$5`, average daily dollar volume of at least `$50M`, premarket dollar volume of at least `$750K`, spread no wider than `35 bps`, premarket RVOL of at least `0.8`, and an absolute gap no greater than `15%`. A `0.30%` gap is one configured event threshold, but it is not a mandatory gate for relative-strength candidates. Event-driven defaults include a `2%` gap or `1.5` premarket RVOL, while relative-strength qualification defaults to at least `0.20%` excess-return magnitude. All thresholds are configuration, not universal market truths.

The current shortlist cap is 12, with at most three names from one sector and a default minimum score of 55. The expected operating range is roughly 6-15 eligible names, but zero is allowed and the configured cap governs.

### 3. First-five-minute confirmation

At 09:35 ET, the pipeline loads the persisted premarket candidates and uses only bars through 09:35. It evaluates opening-range structure, VWAP relationship, volume expansion, spread, gap retention, price action, stock/sector/market alignment, and early failed-breakout or failed-breakdown evidence.

This is provisional evidence. The result narrows the list to at most eight names and records `EARLY_CONFIRMED_LONG`, `EARLY_CONFIRMED_SHORT`, `WATCH_LONG`, `WATCH_SHORT`, `EARLY_REJECTED`, or `INSUFFICIENT_DATA` with reason codes; it does not place an order.

### 4. First-fifteen-minute final confirmation

At 09:45 ET, the final stage repeats confirmation over the complete first-15-minute window and checks the persisted premarket and optional five-minute snapshots. Supported setup labels are deterministic:

- `OPENING_DRIVE_LONG` and `OPENING_DRIVE_SHORT`.
- `OPENING_RANGE_BREAKOUT` and `OPENING_RANGE_BREAKDOWN`.
- `GAP_AND_GO_LONG` and `GAP_AND_GO_SHORT`.
- `PREMARKET_HIGH_BREAKOUT` and `PREMARKET_LOW_BREAKDOWN`.
- `VWAP_RECLAIM` and `VWAP_REJECTION`.
- `FIRST_PULLBACK_LONG` and `FIRST_PULLBACK_SHORT`.
- Failure/no-setup labels `FAILED_BREAKOUT`, `FAILED_BREAKDOWN`, `FAILED_GAP_UP`, `FAILED_GAP_DOWN`, and `NO_VALID_SETUP`.

Final decisions are `CONFIRMED_LONG`, `CONFIRMED_SHORT`, `WATCH_LONG`, `WATCH_SHORT`, `NO_TRADE`, `REJECTED`, or `INSUFFICIENT_DATA`. The report records why a setup confirmed, remained watch-only, or failed. It may return zero candidates and `NO_TRADE`; no fallback symbol is injected.

## Scoring and hard gates

Scores rank only names that satisfy required data and risk conditions. A high score cannot override a failed hard gate.

### Premarket score (100 points)

| Component | Weight |
|---|---:|
| Liquidity | 10 |
| Premarket relative volume | 13 |
| Gap quality | 10 |
| Catalyst quality | 9 |
| Technical structure | 14 |
| Relative strength | 16 |
| Sector confirmation | 11 |
| Market regime | 8 |
| Volatility suitability | 5 |
| Spread quality | 4 |
| **Total** | **100** |

### Opening confirmation score (100 points)

| Component | Weight |
|---|---:|
| Opening-range structure | 17 |
| VWAP behavior | 14 |
| Volume confirmation | 14 |
| Relative strength | 15 |
| Market confirmation | 8 |
| Sector confirmation | 8 |
| Gap retention | 7 |
| Price action | 10 |
| Liquidity/spread | 4 |
| Extension control | 3 |
| **Total before penalties** | **100** |

Default penalties include failed breakout/breakdown (`25`), failed gap (`20`), weak volume (`10`), market conflict (`10`), sector conflict (`7`), excessive extension (`12`), poor close (`7`), and wide spread (`15`). The combined final rank uses 40% of the persisted premarket score and 60% of the opening score.

Hard gates cover, at minimum:

- Required snapshots/bars, timestamps, mapped benchmarks, and complete stage windows; default maximum live-data age is 90 seconds.
- Minimum price and average daily dollar volume, stage-specific premarket dollar volume/RVOL, and maximum spread.
- Default opening RVOL of at least `1.0`.
- Opening-range size between `0.05` and `0.80` ATR, gap no greater than `2.5` ATR, entry extension no greater than `0.35` ATR, and breakout extension no greater than `0.25` ATR.
- Minimum planned reward-to-risk of `1.5` after the configured `5 bps` slippage assumption.
- Failed-breakout, failed-breakdown, failed-gap, chase/extension, directional-conflict, and market/sector-conflict rules. The default directional-conflict score difference is 8 points.

When a setup survives, the report can show entry zone, invalidation, stop reference, targets, reward/risk, and example sizing using the configured `$150` analytical risk budget and `$10,000` maximum notional. Those values are scenario inputs, not an instruction or broker action.

## Data modes and delayed-data warning

The workflow supports three bounded data modes:

- Futu/Moomoo OpenD for live research data, subject to the user's subscriptions, entitlements, session state, and provider timestamps.
- Deterministic demo/mock data for installation checks and tests. `.env.example` defaults `DEMO_MODE=true` so a fresh checkout cannot be mistaken for live operation.
- Replay fixtures for repeatable stage and regression tests.

For Futu, start and log in to OpenD, verify US market-data permissions, set `DEMO_MODE=false`, and configure `FUTU_HOST`, `FUTU_PORT`, and extended-hours access as required. Docker uses host networking so the container can reach a host OpenD at `127.0.0.1:11111`.

> **Delayed-data warning:** a successful connection does not prove that quotes are real time. Inspect provider timestamps and entitlements. The pipeline can reject stale data, but network delay, exchange redistribution delay, missing premarket prints, and later corrections remain possible. Never use generated entry levels as executable prices.

## Installation and configuration

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

The main settings are:

```env
MARKET_CONFIG_PATH=config/market_strategy.json
MARKET_OUTPUT_DIR=output/runs
DATA_DIR=data

FUTU_HOST=127.0.0.1
FUTU_PORT=11111
FUTU_EXTENDED_TIME=true

# Safe installation/test default
DEMO_MODE=true
```

No `OPENAI_API_KEY` is required or used by the fixed-30 pipeline. Optional RSS and Discord settings affect bounded data collection or post-persistence notification; they do not change universe membership and notification failure does not erase an analysis artifact.

Validate configuration changes before scheduling them. Thresholds, weights, universe membership, mappings, and `strategy_version` must be reviewed together. A membership or methodology change should receive a new strategy version so historical artifacts are not silently mixed.

## Command-line operation

Use `--help` on each script for the installed command's complete options.

Validate the fixed configuration and current data health:

```bash
python scripts/validate_universe.py
```

Generate a non-mutating universe-health recommendation report:

```bash
python scripts/review_fixed_universe.py
```

Run the three analysis stages manually for one trade date:

```bash
python scripts/run_market_analysis.py --stage premarket --date 2026-07-16
python scripts/run_market_analysis.py --stage opening-5m --date 2026-07-16
python scripts/run_market_analysis.py --stage opening-15m --date 2026-07-16
```

Hyphenated stage names are normalized to the persisted names `opening_5m` and `opening_15m`. Run stages in time order when using the normal repository because later stages intentionally consume earlier persisted snapshots.

Run the calendar-aware persistent scheduler:

```bash
python scripts/run_market_scheduler.py
```

Replay a deterministic fixture:

```bash
python scripts/run_market_analysis.py \
  --stage opening-15m \
  --data-source replay \
  --input-file tests/fixtures/valid_opening_breakout.json
```

When `--date` is omitted in replay mode, the fixture's required `trade_date` is used. Supplying a conflicting date is rejected so one fixture cannot silently be relabeled as another session.

For a full historical replay, use one clean output date/directory and execute `premarket`, `opening-5m`, then `opening-15m` against the same point-in-time fixture set. Do not mix live and replay artifacts for one date. Use any force/rerun option only intentionally: forced results are audit-visible and should never be used to hide changed configuration or post-cutoff evidence.

## Persistence, idempotency, and cutoffs

The default dated layout is:

```text
output/runs/
  .locks/
    YYYY-MM-DD/
      <logical-run-key>.lock
  YYYY-MM-DD/
    config_snapshot.json
    run_manifest.json
    universe_validation.json
    premarket.json
    premarket.md
    opening_5m.json
    opening_5m.md
    opening_15m.json
    final_report.json
    final_report.md
```

JSON files are the machine-readable source of truth; Markdown files are operator views. Writes are atomic. `config_snapshot.json` freezes the interpretation used for that dated run, and `run_manifest.json` records stage status, files, attempts, persistence time, and force state.

The logical idempotency key is:

```text
trade_date : stage : strategy_version
```

A terminal result for the same key is not recomputed by default. Stage locks prevent overlap, while manifest checks protect across process restarts. Later stages load prior dated JSON snapshots and validate their date, version, stage, status, and cutoff. This prevents a 09:45 run from silently rebuilding its 08:45 evidence with future information.

Each artifact distinguishes the scheduled cutoff from the actual start time and records whether the process began late. Provider requests and feature windows are anchored to the scheduled cutoff. Missing, stale, malformed, post-cutoff, or cross-version input fails closed with explicit reasons rather than being silently accepted.

## Docker deployment

The default Compose service is the fixed-30 scheduler:

```bash
docker compose build
docker compose up -d market-scheduler
docker compose logs -f market-scheduler
```

Stop it with:

```bash
docker compose stop market-scheduler
```

The service runs `python scripts/run_market_scheduler.py`, sets `TZ=America/New_York`, mounts `./config` read-only, persists `./output`, and uses host networking for OpenD access. The application trading calendar remains authoritative; the container timezone is an operational convenience, not a substitute for timezone-aware timestamps.

The old real-time monitor and Discord watchlist bot are optional and outside the deterministic analysis contract. They run only when the separate profile is requested:

```bash
docker compose --profile monitor up -d monitor discord-bot
```

Their alerts must not be represented as fixed-30 pipeline decisions or included in its performance statistics.

## Testing and deterministic replay

Run the repository test suite:

```bash
python -m pytest -q
```

Run focused safety tests while changing universe, scheduling, or persistence behavior:

```bash
python -m pytest -q \
  tests/test_fixed_universe.py \
  tests/test_universe_validation.py \
  tests/test_trading_calendar.py \
  tests/test_scheduler.py \
  tests/test_idempotency.py
```

A useful replay regression contains explicit timestamps and expected cutoff-visible bars, quotes, benchmarks, news, decisions, scores, gates, and reason codes. Test at least:

- A valid opening breakout and a valid short/breakdown setup.
- Failed breakouts, failed gaps, wide spreads, stale or missing data, and excessive extension.
- Sector/market conflict, ties and deterministic ordering, `NO_TRADE`, weekends, holidays, early closes, DST transitions, late starts, duplicate invocations, and restart recovery.
- Proof that a symbol outside the fixed 30 cannot enter through snapshots, replay files, RSS, or provider responses.

Replay output should be byte/logically stable for identical fixture, configuration, date, cutoff, and strategy version, apart from explicitly documented runtime metadata.

## Backtesting responsibly

Replay is the foundation for a backtest, but a passing fixture is not a performance study. A defensible fixed-universe evaluation should:

1. Archive point-in-time quotes, trades/bars, benchmark data, corporate actions, and allowable news for every cutoff. Do not substitute today's metadata for historical metadata.
2. Freeze and retain the universe and configuration snapshot used on each date. Report survivorship and selection bias explicitly; do not claim full-market coverage.
3. Run the three stages chronologically, excluding all post-cutoff evidence and preserving `NO_TRADE`, missing-data, and unavailable-symbol days.
4. Separate development, validation, and out-of-sample periods. Avoid tuning thresholds on the same dates used for reported results.
5. Model spread, configured slippage, gaps through stops, partial/no fills, halts, fees, and data delay. The built-in entry plan does not simulate execution.
6. Report sample size, coverage, rejected setups, turnover, sector concentration, drawdowns, uncertainty, and sensitivity to costs and thresholds—not only winning examples.
7. Keep replay artifacts and strategy versions so another operator can reproduce the result.

Historical performance, replay consistency, and a high score do not guarantee future returns.

## Periodic universe review

`review_fixed_universe.py` is intentionally advisory. It can evaluate recent dollar volume, regular/premarket spreads, data completeness, premarket-filter frequency, opening-watchlist frequency, opening-session dollar volume, and ATR range, then label each current member `KEEP`, `REVIEW`, or `POSSIBLE_REPLACEMENT`.

If the selected data adapter has no retained history for filter-pass or watchlist frequency, those fields are reported as unavailable and the name is conservatively marked for review; missing history is never treated as a perfect record.

It does not search the market for substitutes and cannot mutate configuration. If a human approves a change, document the reason, update the explicit symbol and ETF mappings, increment the strategy version, rerun validation, and treat results before and after the change as different strategy vintages.

## Legacy earnings and ex-dividend utilities

The repository retains older earnings and ex-dividend research utilities for compatibility. They are not stages of the fixed-30 workflow. They may use different universes, discovery logic, storage, schedules, and assumptions, so their symbols and outputs must never be merged into fixed-30 candidates, reports, replay datasets, or performance claims.

They are disabled from the default Compose startup and isolated behind `legacy-research`:

```bash
docker compose --profile legacy-research up -d \
  exdividend-research earnings-research
```

Their scheduler entry points are `scripts/run_daily_exdividend_scheduler.py` and `scripts/run_daily_earnings_scheduler.py`. Operate, test, and disclose them as separate research systems.

## Final safety notes

- Validate timestamps, entitlement status, corporate actions, and the original source before acting on any output.
- Treat missing data as uncertainty, not as zero, neutral, or permission to bypass a gate.
- A score is a deterministic prioritization aid, not a probability of profit.
- `CONFIRMED` describes rule satisfaction at one recorded cutoff; it is not an order recommendation.
- Human review, independent risk controls, and an execution system outside this repository would still be required for any real trading decision.
