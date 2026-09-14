"""APP-016 — Streamlit CLV monitor helpers (read-only over prediction artifacts)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from market.clv import CLV_INSUFFICIENT_THRESHOLD
from market.clv_evaluation import build_clv_report, load_clv_population
from market.odds_closes import PredictionAnchor

CLV_DISCLAIMER = (
    "CLV observability only — not PLAY policy, not model validation, and not a "
    "betting recommendation. ADR-007 remains PROPOSED. Strict CLV requires "
    "prediction time < close snapshot ≤ first pitch from odds_closes.jsonl."
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def summarize_odds_closes(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    live = sum(1 for row in records if row.get("capture_method") == "live_pregame")
    backfill = sum(1 for row in records if row.get("capture_method") == "backfill_odds_books")
    other = len(records) - live - backfill
    latest_snapshot = None
    for row in records:
        ts = row.get("snapshot_timestamp")
        if ts is not None and (latest_snapshot is None or str(ts) > str(latest_snapshot)):
            latest_snapshot = ts
    return {
        "total_rows": len(records),
        "live_pregame_rows": live,
        "backfill_rows": backfill,
        "other_rows": other,
        "latest_snapshot_timestamp": latest_snapshot,
    }


def _format_pp(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.2f} pp"


def _format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}"


def _format_roi(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1%}"


def format_strict_clv_rows(
    population: Sequence[Mapping[str, Any]],
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Recent strict-close games for the dashboard table."""
    strict_rows = [row for row in population if row.get("strict_clv_valid")]
    strict_rows.sort(
        key=lambda row: (
            str(row.get("run_date") or ""),
            str(row.get("game_start_timestamp") or ""),
            row.get("game_pk") or 0,
        ),
        reverse=True,
    )
    display: list[dict[str, Any]] = []
    for row in strict_rows[:limit]:
        display.append(
            {
                "Slate": row.get("run_date"),
                "game_pk": row.get("game_pk"),
                "Edge side": row.get("edge_selected_side"),
                "CLV": _format_pp(row.get("clv")),
                "Close source": row.get("closing_odds_source"),
                "Model P (side)": _format_pct(row.get("prediction_market_p_selected")),
                "Close P (side)": _format_pct(row.get("closing_market_p_selected")),
                "Result": (
                    "Win"
                    if row.get("journal_correct") is True
                    else "Loss"
                    if row.get("journal_correct") is False
                    else "Pending"
                ),
            }
        )
    return display


def build_clv_dashboard(
    *,
    predictions_path: Path,
    journal_path: Path,
    odds_books_path: Path,
    odds_closes_path: Path | None = None,
    prediction_anchor: PredictionAnchor = "latest",
    run_id: str = "streamlit-live",
) -> dict[str, Any]:
    """Build a display-friendly CLV dashboard payload from on-disk artifacts."""
    daily_records = read_jsonl(predictions_path)
    journal_records = read_jsonl(journal_path)
    odds_records = read_jsonl(odds_books_path)
    odds_closes_records = read_jsonl(odds_closes_path) if odds_closes_path else []

    input_paths = {
        "predictions": str(predictions_path),
        "journal": str(journal_path),
        "odds_books": str(odds_books_path),
    }
    if odds_closes_path is not None:
        input_paths["odds_closes"] = str(odds_closes_path)

    if not daily_records:
        return {
            "status": "no_predictions",
            "disclaimer": CLV_DISCLAIMER,
            "capture_summary": summarize_odds_closes(odds_closes_records),
            "prediction_anchor": prediction_anchor,
        }

    try:
        report = build_clv_report(
            daily_records=daily_records,
            journal_records=journal_records,
            odds_records=odds_records,
            odds_closes_records=odds_closes_records or None,
            run_id=run_id,
            input_paths=input_paths,
            prediction_anchor=prediction_anchor,
        )
    except ValueError as exc:
        return {
            "status": "integrity_error",
            "disclaimer": CLV_DISCLAIMER,
            "error": str(exc),
            "capture_summary": summarize_odds_closes(odds_closes_records),
            "prediction_anchor": prediction_anchor,
        }

    strict = report["aggregate"]["strict_close"]
    pregame = report["aggregate"]["pregame_latest"]
    outcome = report["aggregate"]["outcome_all_latest_per_game"]
    verdict = report["verdict"]

    population_rows, _ = load_clv_population(
        daily_records,
        journal_records,
        odds_records,
        odds_closes_records or None,
        prediction_anchor=prediction_anchor,
    )

    return {
        "status": "ok",
        "disclaimer": CLV_DISCLAIMER,
        "generated_at": report.get("generated_at"),
        "prediction_anchor": prediction_anchor,
        "capture_summary": summarize_odds_closes(odds_closes_records),
        "population": report["population"],
        "rejection_summary": report["rejection_summary"],
        "metrics": {
            "strict_clv_n": strict.get("clv_n"),
            "strict_mean_clv_pp": strict.get("mean_clv"),
            "strict_median_clv_pp": strict.get("median_clv"),
            "strict_positive_clv_rate": strict.get("positive_clv_rate"),
            "strict_outcome_n": strict.get("outcome_n"),
            "strict_outcome_roi": strict.get("outcome_roi"),
            "pregame_clv_n": pregame.get("clv_n"),
            "pregame_mean_clv_pp": pregame.get("mean_clv"),
            "full_population_roi": outcome.get("roi"),
            "full_population_n": outcome.get("n_resolved"),
        },
        "verdict": verdict.get("verdict"),
        "verdict_case": verdict.get("case"),
        "verdict_rationale": verdict.get("rationale"),
        "adr_007_recommendation": verdict.get("adr_007_recommendation"),
        "adr_007_status": report.get("adr_007_status"),
        "min_clv_n": CLV_INSUFFICIENT_THRESHOLD,
        "strict_clv_rows": format_strict_clv_rows(population_rows),
    }
