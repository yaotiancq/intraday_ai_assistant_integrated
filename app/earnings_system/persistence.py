from __future__ import annotations

from pathlib import Path
from typing import Any

from app.utils.file_io import write_json, write_text


def ensure_earnings_dirs(output_dir: Path) -> None:
    for name in [
        "calendar",
        "previews",
        "post_release",
        "market_reaction",
        "media",
        "notifications",
        "logs",
    ]:
        (output_dir / name).mkdir(parents=True, exist_ok=True)


def write_partitioned_json(output_dir: Path, section: str, date_key: str, data: Any) -> Path:
    path = output_dir / section / f"{date_key}.json"
    write_json(path, data)
    return path


def write_partitioned_markdown(output_dir: Path, section: str, date_key: str, text: str) -> Path:
    path = output_dir / section / f"{date_key}.md"
    write_text(path, text)
    return path

