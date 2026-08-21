"""Streamlit multipage wrapper for betting results dashboard."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

runpy.run_path(str(_SRC / "app" / "betting_results_page.py"), run_name="__main__")
