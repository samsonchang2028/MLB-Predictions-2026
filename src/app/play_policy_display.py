"""MARKET-007 shadow PLAY policy display adapter.

Read-only observability over :mod:`market.play_policy`. Baseline PLAY/PASS labels
follow the existing board row ``play`` flag (legacy ``abs(edge) >= 0.02``).
Exploratory shadow policies are **NOT PRODUCTION** and **NOT A BETTING
RECOMMENDATION**.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from app.board import DEFAULT_EDGE_THRESHOLD, _parse_datetime
from app.dashboard_analytics import flat_stake_profit, read_jsonl
from market.play_policy import (
    PolicyDecision,
    PolicyReason,
    PolicySpec,
    evaluate_policy,
    is_crossover,
    market_side,
    model_market_agree,
    policy_by_name,
    raw_model_side,
)
from market.policy_evaluation import (
    _bet_american,
    _bet_won,
    load_canonical_population,
    policy_play_metrics,
)

SHADOW_NOT_PRODUCTION_NOTE = (
    "Shadow policy comparison is exploratory observability only — "
    "NOT PRODUCTION and NOT A BETTING RECOMMENDATION. "
    "Baseline PLAY/PASS on the board is unchanged."
)

BASELINE_POLICY = policy_by_name("baseline")
EXPLORATORY_NO_CROSSOVER_POLICY = policy_by_name("no_crossover_abs_edge_ge_0.020")
EXPLORATORY_CONSENSUS_POLICY = policy_by_name("consensus_conf_floor_0.55")

EXPLORATORY_SHADOW_POLICIES: tuple[tuple[str, PolicySpec], ...] = (
    ("no_crossover", EXPLORATORY_NO_CROSSOVER_POLICY),
    ("consensus", EXPLORATORY_CONSENSUS_POLICY),
)

POLICY_DISPLAY_LABELS: dict[str, str] = {
    "baseline": "Baseline legacy (|edge| >= 2%)",
    "no_crossover": "Exploratory no-crossover (family B @ 2%)",
    "consensus": "Exploratory consensus (family C @ 55% floor)",
    "raw_model_favorite": "Raw model favorite (diagnostic)",
}


def merge_policy_evaluation_row(
    board_row: Mapping[str, Any],
    raw_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge board display fields with odds metadata required by play_policy."""
    merged: dict[str, Any] = dict(board_row)
    if raw_record is None:
        return merged
    for key in (
        "home_american",
        "away_american",
        "game_start_timestamp",
        "prediction_timestamp",
        "odds_snapshot_timestamp",
        "build_id",
        "source",
    ):
        if key in raw_record and raw_record.get(key) is not None:
            merged[key] = raw_record[key]
    return merged


def baseline_play_label(board_row: Mapping[str, Any]) -> str:
    """Legacy board PLAY/PASS label — uses the unchanged board ``play`` flag."""
    return "BASELINE PLAY" if board_row.get("play") else "PASS"


def shadow_status_label(decision: PolicyDecision) -> str:
    if decision.play:
        return "SHADOW CANDIDATE"
    reasons = set(decision.reasons)
    if PolicyReason.CROSSOVER_BLOCKED.value in reasons:
        return "CROSSOVER BLOCKED"
    if PolicyReason.LARGE_DISAGREEMENT_BLOCKED.value in reasons:
        return "LARGE DISAGREEMENT REVIEW"
    return "NO CANDIDATE"


def selected_side_probability(row: Mapping[str, Any], side: str | None) -> float | None:
    if side is None:
        return None
    p_home = row.get("model_probability")
    if not isinstance(p_home, (int, float)) or isinstance(p_home, bool):
        return None
    return float(p_home) if side == "HOME" else 1.0 - float(p_home)


