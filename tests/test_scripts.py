from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


TRADE_DATE = "2026-07-16"
STRATEGY_VERSION = "fixed-us-large-cap-opening-v1"
REQUIRED_FINAL_ARTIFACTS = {
    "run_manifest.json",
    "config_snapshot.json",
    "universe_validation.json",
    "premarket.json",
    "premarket.md",
    "opening_5m.json",
    "opening_5m.md",
    "opening_15m.json",
    "final_report.json",
    "final_report.md",
}


def _write_env(tmp_path: Path) -> Path:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "DEMO_MODE=true",
                f"DATA_DIR={tmp_path / 'data'}",
                f"MARKET_OUTPUT_DIR={tmp_path / 'default-output'}",
                "NEWS_RSS_URLS=",
                "DISCORD_PREMARKET_WEBHOOK_URL=",
                "DISCORD_OPEN_CONFIRMATION_WEBHOOK_URL=",
                "",
            )
        ),
        encoding="utf-8",
    )
    return env_file


def _run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
        env=environment,
        check=False,
    )


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_fixed_universe_validation_and_review_clis_are_non_mutating(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env_file = _write_env(tmp_path)
    config = root / "config" / "market_strategy.json"

    validation = _run(
        root,
        "scripts/validate_universe.py",
        "--date",
        TRADE_DATE,
        "--data-source",
        "mock",
        "--env-file",
        str(env_file),
        "--config",
        str(config),
    )
    assert validation.returncode == 0, validation.stderr
    health = json.loads(validation.stdout)
    assert health["trade_date"] == TRADE_DATE
    assert health["configured_symbol_count"] == 30
    assert health["configured_benchmark_count"] == 20
    assert health["duplicate_symbol_check"] is True
    assert len(health["benchmark_mappings"]) == 30
    assert health["unavailable_symbols"] == []

    review = _run(
        root,
        "scripts/review_fixed_universe.py",
        "--date",
        TRADE_DATE,
        "--data-source",
        "mock",
        "--env-file",
        str(env_file),
        "--config",
        str(config),
    )
    assert review.returncode == 0, review.stderr
    report = json.loads(review.stdout)
    assert report["configured_stock_count"] == 30
    assert report["configuration_mutated"] is False
    assert len(report["recommendations"]) == 30
    assert {item["symbol"] for item in report["recommendations"]} == {
        "AAPL", "MSFT", "NVDA", "AMD", "AVGO", "TSM", "GOOGL", "META", "NFLX", "AMZN",
        "TSLA", "HD", "MCD", "JPM", "BAC", "C", "GS", "LLY", "UNH", "JNJ", "CAT", "GE",
        "BA", "XOM", "CVX", "WMT", "COST", "LIN", "NEE", "PLD",
    }


def test_mock_premarket_cli_persists_deterministic_dated_snapshot(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env_file = _write_env(tmp_path)
    output_root = tmp_path / "runs"
    result = _run(
        root,
        "scripts/run_market_analysis.py",
        "--stage",
        "premarket",
        "--date",
        TRADE_DATE,
        "--data-source",
        "mock",
        "--env-file",
        str(env_file),
        "--config",
        str(root / "config" / "market_strategy.json"),
        "--output-dir",
        str(output_root),
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert "[premarket] COMPLETED" in result.stdout

    run_dir = output_root / TRADE_DATE
    assert {path.name for path in run_dir.iterdir() if path.is_file()} == {
        "run_manifest.json",
        "config_snapshot.json",
        "premarket.json",
        "premarket.md",
    }
    snapshot = _json(run_dir / "premarket.json")
    assert snapshot["stage"] == "premarket"
    assert snapshot["status"] == "COMPLETED"
    assert snapshot["trade_date"] == TRADE_DATE
    assert snapshot["strategy_version"] == STRATEGY_VERSION
    assert snapshot["run_key"] == f"{TRADE_DATE}:premarket:{STRATEGY_VERSION}"
    assert snapshot["scheduled_cutoff"] == f"{TRADE_DATE}T08:45:00-04:00"
    assert snapshot["configured_stock_count"] == 30
    assert len(snapshot["analyzed_symbols"]) == 30
    assert snapshot["outside_symbols_analyzed"] == []

    manifest = _json(run_dir / "run_manifest.json")
    assert set(manifest["stages"]) == {"premarket"}
    assert manifest["stages"]["premarket"]["files"] == ["premarket.json", "premarket.md"]
    assert _json(run_dir / "config_snapshot.json")["strategy_version"] == STRATEGY_VERSION
    assert "# Premarket Analysis" in (run_dir / "premarket.md").read_text(encoding="utf-8")


def test_replay_opening_15m_cli_runs_prerequisites_and_writes_exact_final_layout(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env_file = _write_env(tmp_path)
    output_root = tmp_path / "runs"
    fixture = root / "tests" / "fixtures" / "valid_opening_breakout.json"
    result = _run(
        root,
        "scripts/run_market_analysis.py",
        "--stage",
        "opening-15m",
        "--data-source",
        "replay",
        "--input-file",
        str(fixture),
        "--env-file",
        str(env_file),
        "--config",
        str(root / "config" / "market_strategy.json"),
        "--output-dir",
        str(output_root),
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    for stage in ("universe_validation", "premarket", "opening_5m", "opening_15m"):
        assert f"[{stage}] COMPLETED" in result.stdout

    run_dir = output_root / TRADE_DATE
    assert {path.name for path in run_dir.iterdir() if path.is_file()} == REQUIRED_FINAL_ARTIFACTS

    expected_cutoffs = {
        "universe_validation": "08:20",
        "premarket": "08:45",
        "opening_5m": "09:35",
        "opening_15m": "09:45",
    }
    for stage, hhmm in expected_cutoffs.items():
        payload = _json(run_dir / f"{stage}.json")
        assert payload["status"] == "COMPLETED"
        assert payload["stage"] == stage
        assert payload["scheduled_cutoff"] == f"{TRADE_DATE}T{hhmm}:00-04:00"
        assert payload["actual_started_at"] == payload["scheduled_cutoff"]
        assert payload["late_start"] is False
        assert payload["run_key"] == f"{TRADE_DATE}:{stage}:{STRATEGY_VERSION}"

    opening_5m = _json(run_dir / "opening_5m.json")
    final_snapshot = _json(run_dir / "opening_15m.json")
    final_report = _json(run_dir / "final_report.json")
    assert opening_5m["evidence_cutoff"] == f"{TRADE_DATE}T09:35:00-04:00"
    assert final_snapshot["evidence_cutoff"] == f"{TRADE_DATE}T09:45:00-04:00"
    watchlist = _json(run_dir / "premarket.json")["opening_watchlist"]
    assert final_snapshot["analyzed_symbols"] == [item["symbol"] for item in watchlist]
    assert final_snapshot["outside_symbols_analyzed"] == []
    assert final_report["stage"] == "opening_15m"
    assert final_report["status"] == "COMPLETED"
    assert final_report["trade_date"] == TRADE_DATE
    assert final_report["evidence_cutoff"] == f"{TRADE_DATE}T09:45:00-04:00"
    assert final_report["analyzed_symbols"] == final_snapshot["analyzed_symbols"]
    assert final_report == final_snapshot
    assert "# Fifteen-Minute Final Confirmation" in (run_dir / "final_report.md").read_text(encoding="utf-8")

    manifest = _json(run_dir / "run_manifest.json")
    assert set(manifest["stages"]) == set(expected_cutoffs)
    assert manifest["stages"]["opening_15m"]["files"] == [
        "opening_15m.json",
        "final_report.md",
        "final_report.json",
    ]

    repeat_root = tmp_path / "repeat-runs"
    repeat = _run(
        root,
        "scripts/run_market_analysis.py",
        "--stage",
        "opening-15m",
        "--data-source",
        "replay",
        "--input-file",
        str(fixture),
        "--env-file",
        str(env_file),
        "--config",
        str(root / "config" / "market_strategy.json"),
        "--output-dir",
        str(repeat_root),
        "--dry-run",
    )
    assert repeat.returncode == 0, repeat.stderr
    repeated_dir = repeat_root / TRADE_DATE
    for filename in REQUIRED_FINAL_ARTIFACTS - {"run_manifest.json"}:
        assert (run_dir / filename).read_bytes() == (repeated_dir / filename).read_bytes()
