"""Closing Line Value (CLV) evaluation for MARKET-008.

Builds the latest-per-game population from immutable prediction artifacts,
joins multi-book closing snapshots from odds_books.jsonl, computes selected-side
CLV with strict timestamp integrity, and emits JSON/Markdown study reports.
Read-only over source artifacts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.board import (
    DEFAULT_EDGE_THRESHOLD,
    _parse_datetime,
    _prediction_key,
    load_daily_board_with_diagnostics,
)
from app.dashboard_analytics import flat_stake_profit
from market.clv import (
    CLV_INSUFFICIENT_THRESHOLD,
    EDGE_BUCKET_SPECS_PP,
    LEAD_TIME_BUCKET_SPECS_HOURS,
    PREGAME_LATEST_CLOSING_DEFINITION,
    STRICT_CLOSING_DEFINITION,
    close_after_first_pitch,
    close_at_or_before_prediction,
    compute_clv,
    edge_bucket_label,
    interpret_clv_verdict,
    lead_time_bucket_label,
    pregame_latest_valid,
    row_clv_diagnostics,
    selected_side_market_probability,
    strict_close_valid,
)
from market.engine import no_vig_two_way
from market.play_policy import edge_selected_side, raw_model_side
from market.policy_evaluation import (
    EDGE_BUCKET_SPECS,
    JOURNAL_FIELD_NOTE,
    _ListStore,
    sha256_file,
)

DEFAULT_BOOKMAKER = "draftkings"


def bookmaker_from_source(source: Any) -> str:
    """Extract bookmaker key from prediction ``source`` (``the_odds_api:draftkings``)."""
    if not isinstance(source, str) or not source.strip():
        return DEFAULT_BOOKMAKER
    if ":" in source:
        return source.rsplit(":", 1)[-1].strip() or DEFAULT_BOOKMAKER
    return source.strip()


def index_odds_books(
    odds_records: Sequence[Mapping[str, Any]],
) -> dict[tuple[Any, Any, Any], Mapping[str, Any]]:
    """Index odds_books rows by ``(game_pk, run_date, bookmaker)``."""
    indexed: dict[tuple[Any, Any, Any], Mapping[str, Any]] = {}
    for record in odds_records:
        if not isinstance(record, Mapping):
            continue
        key = (record.get("game_pk"), record.get("run_date"), record.get("bookmaker"))
        indexed[key] = record
    return indexed


def _closing_market_home_probability(
    odds_row: Mapping[str, Any] | None,
) -> float | None:
    if odds_row is None:
        return None
    home = odds_row.get("home_american")
    away = odds_row.get("away_american")
    if not isinstance(home, int) or isinstance(home, bool):
        return None
    if not isinstance(away, int) or isinstance(away, bool):
        return None
    try:
        market = no_vig_two_way(home, away)
    except ValueError:
        return None
    return market.no_vig_home_probability


def _attach_raw_prediction_fields(
    rows: Sequence[Mapping[str, Any]],
    daily_records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_key = {
        _prediction_key(r.get("game_pk"), r.get("prediction_timestamp")): r
        for r in daily_records
        if isinstance(r, Mapping)
    }
    augmented: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        raw = by_key.get(_prediction_key(enriched.get("game_pk"), enriched.get("prediction_timestamp")))
        if raw is not None:
            enriched["home_american"] = raw.get("home_american")
            enriched["away_american"] = raw.get("away_american")
            enriched["game_start_timestamp"] = raw.get("game_start_timestamp")
            enriched["source"] = raw.get("source")
            enriched["run_date"] = raw.get("run_date")
            enriched["odds_snapshot_timestamp"] = raw.get("odds_snapshot_timestamp")
            game_start = _parse_datetime(raw.get("game_start_timestamp"))
            prediction_ts = enriched.get("prediction_timestamp")
            if game_start is not None and isinstance(prediction_ts, datetime):
                enriched["hours_to_first_pitch"] = (
                    game_start - prediction_ts
                ).total_seconds() / 3600.0
        augmented.append(enriched)
    return augmented


def load_clv_population(
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
    odds_records: Sequence[Mapping[str, Any]],
    *,
    model_version: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Latest pregame snapshot per ``game_pk`` with journal and closing odds joined."""
    board = load_daily_board_with_diagnostics(
        _ListStore(daily_records),
        journal_store=_ListStore(journal_records),
        edge_threshold=DEFAULT_EDGE_THRESHOLD,
    )
    rows = [dict(r) for r in board["rows"]]
    if model_version is not None:
        rows = [r for r in rows if r.get("model_version") == model_version]

    rows = _attach_raw_prediction_fields(rows, daily_records)
    odds_index = index_odds_books(odds_records)
    journal_by_key = {
        _prediction_key(r.get("game_pk"), r.get("prediction_timestamp")): r
        for r in journal_records
        if isinstance(r, Mapping)
    }

    journal_mismatches = 0
    prediction_after_first_pitch = 0
    enriched: list[dict[str, Any]] = []

    for row in rows:
        record = dict(row)
        record["raw_model_side"] = raw_model_side(record)
        record["edge_selected_side"] = edge_selected_side(record)
        record.update(row_clv_diagnostics(record))

        journal = journal_by_key.get(
            _prediction_key(record.get("game_pk"), record.get("prediction_timestamp"))
        )
        if journal is not None:
            predicted_home = journal.get("predicted_home_win")
            edge_side_home = record.get("edge_selected_side") == "HOME"
            if isinstance(predicted_home, bool) and predicted_home != edge_side_home:
                journal_mismatches += 1
            record["journal_predicted_home_win"] = predicted_home
            record["journal_correct"] = journal.get("correct")

        bookmaker = bookmaker_from_source(record.get("source"))
        record["bookmaker"] = bookmaker
        odds_key = (record.get("game_pk"), record.get("run_date"), bookmaker)
        odds_row = odds_index.get(odds_key)
        record["closing_odds_row_present"] = odds_row is not None

        prediction_ts = record.get("prediction_timestamp")
        if not isinstance(prediction_ts, datetime):
            prediction_ts = _parse_datetime(prediction_ts)
            record["prediction_timestamp"] = prediction_ts

        start_ts = _parse_datetime(record.get("game_start_timestamp"))
        close_ts = _parse_datetime(odds_row.get("snapshot_timestamp")) if odds_row else None

        if (
            isinstance(prediction_ts, datetime)
            and start_ts is not None
            and prediction_ts >= start_ts
        ):
            prediction_after_first_pitch += 1

        side = record.get("edge_selected_side")
        pred_market_p_selected = (
            selected_side_market_probability(record, side=side)
            if isinstance(side, str)
            else None
        )
        closing_market_p_home = _closing_market_home_probability(odds_row)
        closing_market_p_selected = (
            closing_market_p_home
            if side == "HOME"
            else (1.0 - closing_market_p_home if closing_market_p_home is not None else None)
        )

        strict_valid = strict_close_valid(prediction_ts, close_ts, start_ts)
        pregame_valid = pregame_latest_valid(close_ts, start_ts)

        clv = None
        pregame_clv = None
        if pred_market_p_selected is not None and closing_market_p_selected is not None:
            pregame_clv = compute_clv(
                prediction_market_p_selected=pred_market_p_selected,
                closing_market_p_selected=closing_market_p_selected,
            )
            if strict_valid:
                clv = pregame_clv

        rejection_reason = None
        if odds_row is None:
            rejection_reason = "missing_bookmaker_row"
        elif close_ts is None:
            rejection_reason = "missing_close_timestamp"
        elif close_after_first_pitch(close_ts, start_ts):
            rejection_reason = "close_after_first_pitch"
        elif close_at_or_before_prediction(prediction_ts, close_ts):
            rejection_reason = "close_at_or_before_prediction"
        elif not strict_valid:
            rejection_reason = "strict_window_invalid"

        record.update(
            {
                "prediction_market_p_selected": pred_market_p_selected,
                "closing_market_p_home": closing_market_p_home,
                "closing_market_p_selected": closing_market_p_selected,
                "closing_snapshot_timestamp": (
                    close_ts.isoformat() if isinstance(close_ts, datetime) else None
                ),
                "strict_clv_valid": strict_valid,
                "pregame_latest_valid": pregame_valid,
                "clv": clv,
                "pregame_latest_clv": pregame_clv if pregame_valid else None,
                "strict_clv_rejection_reason": None if strict_valid else rejection_reason,
                "lead_time_bucket": lead_time_bucket_label(record.get("hours_to_first_pitch")),
                "edge_bucket_coarse": edge_bucket_label(
                    abs(float(record.get("edge", 0.0))),
                    EDGE_BUCKET_SPECS_PP,
                ),
                "edge_bucket_fine": edge_bucket_label(
                    abs(float(record.get("edge", 0.0))),
                    EDGE_BUCKET_SPECS,
                ),
            }
        )
        enriched.append(record)

    meta = {
        "n_latest_per_game": len(enriched),
        "n_malformed_daily_skips": len(board["skipped"]),
        "journal_predicted_home_win_note": JOURNAL_FIELD_NOTE,
        "journal_side_mismatches": journal_mismatches,
        "prediction_after_first_pitch": prediction_after_first_pitch,
        "model_version_filter": model_version,
        "strict_closing_definition": STRICT_CLOSING_DEFINITION,
        "pregame_latest_closing_definition": PREGAME_LATEST_CLOSING_DEFINITION,
    }
    return enriched, meta