def derive_policy_risk_flags(
    row: Mapping[str, Any],
    *decisions: PolicyDecision,
) -> list[str]:
    flags: list[str] = []
    crossover = is_crossover(row)
    if crossover is True:
        flags.append("CROSSOVER")
    agree = model_market_agree(row)
    if agree is False:
        flags.append("MODEL_MARKET_DISAGREE")
    p_home = row.get("model_probability")
    m_home = row.get("market_probability")
    if isinstance(p_home, (int, float)) and isinstance(m_home, (int, float)):
        if abs(float(p_home) - float(m_home)) >= 0.08:
            flags.append("LARGE_MODEL_MARKET_DISAGREEMENT")
    for decision in decisions:
        if PolicyReason.MISSING_ODDS.value in decision.reasons:
            flags.append("MISSING_ODDS")
            break
    return flags


def evaluate_shadow_policy(row: Mapping[str, Any], policy: PolicySpec) -> PolicyDecision:
    return evaluate_policy(row, policy)


def enrich_board_row_with_shadow(
    board_row: Mapping[str, Any],
    raw_record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach shadow policy labels, reason codes, and risk flags to one board row."""
    merged = merge_policy_evaluation_row(board_row, raw_record)
    baseline_decision = evaluate_shadow_policy(merged, BASELINE_POLICY)
    no_crossover_decision = evaluate_shadow_policy(merged, EXPLORATORY_NO_CROSSOVER_POLICY)
    consensus_decision = evaluate_shadow_policy(merged, EXPLORATORY_CONSENSUS_POLICY)
    model_fav = raw_model_side(merged)
    market_fav = market_side(merged)
    return {
        **dict(board_row),
        "raw_model_favorite": model_fav,
        "market_favorite": market_fav,
        "model_market_agree": model_market_agree(merged),
        "raw_model_side_probability": selected_side_probability(merged, model_fav),
        "market_side_probability": selected_side_probability(merged, market_fav),
        "baseline_play_label": baseline_play_label(board_row),
        "baseline_reason_codes": baseline_decision.reasons,
        "no_crossover_label": shadow_status_label(no_crossover_decision),
        "no_crossover_reason_codes": no_crossover_decision.reasons,
        "consensus_label": shadow_status_label(consensus_decision),
        "consensus_reason_codes": consensus_decision.reasons,
        "risk_flags": derive_policy_risk_flags(
            merged,
            no_crossover_decision,
            consensus_decision,
        ),
        "shadow_note": SHADOW_NOT_PRODUCTION_NOTE,
    }


def latest_raw_records_by_game(
    records: Sequence[Mapping[str, Any]],
    *,
    run_date: str | None = None,
) -> dict[Any, dict[str, Any]]:
    latest: dict[Any, dict[str, Any]] = {}
    for record in records:
        if run_date is not None and str(record.get("run_date")) != str(run_date):
            continue
        game_pk = record.get("game_pk")
        if game_pk is None:
            continue
        current = latest.get(game_pk)
        if current is None or _record_is_newer(record, current):
            latest[game_pk] = dict(record)
    return latest


def enrich_board_rows_with_shadow(
    board_rows: Sequence[Mapping[str, Any]],
    *,
    raw_records_by_game: Mapping[Any, Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    raw_records_by_game = raw_records_by_game or {}
    return [
        enrich_board_row_with_shadow(
            row,
            raw_records_by_game.get(row.get("game_pk")),
        )
        for row in board_rows
    ]


def _record_is_newer(candidate: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    candidate_ts = _parse_datetime(candidate.get("prediction_timestamp"))
    current_ts = _parse_datetime(current.get("prediction_timestamp"))
    if isinstance(candidate_ts, datetime) and isinstance(current_ts, datetime):
        return candidate_ts >= current_ts
    return candidate_ts is not None


def _average_odds_for_plays(
    rows: Sequence[Mapping[str, Any]],
    policy: PolicySpec,
) -> float | None:
    odds_values: list[int] = []
    for row in rows:
        decision = evaluate_policy(row, policy)
        if not decision.play or decision.bet_side is None:
            continue
        american = _bet_american(row, decision.bet_side)
        if isinstance(american, int) and not isinstance(american, bool):
            odds_values.append(american)
    if not odds_values:
        return None
    return sum(odds_values) / len(odds_values)


def raw_model_favorite_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wins = losses = pending = 0
    profits: list[float] = []
    odds_values: list[int] = []
    n_play = 0

    for row in rows:
        side = raw_model_side(row)
        if side is None:
            continue
        american = _bet_american(row, side)
        if american is None:
            pending += 1
            continue
        n_play += 1
        odds_values.append(american)
        won = _bet_won(row, side)
        if won is None:
            pending += 1
            continue
        profit = flat_stake_profit(won=won, american=american)
        if profit is None:
            pending += 1
            continue
        if won:
            wins += 1
        else:
            losses += 1
        profits.append(profit)

    finished = wins + losses
    return {
        "policy": "raw_model_favorite",
        "family": "diagnostic",
        "n_candidates": len(rows),
        "n_play": n_play,
        "n_resolved": finished,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": wins / finished if finished else None,
        "roi": sum(profits) / len(profits) if profits else None,
        "units": float(sum(profits)) if profits else None,
        "average_odds": sum(odds_values) / len(odds_values) if odds_values else None,
    }


def _sample_period(rows: Sequence[Mapping[str, Any]]) -> str | None:
    run_dates = sorted({str(row.get("run_date")) for row in rows if row.get("run_date") is not None})
    if not run_dates:
        return None
    if len(run_dates) == 1:
        return run_dates[0]
    return f"{run_dates[0]} to {run_dates[-1]}"


def _metrics_display_row(key: str, metrics: Mapping[str, Any]) -> dict[str, Any]:
    n_play = metrics.get("n_play") or 0
    n_resolved = metrics.get("n_resolved") or 0
    win_rate = metrics.get("win_rate")
    roi = metrics.get("roi")
    units = metrics.get("units")
    avg_odds = metrics.get("average_odds")
    pending = metrics.get("pending") or 0
    return {
        "Strategy": POLICY_DISPLAY_LABELS.get(key, key),
        "N (plays)": n_play,
        "Resolved": n_resolved,
        "Win rate": "—" if win_rate is None else f"{win_rate:.1%}",
        "ROI (flat 1u)": "—" if roi is None else f"{roi:.1%}",
        "Units": "—" if units is None else round(float(units), 2),
        "Avg odds": "—" if avg_odds is None else f"{avg_odds:.0f}",
        "Pending": pending,
    }


def build_play_policy_comparison(
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolved latest-per-game comparison aligned with MARKET-006 population semantics."""
    population, meta = load_canonical_population(daily_records, journal_records)
    sample_period = _sample_period(population)

    strategies: dict[str, dict[str, Any]] = {
        "baseline": policy_play_metrics(population, BASELINE_POLICY),
        "no_crossover": policy_play_metrics(population, EXPLORATORY_NO_CROSSOVER_POLICY),
        "consensus": policy_play_metrics(population, EXPLORATORY_CONSENSUS_POLICY),
        "raw_model_favorite": raw_model_favorite_metrics(population),
    }
    for key, policy in (
        ("baseline", BASELINE_POLICY),
        ("no_crossover", EXPLORATORY_NO_CROSSOVER_POLICY),
        ("consensus", EXPLORATORY_CONSENSUS_POLICY),
    ):
        strategies[key]["average_odds"] = _average_odds_for_plays(population, policy)

    return {
        "note": SHADOW_NOT_PRODUCTION_NOTE,
        "population_note": (
            "Resolved latest pregame snapshot per game_pk "
            f"(n={meta.get('n_resolved_latest_per_game', 0)})."
        ),
        "sample_period": sample_period,
        "baseline_edge_threshold": DEFAULT_EDGE_THRESHOLD,
        "strategies": strategies,
        "display_rows": [_metrics_display_row(key, metrics) for key, metrics in strategies.items()],
        "meta": meta,
    }


def build_play_policy_comparison_from_paths(
    predictions_path: Any,
    journal_path: Any,
) -> dict[str, Any]:
    return build_play_policy_comparison(
        read_jsonl(predictions_path),
        read_jsonl(journal_path),
    )
