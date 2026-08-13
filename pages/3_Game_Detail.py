"""Streamlit multipage wrapper for the game detail page."""

from __future__ import annotations

import runpy
from pathlib import Path

runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "src" / "app" / "game_detail_page.py"),
    run_name="__main__",
)
