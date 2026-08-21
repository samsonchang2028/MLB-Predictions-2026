"""Streamlit multipage wrapper for prospective evaluation dashboard."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

runpy.run_path(str(_SRC / "app" / "prospective_evaluation_page.py"), run_name="__main__")
