import os
import subprocess
import sys
from pathlib import Path


def write_env(tmp_path: Path) -> Path:
    env = tmp_path / '.env'
    env.write_text(f'''
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5-mini
DEMO_MODE=true
DATA_DIR={tmp_path / 'data'}
TIMEZONE=America/Los_Angeles
CORE_SYMBOLS=SPY,QQQ,NVDA,AMD,MU
INDEX_SYMBOLS=SPY,QQQ,IWM,DIA
SECTOR_ETFS=SMH,XLK,XLE
NEWS_RSS_URLS=
MAX_A_TIER=3
MAX_B_TIER=4
DISCORD_PREMARKET_WEBHOOK_URL=
''', encoding='utf-8')
    return env


def run_cmd(args, cwd):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=90,
        env=env,
    )


def test_run_premarket_script_dry_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = write_env(tmp_path)
    res = run_cmd([
        'scripts/run_premarket.py',
        '--env-file', str(env),
        '--dry-run',
        '--force-run',
        '--allow-non-trading-day-test',
    ], root)
    assert res.returncode == 0, res.stderr
    assert '截至时间' in res.stdout
    assert (tmp_path / 'data' / 'premarket_report.md').exists()
    assert (tmp_path / 'data' / 'premarket_report_status.json').exists()


def test_run_premarket_script_disable_llm(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = write_env(tmp_path)
    res = run_cmd([
        'scripts/run_premarket.py',
        '--env-file', str(env),
        '--dry-run',
        '--force-run',
        '--allow-non-trading-day-test',
        '--disable-llm',
    ], root)
    assert res.returncode == 0, res.stderr
    assert '[premarket_report] mode=rule_based reason=llm_disabled' in res.stdout
    assert '报告模式：规则兜底' in res.stdout


def test_run_premarket_discord_failure_preserves_local_outputs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = write_env(tmp_path)
    res = run_cmd([
        'scripts/run_premarket.py',
        '--env-file', str(env),
        '--send-discord',
        '--force-run',
        '--allow-non-trading-day-test',
        '--disable-llm',
    ], root)
    assert res.returncode == 0, res.stderr
    assert '[discord_error]' in res.stdout
    assert (tmp_path / 'data' / 'premarket_report.md').exists()
    assert (tmp_path / 'data' / 'premarket_report_status.json').exists()


def test_run_single_stock_script_dry_run(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = write_env(tmp_path)
    res = run_cmd(['scripts/run_single_stock_analysis.py', '--symbol', 'SATS', '--env-file', str(env), '--dry-run'], root)
    assert res.returncode == 0, res.stderr
    assert ('一句话结论' in res.stdout) or ('结论' in res.stdout)
    assert (tmp_path / 'data' / 'evidence_pack_SATS.json').exists()


def test_build_news_rss_urls_generates_per_symbol_feeds():
    from scripts.run_premarket import build_news_rss_urls

    urls = build_news_rss_urls(['https://example.com/general.rss'], ['NVDA', 'AMD'])
    assert 'https://example.com/general.rss' in urls
    assert any('s=NVDA' in u for u in urls)
    assert any('s=AMD' in u for u in urls)
