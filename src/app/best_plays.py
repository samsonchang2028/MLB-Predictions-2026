"""Display-only ranking helpers for the Daily Predictions page.

This module does not create a betting policy, recompute market math, or derive
ROI. It ranks the already-shaped APP-007 board rows by their displayed
model-vs-market difference so Streamlit can show a simple "best plays" section.
"""

from __future__ import annotations

from typing import Any


def build_best_plays_report(
    rows: list[dict[str, Any]],
    *,
    limit: int = 5,
) -> dict[str, Any]:
    """Return deterministic display rows for the strongest differences.

    Rows are ranked by absolute displayed ``difference`` descending, then
    ``game_pk`` ascending for deterministic ties. PASS rows remain visible as
    PASS; callers can use ``status == "all_pass"`` for empty-state copy.
    """
    if not rows:
        return {"status": "no_predictions", "rows": []}

    ranked = sorted(
        rows,
        key=lambda row: (
            -abs(float(row.get("difference", 0.0) or 0.0)),
            row.get("game_pk") if row.get("game_pk") is not None else 0,
        ),
    )
    shaped = [
        {
            "Rank": index,
            "Pick": row.get("pick"),
            "Matchup": row.get("matchup"),
            "First Pitch (Pacific)": row.get("game_start_pacific"),
            "Difference": float(row.get("difference", 0.0) or 0.0),
            "Recommendation": row.get("recommendation", "PASS"),
            "Result": row.get("result_label") or row.get("result_status"),
            "game_pk": row.get("game_pk"),
            "play": bool(row.get("play")),
        }
        for index, row in enumerate(ranked[:limit], start=1)
    ]
    status = "ok" if any(row["play"] for row in shaped) else "all_pass"
    return {"status": status, "rows": shaped}
