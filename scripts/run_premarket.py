"""Compatibility entry point for the single deterministic premarket stage."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_market_analysis import main as run_market_analysis


def main() -> int:
    return run_market_analysis(["--stage", "premarket", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
