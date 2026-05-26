import os
import subprocess
import sys
from pathlib import Path

import pytest


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