def _bet_won(row: Mapping[str, Any]) -> bool | None:
    actual = row.get("actual_home_win")
    side = row.get("edge_selected_side")
    if not isinstance(actual, bool) or not isinstance(side, str):
        return None
    return actual if side == "HOME" else not actual


def _bet_american(row: Mapping[str, Any]) -> int | None:
    side = row.get("edge_selected_side")
    if side == "HOME":
        value = row.get("home_american")
    elif side == "AWAY":
        value = row.get("away_american")
    else:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def outcome_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Flat 1u ROI on edge-selected side for resolved games only."""
    profits: list[float] = []
    wins = losses = 0
    pending = 0
    for row in rows:
        won = _bet_won(row)
        if won is None:
            pending += 1
            continue
        american = _bet_american(row)
        profit = flat_stake_profit(won=won, american=american)
        if profit is None:
            pending += 1
            continue
        profits.append(profit)
        if won:
            wins += 1
        else:
            losses += 1
    finished = wins + losses
    return {
        "n_candidates": len(rows),
        "n_resolved": finished,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": wins / finished if finished else None,
        "roi": sum(profits) / len(profits) if profits else None,
        "units": float(sum(profits)) if profits else None,
        "staked_units": len(profits),
    }


def clv_metrics(rows: Sequence[Mapping[str, Any]], *, strict: bool = True) -> dict[str, Any]:
    """Aggregate CLV on strict-close or pregame-latest populations."""
    key = "strict_clv_valid" if strict else "pregame_latest_valid"
    clv_key = "clv" if strict else "pregame_latest_clv"
    eligible = [row for row in rows if row.get(key)]
    clvs = [float(row[clv_key]) for row in eligible if row.get(clv_key) is not None]
    outcome = outcome_metrics(eligible)
    return {
        "population": "strict_close" if strict else "pregame_latest",
        "clv_n": len(clvs),
        "mean_clv": sum(clvs) / len(clvs) if clvs else None,
        "median_clv": _median(clvs),
        "positive_clv_rate": sum(1 for value in clvs if value > 0.0) / len(clvs) if clvs else None,
        "outcome_n": outcome["n_resolved"],
        "outcome_roi": outcome["roi"],
        "outcome_units": outcome["units"],
        "low_confidence": len(clvs) < CLV_INSUFFICIENT_THRESHOLD,
    }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def rejection_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("strict_clv_valid"):
            continue
        reason = row.get("strict_clv_rejection_reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _slice_clv_metrics(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    metrics = clv_metrics(group, strict=True)
    metrics["edge_selected_side_counts"] = _count_values(group, "edge_selected_side")
    return metrics


def _count_values(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        label = str(value) if value is not None else "unknown"
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def build_clv_slices(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    strict_rows = [row for row in rows if row.get("strict_clv_valid")]

    def bucket_slice(field: str) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in strict_rows:
            label = str(row.get(field) or "unknown")
            grouped.setdefault(label, []).append(row)
        return {label: _slice_clv_metrics(group) for label, group in sorted(grouped.items())}

    crossover = {
        "crossover": _slice_clv_metrics([r for r in strict_rows if r.get("crossover") is True]),
        "no_crossover": _slice_clv_metrics([r for r in strict_rows if r.get("crossover") is False]),
    }
    agreement = {
        "model_market_agree": _slice_clv_metrics(
            [r for r in strict_rows if r.get("model_market_agree") is True]
        ),
        "model_market_disagree": _slice_clv_metrics(
            [r for r in strict_rows if r.get("model_market_agree") is False]
        ),
    }
    selected_side = {
        "HOME": _slice_clv_metrics([r for r in strict_rows if r.get("edge_selected_side") == "HOME"]),
        "AWAY": _slice_clv_metrics([r for r in strict_rows if r.get("edge_selected_side") == "AWAY"]),
    }

    return {
        "edge_buckets_coarse_pp": bucket_slice("edge_bucket_coarse"),
        "edge_buckets_fine_pp": bucket_slice("edge_bucket_fine"),
        "crossover": crossover,
        "agreement": agreement,
        "selected_side": selected_side,
        "market_favorite_role": bucket_slice("market_favorite_role"),
        "lead_time": bucket_slice("lead_time_bucket"),
        "bookmaker": bucket_slice("bookmaker"),
    }


def build_clv_report(
    *,
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
    odds_records: Sequence[Mapping[str, Any]],
    run_id: str,
    input_paths: Mapping[str, str],
    model_version: str | None = None,
) -> dict[str, Any]:
    population, population_meta = load_clv_population(
        daily_records,
        journal_records,
        odds_records,
        model_version=model_version,
    )
    if population_meta["journal_side_mismatches"]:
        raise ValueError(
            "journal predicted_home_win mismatches edge-selected side: "
            f"{population_meta['journal_side_mismatches']}"
        )
    if population_meta["prediction_after_first_pitch"]:
        raise ValueError(
            "predictions at or after first pitch in latest-per-game population: "
            f"{population_meta['prediction_after_first_pitch']}"
        )

    strict_metrics = clv_metrics(population, strict=True)
    pregame_metrics = clv_metrics(population, strict=False)
    full_outcome = outcome_metrics(population)
    verdict_info = interpret_clv_verdict(
        mean_clv=strict_metrics["mean_clv"],
        roi=strict_metrics["outcome_roi"],
        clv_n=strict_metrics["clv_n"],
    )

    artifact_hashes = {name: sha256_file(Path(path)) for name, path in input_paths.items()}

    return {
        "status": "MARKET_008_CLV_VALIDATION",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": dict(input_paths),
        "artifact_hashes": artifact_hashes,
        "population": population_meta,
        "journal_field_normalization": JOURNAL_FIELD_NOTE,
        "rejection_summary": rejection_summary(population),
        "aggregate": {
            "strict_close": strict_metrics,
            "pregame_latest": pregame_metrics,
            "outcome_all_latest_per_game": full_outcome,
        },
        "slices": build_clv_slices(population),
        "verdict": verdict_info,
        "adr_007_status": "PROPOSED — not accepted; observability only",
    }


def _append_slice_table(
    lines: list[str],
    title: str,
    slices: Mapping[str, Mapping[str, Any]],
) -> None:
    lines.extend(
        [
            "",
            f"## {title}",
            "",
            "| slice | clv_n | mean_clv | outcome_n | outcome_roi |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for label, metrics in sorted(slices.items()):
        lines.append(
            f"| {label} | {metrics.get('clv_n')} | {metrics.get('mean_clv')} | "
            f"{metrics.get('outcome_n')} | {metrics.get('outcome_roi')} |"
        )


def render_markdown_report(report: Mapping[str, Any]) -> str:
    population = report["population"]
    strict = report["aggregate"]["strict_close"]
    pregame = report["aggregate"]["pregame_latest"]
    outcome = report["aggregate"]["outcome_all_latest_per_game"]
    verdict = report["verdict"]

    lines = [
        f"# MARKET-008 CLV Validation ({report.get('run_id')})",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "## Closing-line definitions",
        "",
        f"- **Strict (primary):** {population['strict_closing_definition']}",
        f"- **Pregame latest (secondary):** {population['pregame_latest_closing_definition']}",
        "",
        "## Population",
        "",
        f"- Latest-per-game rows: {population['n_latest_per_game']}",
        f"- Malformed daily skips: {population['n_malformed_daily_skips']}",
        f"- Journal side mismatches: {population['journal_side_mismatches']}",
        "",
        report.get("journal_field_normalization", ""),
        "",
        "## Aggregate CLV (strict close)",
        "",
        f"- CLV N: {strict['clv_n']}",
        f"- Mean CLV (pp): {strict['mean_clv']}",
        f"- Median CLV (pp): {strict['median_clv']}",
        f"- Positive CLV rate: {strict['positive_clv_rate']}",
        f"- Outcome N (same strict population): {strict['outcome_n']}",
        f"- Outcome ROI (strict population): {strict['outcome_roi']}",
        "",
        "## Aggregate CLV (pregame latest)",
        "",
        f"- CLV N: {pregame['clv_n']}",
        f"- Mean CLV (pp): {pregame['mean_clv']}",
        "",
        "## Outcome / ROI (all latest-per-game, resolved only)",
        "",
        f"- Outcome N: {outcome['n_resolved']}",
        f"- Pending excluded: {outcome['pending']}",
        f"- ROI: {outcome['roi']}",
        f"- Units: {outcome['units']}",
        "",
        "## Strict-close rejection summary",
        "",
    ]
    rejection = report.get("rejection_summary") or {}
    if rejection:
        for reason, count in rejection.items():
            lines.append(f"- {reason}: {count}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"**{verdict.get('verdict')}** (case {verdict.get('case')})",
            "",
            verdict.get("rationale", ""),
            "",
            f"ADR-007 recommendation: {verdict.get('adr_007_recommendation')}",
            "",
            f"ADR-007 status: {report.get('adr_007_status')}",
        ]
    )
    slice_data = report.get("slices") or {}
    _append_slice_table(lines, "Strict-close slices (coarse edge buckets, pp)", slice_data.get("edge_buckets_coarse_pp") or {})
    _append_slice_table(lines, "Strict-close slices (crossover)", slice_data.get("crossover") or {})
    _append_slice_table(lines, "Strict-close slices (model/market agreement)", slice_data.get("agreement") or {})
    _append_slice_table(lines, "Strict-close slices (selected side)", slice_data.get("selected_side") or {})
    _append_slice_table(
        lines,
        "Strict-close slices (market favorite role)",
        slice_data.get("market_favorite_role") or {},
    )
    _append_slice_table(lines, "Strict-close slices (lead time)", slice_data.get("lead_time") or {})
    _append_slice_table(lines, "Strict-close slices (bookmaker)", slice_data.get("bookmaker") or {})
    lines.append("")
    return "\n".join(lines) + "\n"


def json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")
