import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault('ENV_FILE', '/tmp/intraday-ai-assistant-test-missing.env')
os.environ['HOME'] = '/tmp'
monitor = pytest.importorskip('scripts.run_realtime_monitor')


def test_bar_period_configs_use_typical_breakout_lookbacks():
    assert monitor.get_bar_period_config('1m').breakout_lookback == 20
    assert monitor.get_bar_period_config('3m').breakout_lookback == 10
    assert monitor.get_bar_period_config('5m').breakout_lookback == 6
    assert monitor.get_bar_period_config('K_3M').label == '3m'


def test_engine_period_switch_resets_symbol_state():
    engine = monitor.OpeningMomentumSignalEngine(
        symbols=['US.SPY'],
        workers=1,
        bar_period_config=monitor.get_bar_period_config('1m'),
    )
    try:
        state = engine.states['US.SPY']
        with state.lock:
            state.current_bar = monitor.Bar(
                code='US.SPY',
                time_key='2026-05-26 09:30:00',
                dt=monitor.parse_time_key('2026-05-26 09:30:00'),
                open=100,
                high=101,
                low=99,
                close=100.5,
                volume=1000,
                turnover=100500,
            )
            state.position = 'LONG'

        engine.set_bar_period_config(monitor.get_bar_period_config('5m'))

        assert engine.strategy_status()['bar_period'] == '5m'
        assert engine.strategy_status()['breakout_lookback'] == 6
        assert state.current_bar is None
        assert state.position == 'FLAT'
    finally:
        engine.shutdown()


def test_monitor_refuses_empty_admin_token(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.pop('WATCHLIST_ADMIN_TOKEN', None)
    env.pop('MONITOR_ALLOW_EMPTY_ADMIN_TOKEN', None)
    env['HOME'] = str(tmp_path)

    res = subprocess.run(
        [
            sys.executable,
            'scripts/run_realtime_monitor.py',
            '--symbols',
            'US.SPY',
        ],
        cwd=root,
        text=True,
        encoding='utf-8',
        errors='replace',
        capture_output=True,
        timeout=15,
        env=env,
    )

    if "No module named 'futu'" in res.stderr:
        pytest.skip('futu package is not installed in this environment')

    assert res.returncode == 2
    assert 'missing_admin_token' in res.stdout
