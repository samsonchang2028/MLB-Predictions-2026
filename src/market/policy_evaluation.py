"""Canonical PLAY policy evaluation for MARKET-006.

Builds the resolved latest-per-game population, computes flat 1u ROI/units,
diagnostic slices, chronological walk-forward validation, and production gates.
Read-only over immutable prediction/journal artifacts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


from app.board import (
    DEFAULT_EDGE_THRESHOLD,
    _parse_datetime,
    _prediction_key,
    load_daily_board_with_diagnostics,
)
from app.dashboard_analytics import flat_stake_profit
from market.play_policy import (
    PolicySpec,
    build_candidate_grid,
    edge_selected_side,
    evaluate_policy,
    is_crossover,
    model_market_agree,
    policy_by_name,
    raw_model_side,
)

LOW_N_THRESHOLD = 30
BOOTSTRAP_SEED = 42
BOOTSTRAP_SAMPLES = 1000
HOLDOUT_FRACTION = 0.22
DEV_FOLDS = 3
LARGE_DISAGREEMENT_THRESHOLD = 0.08

EDGE_BUCKET_SPECS: tuple[tuple[str, float, float | None], ...] = (
    ("0-1pp", 0.0, 0.01),
    ("1-2pp", 0.01, 0.02),
    ("2-4pp", 0.02, 0.04),
    ("4-6pp", 0.04, 0.06),
    ("6-8pp", 0.06, 0.08),
    ("8-10pp", 0.08, 0.10),
    ("10pp+", 0.10, None),
)



class _ListStore:
    """Read-only ``.records()`` adapter over immutable JSONL rows."""

    def __init__(self, records: Sequence[Mapping[str, Any]]) -> None:
        self._records = list(records)

    def records(self) -> list[Mapping[str, Any]]:
        return self._records


def _resolved_board_rows(
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
    *,
    model_version: str | None = None,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
) -> tuple[list[dict[str, Any]], int]:
    board = load_daily_board_with_diagnostics(
        _ListStore(daily_records),
        journal_store=_ListStore(journal_records),
        edge_threshold=edge_threshold,
    )
    rows = [dict(r) for r in board["rows"] if r.get("result_status") == "Final"]
    if model_version is not None:
        rows = [r for r in rows if r.get("model_version") == model_version]
    return rows, len(board["skipped"])


def _attach_raw_prediction_fields(
    rows: Sequence[Mapping[str, Any]], daily_records: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_key = {
        _prediction_key(r.get("game_pk"), r.get("prediction_timestamp")): r
        for r in daily_records
    }
    augmented: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        raw = by_key.get(_prediction_key(enriched.get("game_pk"), enriched.get("prediction_timestamp")))
        selected_american = None
        hours_to_first_pitch = None
        if raw is not None:
            selected_american = (
                raw.get("home_american")
                if enriched.get("pick_side") == "home"
                else raw.get("away_american")
            )
            game_start = _parse_datetime(raw.get("game_start_timestamp"))
            prediction_ts = enriched.get("prediction_timestamp")
            if game_start is not None and isinstance(prediction_ts, datetime):
                hours_to_first_pitch = (game_start - prediction_ts).total_seconds() / 3600.0
            enriched["home_american"] = raw.get("home_american")
            enriched["away_american"] = raw.get("away_american")
            enriched["game_start_timestamp"] = raw.get("game_start_timestamp")
        enriched["selected_side_american"] = selected_american
        enriched["hours_to_first_pitch"] = hours_to_first_pitch
        augmented.append(enriched)
    return augmented

JOURNAL_FIELD_NOTE = (
    "journal.predicted_home_win records whether the edge-selected side won, "
    "not the raw model favorite. Analytics normalize on read; immutable history "
    "is never rewritten."
)


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def load_canonical_population(
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
    *,
    model_version: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Latest pregame snapshot per game_pk, resolved-only, with raw odds joined."""
    rows, n_malformed = _resolved_board_rows(
        daily_records,
        journal_records,
        model_version=model_version,
        edge_threshold=DEFAULT_EDGE_THRESHOLD,
    )
    rows = _attach_raw_prediction_fields(rows, daily_records)
    normalized: list[dict[str, Any]] = []
    journal_mismatches = 0
    journal_by_key = {
        _prediction_key(r.get("game_pk"), r.get("prediction_timestamp")): r
        for r in journal_records
        if isinstance(r, Mapping)
    }
    for row in rows:
        enriched = dict(row)
        enriched["raw_model_side"] = raw_model_side(enriched)
        enriched["edge_selected_side"] = edge_selected_side(enriched)
        enriched["crossover"] = is_crossover(enriched)
        enriched["model_market_agree"] = model_market_agree(enriched)
        journal = journal_by_key.get(
            _prediction_key(enriched.get("game_pk"), enriched.get("prediction_timestamp"))
        )
        if journal is not None:
            predicted_home = journal.get("predicted_home_win")
            edge_side_home = enriched.get("edge_selected_side") == "HOME"
            if isinstance(predicted_home, bool) and predicted_home != edge_side_home:
                journal_mismatches += 1
            enriched["journal_predicted_home_win"] = predicted_home
            enriched["journal_correct"] = journal.get("correct")
        normalized.append(enriched)

    meta = {
        "n_resolved_latest_per_game": len(normalized),
        "n_malformed_daily_skips": n_malformed,
        "journal_predicted_home_win_note": JOURNAL_FIELD_NOTE,
        "journal_side_mismatches": journal_mismatches,
        "model_version_filter": model_version,
    }
    return normalized, meta


