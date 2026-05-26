from pathlib import Path

from app.config import load_settings


def test_load_settings_reads_futu_extended_time(tmp_path: Path):
    env = tmp_path / '.env'
    env.write_text(
        f'''
OPENAI_API_KEY=
DATA_DIR={tmp_path / 'data'}
FUTU_EXTENDED_TIME=false
''',
        encoding='utf-8',
    )

    settings = load_settings(env)
    assert settings.futu_extended_time is False