def _chronological_key(row: Mapping[str, Any]) -> datetime:
    for field in ("game_start_timestamp", "prediction_timestamp"):
        parsed = row.get(field)
        if isinstance(parsed, datetime):
            return parsed
        parsed_dt = _parse_datetime(parsed)
        if parsed_dt is not None:
            return parsed_dt
    return datetime.min.replace(tzinfo=timezone.utc)


def sort_chronologically(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return sorted((dict(r) for r in rows), key=_chronological_key)


def walk_forward_splits(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sort_chronologically(rows)
    n = len(ordered)
    holdout_n = max(1, int(round(n * HOLDOUT_FRACTION))) if n else 0
    if n <= holdout_n:
        holdout = ordered
        dev = []
    else:
        holdout = ordered[-holdout_n:]
        dev = ordered[:-holdout_n]

    folds: list[list[dict[str, Any]]] = []
    if dev:
        fold_size = max(1, len(dev) // DEV_FOLDS)
        start = 0
        for idx in range(DEV_FOLDS):
            end = len(dev) if idx == DEV_FOLDS - 1 else start + fold_size
            folds.append(dev[start:end])
            start = end

    return {
        "n_total": n,
        "n_dev": len(dev),
        "n_holdout": len(holdout),
        "dev_folds": folds,
        "holdout": holdout,
        "holdout_fraction": HOLDOUT_FRACTION,
        "dev_fold_count": DEV_FOLDS,
        "small_sample_warning": n < LOW_N_THRESHOLD * 4,
    }


def _probability_disagreement(row: Mapping[str, Any]) -> float | None:
    model_p = row.get("model_probability")
    market_p = row.get("market_probability")
    if isinstance(model_p, (int, float)) and not isinstance(model_p, bool):
        if isinstance(market_p, (int, float)) and not isinstance(market_p, bool):
            return abs(float(model_p) - float(market_p))
    return None


def _is_large_disagreement(row: Mapping[str, Any]) -> bool:
    diff = _probability_disagreement(row)
    return diff is not None and diff >= LARGE_DISAGREEMENT_THRESHOLD


def _bucket_label(value: float, specs: Sequence[tuple[str, float, float | None]]) -> str:
    for label, lower, upper in specs:
        if upper is None and value >= lower:
            return label
        if upper is not None and lower <= value < upper:
            return label
    return "unknown"


def _bet_won(row: Mapping[str, Any], bet_side: str) -> bool | None:
    actual = row.get("actual_home_win")
    if not isinstance(actual, bool):
        return None
    return actual if bet_side == "HOME" else not actual


def _bet_american(row: Mapping[str, Any], bet_side: str) -> int | None:
    value = row.get("selected_side_american")
    if bet_side != row.get("edge_selected_side"):
        field = "home_american" if bet_side == "HOME" else "away_american"
        candidate = row.get(field)
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    field = "home_american" if bet_side == "HOME" else "away_american"
    candidate = row.get(field)
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        return candidate
    return None


def policy_play_metrics(
    rows: Sequence[Mapping[str, Any]],
    policy: PolicySpec,
) -> dict[str, Any]:
    wins = losses = pending = 0
    profits: list[float] = []
    play_rows: list[dict[str, Any]] = []

    for row in rows:
        decision = evaluate_policy(row, policy)
        if not decision.play or decision.bet_side is None:
            continue
        play_rows.append({**dict(row), "policy_decision": asdict(decision)})
        won = _bet_won(row, decision.bet_side)
        if won is None:
            pending += 1
            continue
        american = _bet_american(row, decision.bet_side)
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
    roi = sum(profits) / len(profits) if profits else None
    return {
        "policy": policy.name,
        "family": policy.family,
        "n_candidates": len(rows),
        "n_play": len(play_rows),
        "n_resolved": finished,
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": wins / finished if finished else None,
        "roi": roi,
        "units": float(sum(profits)) if profits else None,
        "staked_units": len(profits),
        "win_rate_ci": wilson_interval(wins, finished) if finished else None,
        "roi_bootstrap_ci": bootstrap_roi_ci(profits) if profits else None,
        "low_confidence": finished < LOW_N_THRESHOLD,
    }


def wilson_interval(wins: int, n: int, z: float = 1.96) -> dict[str, float] | None:
    if n == 0:
        return None
    p = wins / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = (z * np.sqrt((p * (1 - p) + z**2 / (4 * n)) / n)) / denom
    return {"lower": float(max(0.0, center - margin)), "upper": float(min(1.0, center + margin))}


def bootstrap_roi_ci(profits: Sequence[float], *, seed: int = BOOTSTRAP_SEED) -> dict[str, float] | None:
    if not profits:
        return None
    rng = np.random.default_rng(seed)
    arr = np.asarray(profits, dtype=float)
    n = len(arr)
    samples = []
    for _ in range(BOOTSTRAP_SAMPLES):
        draw = arr[rng.integers(0, n, size=n)]
        samples.append(float(draw.mean()))
    return {
        "lower": float(np.quantile(samples, 0.025)),
        "upper": float(np.quantile(samples, 0.975)),
        "seed": seed,
        "n_bootstrap": BOOTSTRAP_SAMPLES,
    }


def baseline_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    policy = policy_by_name("baseline")
    metrics = policy_play_metrics(rows, policy)
    metrics["edge_threshold"] = DEFAULT_EDGE_THRESHOLD
    return metrics


def decomposition_slices(rows: Sequence[Mapping[str, Any]], policy: PolicySpec) -> dict[str, Any]:
    play_rows = [row for row in rows if evaluate_policy(row, policy).play]

    def slice_metrics(group: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return policy_play_metrics(group, policy)

    crossover = {
        "crossover": slice_metrics([r for r in play_rows if r.get("crossover") is True]),
        "no_crossover": slice_metrics([r for r in play_rows if r.get("crossover") is False]),
    }
    agreement = {
        "model_market_agree": slice_metrics([r for r in play_rows if r.get("model_market_agree") is True]),
        "model_market_disagree": slice_metrics([r for r in play_rows if r.get("model_market_agree") is False]),
    }
    large_disagreement = {
        "large_disagreement": slice_metrics([r for r in play_rows if _is_large_disagreement(r)]),
        "not_large_disagreement": slice_metrics([r for r in play_rows if not _is_large_disagreement(r)]),
    }
    edge_buckets: dict[str, Any] = {}
    for row in play_rows:
        edge = abs(float(row.get("edge", 0.0)))
        label = _bucket_label(edge, EDGE_BUCKET_SPECS)
        edge_buckets.setdefault(label, []).append(row)
    edge_bucket_metrics = {label: slice_metrics(group) for label, group in edge_buckets.items()}

    return {
        "policy": policy.name,
        "crossover": crossover,
        "agreement": agreement,
        "large_disagreement": large_disagreement,
        "edge_buckets": edge_bucket_metrics,
    }


def _rank_key(metrics: Mapping[str, Any]) -> tuple[Any, ...]:
    """Rank candidates on development folds only — never full-sample or holdout."""
    dev_roi = metrics.get("dev_mean_roi")
    dev_units = metrics.get("dev_mean_units")
    dev_n = metrics.get("dev_n_resolved") or 0
    fold_rois = metrics.get("dev_fold_rois") or []
    stable = sum(1 for value in fold_rois if value is not None and value >= 0.0)
    return (
        dev_roi is not None,
        dev_roi if dev_roi is not None else float("-inf"),
        dev_units if dev_units is not None else float("-inf"),
        dev_n,
        stable,
    )


def _candidate_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    holdout = candidate.get("holdout_metrics") or {}
    return {
        "policy": candidate.get("policy"),
        "family": candidate.get("family"),
        "roi": candidate.get("roi"),
        "units": candidate.get("units"),
        "n_resolved": candidate.get("n_resolved"),
        "dev_mean_roi": candidate.get("dev_mean_roi"),
        "dev_mean_units": candidate.get("dev_mean_units"),
        "dev_n_resolved": candidate.get("dev_n_resolved"),
        "holdout_roi": candidate.get("holdout_roi"),
        "holdout_units": candidate.get("holdout_units"),
        "n_holdout": holdout.get("n_resolved"),
    }


def build_overfitting_guard(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Separate exploratory in-sample from confirmatory dev/holdout bests."""

    def _best_by(key: str) -> dict[str, Any] | None:
        scored = [
            c for c in candidates if c.get(key) is not None and not isinstance(c.get(key), bool)
        ]
        if not scored:
            return None
        winner = max(scored, key=lambda c: float(c[key]))
        return _candidate_summary(winner)

    dev_ranked = sorted(candidates, key=_rank_key, reverse=True)
    return {
        "candidates_tested": len(candidates),
        "exploratory": {
            "label": "full-sample in-sample (not used for selection)",
            "best": _best_by("roi"),
        },
        "cross_validated": {
            "label": "development-fold mean ROI (used for candidate ranking)",
            "best": _candidate_summary(dev_ranked[0]) if dev_ranked else None,
        },
        "confirmatory": {
            "label": "untouched chronological holdout block (not used for ranking)",
            "best": _best_by("holdout_roi"),
        },
        "note": (
            "Ranking and gate selection use development folds only. "
            "Full-sample and holdout bests are reported separately to avoid conflation."
        ),
    }


def evaluate_candidate(
    rows: Sequence[Mapping[str, Any]],
    policy: PolicySpec,
    *,
    splits: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    split_info = splits or walk_forward_splits(rows)
    dev_folds: list[list[dict[str, Any]]] = split_info["dev_folds"]
    holdout: list[dict[str, Any]] = split_info["holdout"]

    fold_metrics = [policy_play_metrics(fold, policy) for fold in dev_folds]
    holdout_metrics = policy_play_metrics(holdout, policy)
    full_metrics = policy_play_metrics(rows, policy)

    dev_rois = [m["roi"] for m in fold_metrics if m["n_resolved"]]
    dev_units = [m["units"] for m in fold_metrics if m["units"] is not None]
    dev_n_resolved = sum(m["n_resolved"] for m in fold_metrics)
    return {
        **full_metrics,
        "dev_fold_metrics": fold_metrics,
        "dev_fold_rois": dev_rois,
        "dev_mean_roi": float(np.mean(dev_rois)) if dev_rois else None,
        "dev_mean_units": float(np.mean(dev_units)) if dev_units else None,
        "dev_n_resolved": dev_n_resolved,
        "holdout_metrics": holdout_metrics,
        "holdout_roi": holdout_metrics.get("roi"),
        "holdout_units": holdout_metrics.get("units"),
    }


def production_gates(
    candidate: Mapping[str, Any],
    *,
    baseline_holdout: Mapping[str, Any],
) -> dict[str, Any]:
    holdout = candidate.get("holdout_metrics") or {}
    checks = {
        "holdout_n_ge_30": (holdout.get("n_resolved") or 0) >= LOW_N_THRESHOLD,
        "holdout_roi_positive": (holdout.get("roi") or float("-inf")) > 0.0,
        "dev_mean_roi_positive": (candidate.get("dev_mean_roi") or float("-inf")) > 0.0,
        "holdout_beats_baseline_units": (
            (holdout.get("units") or float("-inf")) > (baseline_holdout.get("units") or float("-inf"))
        ),
        "fold_stability": sum(
            1 for roi in (candidate.get("dev_fold_rois") or []) if roi is not None and roi >= 0.0
        )
        >= max(1, len(candidate.get("dev_fold_rois") or []) // 2),
    }
    passed = all(checks.values())
    return {"passed": passed, "checks": checks}


def select_verdict(
    ranked: Sequence[Mapping[str, Any]],
    *,
    baseline_holdout: Mapping[str, Any],
) -> dict[str, Any]:
    best = None
    for candidate in ranked:
        gates = production_gates(candidate, baseline_holdout=baseline_holdout)
        candidate_with_gates = {**candidate, "production_gates": gates}
        if gates["passed"]:
            best = candidate_with_gates
            break

    if best is None:
        return {
            "verdict": "NO POLICY READY FOR PRODUCTION",
            "shadow_candidate": None,
            "rationale": "No fixed-grid candidate passed all production gates on walk-forward holdout.",
        }

    holdout_roi = best.get("holdout_roi")
    if holdout_roi is not None and holdout_roi > 0.05:
        verdict = "POLICY CANDIDATE READY FOR ADR REVIEW"
    else:
        verdict = "POLICY CANDIDATE READY FOR PROSPECTIVE SHADOW VALIDATION"

    return {
        "verdict": verdict,
        "shadow_candidate": best.get("policy"),
        "rationale": f"Top gated candidate {best.get('policy')} with holdout ROI {holdout_roi}.",
        "candidate": best,
    }


def evaluate_all_candidates(
    rows: Sequence[Mapping[str, Any]],
    *,
    policies: Sequence[PolicySpec] | None = None,
) -> dict[str, Any]:
    grid = list(policies) if policies is not None else build_candidate_grid()
    splits = walk_forward_splits(rows)
    baseline = policy_by_name("baseline")
    baseline_eval = evaluate_candidate(rows, baseline, splits=splits)
    results = [evaluate_candidate(rows, policy, splits=splits) for policy in grid]
    ranked = sorted(results, key=_rank_key, reverse=True)
    verdict_info = select_verdict(ranked, baseline_holdout=baseline_eval["holdout_metrics"])
    overfitting_guard = build_overfitting_guard(ranked)
    return {
        "candidate_count": len(grid),
        "overfitting_guard": overfitting_guard,
        "walk_forward": {
            "design": {
                "holdout_fraction": HOLDOUT_FRACTION,
                "dev_folds": DEV_FOLDS,
                "ordering": "game_start_timestamp then prediction_timestamp",
                "small_sample_warning": splits["small_sample_warning"],
            },
            "splits": {
                "n_total": splits["n_total"],
                "n_dev": splits["n_dev"],
                "n_holdout": splits["n_holdout"],
            },
        },
        "baseline": baseline_eval,
        "baseline_decomposition": decomposition_slices(rows, baseline),
        "candidates": ranked,
        "verdict": verdict_info,
    }


def build_study_report(
    *,
    daily_records: Sequence[Mapping[str, Any]],
    journal_records: Sequence[Mapping[str, Any]],
    run_id: str,
    input_paths: Mapping[str, str],
    model_version: str | None = None,
    single_policy: str | None = None,
) -> dict[str, Any]:
    population, population_meta = load_canonical_population(
        daily_records,
        journal_records,
        model_version=model_version,
    )
    if population_meta["journal_side_mismatches"]:
        raise ValueError(
            f"journal predicted_home_win mismatches edge-selected side: "
            f"{population_meta['journal_side_mismatches']}"
        )

    artifact_hashes = {
        name: sha256_file(Path(path)) for name, path in input_paths.items()
    }

    if single_policy is not None:
        policy = policy_by_name(single_policy)
        metrics = {
            "baseline": evaluate_candidate(population, policy_by_name("baseline")),
            "requested_policy": evaluate_candidate(population, policy),
        }
        evaluation = None
    else:
        metrics = None
        evaluation = evaluate_all_candidates(population)

    return {
        "status": "MARKET_006_PLAY_POLICY_STUDY",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": dict(input_paths),
        "artifact_hashes": artifact_hashes,
        "population": population_meta,
        "journal_field_normalization": JOURNAL_FIELD_NOTE,
        "single_policy": single_policy,
        "single_policy_metrics": metrics,
        "evaluation": evaluation,
    }


def render_markdown_report(report: Mapping[str, Any]) -> str:
    lines = [
        f"# MARKET-006 PLAY Policy Study ({report.get('run_id')})",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "## Population",
        "",
        f"- Resolved latest-per-game rows: {report['population']['n_resolved_latest_per_game']}",
        f"- Malformed daily skips: {report['population']['n_malformed_daily_skips']}",
        "",
        report.get("journal_field_normalization", ""),
        "",
    ]

    evaluation = report.get("evaluation")
    if evaluation is None and report.get("single_policy_metrics"):
        baseline = report["single_policy_metrics"]["baseline"]
        lines.extend(
            [
                "## Baseline (single-policy mode)",
                "",
                f"- ROI: {baseline.get('roi')}",
                f"- Units: {baseline.get('units')}",
                f"- N resolved plays: {baseline.get('n_resolved')}",
                "",
            ]
        )
        return "\n".join(lines) + "\n"

    if evaluation is None:
        return "\n".join(lines) + "\n"

    baseline = evaluation["baseline"]
    lines.extend(
        [
            "## Baseline (abs(edge) >= 0.02)",
            "",
            f"- Full-sample ROI: {baseline.get('roi')}",
            f"- Full-sample units: {baseline.get('units')}",
            f"- Full-sample resolved plays: {baseline.get('n_resolved')}",
            f"- Holdout ROI: {baseline.get('holdout_roi')}",
            f"- Holdout units: {baseline.get('holdout_units')}",
            "",
            "## Overfitting guard",
            "",
        ]
    )
    guard = evaluation.get("overfitting_guard") or {}
    lines.append(guard.get("note", ""))
    lines.append("")
    for phase in ("exploratory", "cross_validated", "confirmatory"):
        block = guard.get(phase) or {}
        best = block.get("best")
        lines.append(f"**{block.get('label', phase)}:**")
        if best:
            lines.append(
                f"- `{best.get('policy')}` — roi={best.get('roi')}, "
                f"dev_mean_roi={best.get('dev_mean_roi')}, holdout_roi={best.get('holdout_roi')}, "
                f"n_holdout={best.get('n_holdout')}"
            )
        else:
            lines.append("- none")
        lines.append("")

    lines.extend(
        [
            "## Top candidates (ranked by dev-fold mean ROI only)",
            "",
            "| rank | policy | family | dev_mean_roi | dev_n | holdout_roi | holdout_units | n_holdout |",
            "|---:|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for idx, candidate in enumerate(evaluation["candidates"][:10], start=1):
        holdout = candidate.get("holdout_metrics") or {}
        lines.append(
            f"| {idx} | {candidate.get('policy')} | {candidate.get('family')} | "
            f"{candidate.get('dev_mean_roi')} | {candidate.get('dev_n_resolved')} | "
            f"{candidate.get('holdout_roi')} | {candidate.get('holdout_units')} | "
            f"{holdout.get('n_resolved')} |"
        )

    verdict = evaluation["verdict"]
    lines.extend(
        [
            "",
            "## Production verdict",
            "",
            f"**{verdict.get('verdict')}**",
            "",
            verdict.get("rationale", ""),
            "",
            f"Shadow candidate: {verdict.get('shadow_candidate')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
