"""Unit tests for market.policy_evaluation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.board import DEFAULT_EDGE_THRESHOLD
from market.play_policy import policy_by_name
from market.policy_evaluation import (
    baseline_metrics,
    bootstrap_roi_ci,
    load_canonical_population,
    policy_play_metrics,
    sort_chronologically,
    walk_forward_splits,
    wilson_interval,
)


def _prediction(
    game_pk: int,
    *,
    edge: float,
    model_probability: float | None = None,
    market_probability: float | None = None,
    offset_hours: int = 0,
    home_american: int = -110,
    away_american: int = 100,
) -> dict:
    if model_probability is None:
        model_probability = 0.5 + edge / 2
    if market_probability is None:
        market_probability = model_probability - edge
    base = datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc) + timedelta(hours=offset_hours)
    return {
        "game_pk": game_pk,
        "run_date": "2026-08-01",
        "model_probability": model_probability,
        "market_probability": market_probability,
        "edge": edge,
        "home_american": home_american,
        "away_american": away_american,
        "odds_snapshot_timestamp": (base - timedelta(minutes=30)).isoformat(),
        "prediction_timestamp": base.isoformat(),
        "game_start_timestamp": (base + timedelta(hours=2)).isoformat(),
        "model_version": "test-model",
        "build_id": "test-build",
    }


def _journal(prediction: dict, *, actual_home_win: bool) -> dict:
    picked_home = float(prediction["edge"]) >= 0.0
    return {
        "game_pk": prediction["game_pk"],
        "prediction_timestamp": prediction["prediction_timestamp"],
        "model_version": prediction["model_version"],
        "enrichment_timestamp": "2026-08-02T12:00:00+00:00",
        "actual_home_win": actual_home_win,
        "predicted_home_win": picked_home,
        "correct": picked_home == actual_home_win,
    }


def test_load_canonical_population_dedups_latest_per_game() -> None:
    older = _prediction(100, edge=0.03, offset_hours=0)
    newer = _prediction(100, edge=0.05, offset_hours=24)
    other = _prediction(101, edge=0.04, offset_hours=48)
    daily = [older, newer, other]
    journal = [
        _journal(older, actual_home_win=True),
        _journal(newer, actual_home_win=False),
        _journal(other, actual_home_win=True),
    ]
    rows, meta = load_canonical_population(daily, journal)
    assert meta["n_resolved_latest_per_game"] == 2
    by_pk = {row["game_pk"]: row for row in rows}
    assert by_pk[100]["edge"] == pytest.approx(0.05)


def test_pending_games_excluded() -> None:
    pred = _prediction(200, edge=0.05)
    rows, meta = load_canonical_population([pred], [])
    assert rows == []
    assert meta["n_resolved_latest_per_game"] == 0


def test_flat_stake_roi_american_favorite_and_underdog() -> None:
    policy = policy_by_name("baseline")
    favorite_win = _prediction(1, edge=0.05, home_american=-200, away_american=170)
    favorite_loss = _prediction(2, edge=0.05, home_american=-200, away_american=170)
    underdog_win = _prediction(3, edge=-0.05, model_probability=0.45, market_probability=0.50, away_american=150)
    journal = [
        _journal(favorite_win, actual_home_win=True),
        _journal(favorite_loss, actual_home_win=False),
        _journal(underdog_win, actual_home_win=False),
    ]
    rows, _ = load_canonical_population([favorite_win, favorite_loss, underdog_win], journal)
    metrics = policy_play_metrics(rows, policy)
    assert metrics["n_resolved"] == 3
    assert metrics["roi"] == pytest.approx((0.5 - 1.0 + 1.5) / 3)


def test_walk_forward_is_chronological_without_overlap() -> None:
    preds = [_prediction(i, edge=0.03, offset_hours=i * 24) for i in range(10)]
    journal = [_journal(p, actual_home_win=True) for p in preds]
    rows, _ = load_canonical_population(preds, journal)
    splits = walk_forward_splits(rows)
    holdout_pks = {row["game_pk"] for row in splits["holdout"]}
    dev_pks = {row["game_pk"] for fold in splits["dev_folds"] for row in fold}
    assert holdout_pks.isdisjoint(dev_pks)
    assert len(holdout_pks) == splits["n_holdout"]


def test_baseline_reproduces_default_threshold() -> None:
    play = _prediction(1, edge=0.03)
    pass_row = _prediction(2, edge=0.01)
    journal = [
        _journal(play, actual_home_win=True),
        _journal(pass_row, actual_home_win=True),
    ]
    rows, _ = load_canonical_population([play, pass_row], journal)
    metrics = baseline_metrics(rows)
    assert metrics["n_play"] == 1
    assert metrics["edge_threshold"] == DEFAULT_EDGE_THRESHOLD


def test_empty_sample_returns_null_metrics() -> None:
    policy = policy_by_name("baseline")
    metrics = policy_play_metrics([], policy)
    assert metrics["n_play"] == 0
    assert metrics["roi"] is None


def test_wilson_and_bootstrap_are_deterministic() -> None:
    interval = wilson_interval(40, 100)
    assert interval is not None
    assert interval["lower"] < interval["upper"]
    profits = [1.0, -1.0, 0.5, -1.0]
    first = bootstrap_roi_ci(profits, seed=42)
    second = bootstrap_roi_ci(profits, seed=42)
    assert first == second


def test_sort_chronological_uses_game_start_timestamp() -> None:
    rows = [
        {"game_pk": 2, "game_start_timestamp": "2026-08-03T00:00:00+00:00"},
        {"game_pk": 1, "game_start_timestamp": "2026-08-01T00:00:00+00:00"},
    ]
    ordered = sort_chronologically(rows)
    assert [row["game_pk"] for row in ordered] == [1, 2]

def test_rank_key_uses_dev_metrics_only() -> None:
    from market.policy_evaluation import _rank_key

    holdout_worse = {
        "dev_mean_roi": 0.05,
        "dev_mean_units": 2.0,
        "dev_n_resolved": 100,
        "dev_fold_rois": [0.04, 0.06],
        "roi": -0.20,
        "units": -10.0,
        "n_resolved": 150,
        "holdout_roi": 0.30,
    }
    holdout_better = {
        "dev_mean_roi": 0.01,
        "dev_mean_units": 0.5,
        "dev_n_resolved": 90,
        "dev_fold_rois": [0.0, 0.02],
        "roi": 0.25,
        "units": 20.0,
        "n_resolved": 140,
        "holdout_roi": -0.10,
    }
    ranked = sorted([holdout_worse, holdout_better], key=_rank_key, reverse=True)
    assert ranked[0] is holdout_worse

def test_production_gates_pass_when_all_checks_met() -> None:
    from market.policy_evaluation import production_gates

    baseline_holdout = {"units": -5.0}
    candidate = {
        "dev_mean_roi": 0.05,
        "dev_fold_rois": [0.03, 0.07],
        "holdout_metrics": {
            "n_resolved": 35,
            "roi": 0.02,
            "units": 0.5,
        },
    }
    gates = production_gates(candidate, baseline_holdout=baseline_holdout)
    assert gates["passed"] is True
    assert all(gates["checks"].values())


def test_production_gates_fail_when_holdout_n_low() -> None:
    from market.policy_evaluation import production_gates

    candidate = {
        "dev_mean_roi": 0.05,
        "dev_fold_rois": [0.03, 0.07],
        "holdout_metrics": {"n_resolved": 20, "roi": 0.10, "units": 2.0},
    }
    gates = production_gates(candidate, baseline_holdout={"units": -1.0})
    assert gates["passed"] is False
    assert gates["checks"]["holdout_n_ge_30"] is False


def test_production_gates_fail_when_dev_roi_negative() -> None:
    from market.policy_evaluation import production_gates

    candidate = {
        "dev_mean_roi": -0.02,
        "dev_fold_rois": [-0.01, -0.03],
        "holdout_metrics": {"n_resolved": 40, "roi": 0.08, "units": 3.0},
    }
    gates = production_gates(candidate, baseline_holdout={"units": 0.0})
    assert gates["passed"] is False
    assert gates["checks"]["dev_mean_roi_positive"] is False


def test_select_verdict_returns_no_policy_when_all_fail() -> None:
    from market.policy_evaluation import select_verdict

    failing = {
        "policy": "failing_policy",
        "dev_mean_roi": -0.05,
        "dev_fold_rois": [-0.02, -0.08],
        "holdout_metrics": {"n_resolved": 10, "roi": -0.10, "units": -1.0},
    }
    result = select_verdict([failing], baseline_holdout={"units": 0.0})
    assert result["verdict"] == "NO POLICY READY FOR PRODUCTION"
    assert result["shadow_candidate"] is None


def test_build_overfitting_guard_exploratory_dev_holdout_differ() -> None:
    from market.policy_evaluation import build_overfitting_guard

    candidates = [
        {
            "policy": "exploratory_best",
            "family": "A",
            "roi": 0.20,
            "units": 10.0,
            "n_resolved": 100,
            "dev_mean_roi": -0.05,
            "dev_mean_units": -1.0,
            "dev_n_resolved": 80,
            "dev_fold_rois": [-0.02, -0.08],
            "holdout_roi": -0.10,
            "holdout_units": -2.0,
            "holdout_metrics": {"n_resolved": 20, "roi": -0.10, "units": -2.0},
        },
        {
            "policy": "dev_best",
            "family": "B",
            "roi": 0.05,
            "units": 2.0,
            "n_resolved": 90,
            "dev_mean_roi": 0.15,
            "dev_mean_units": 5.0,
            "dev_n_resolved": 70,
            "dev_fold_rois": [0.10, 0.20],
            "holdout_roi": -0.02,
            "holdout_units": -0.5,
            "holdout_metrics": {"n_resolved": 20, "roi": -0.02, "units": -0.5},
        },
        {
            "policy": "holdout_best",
            "family": "C",
            "roi": -0.05,
            "units": -1.0,
            "n_resolved": 85,
            "dev_mean_roi": -0.02,
            "dev_mean_units": -0.5,
            "dev_n_resolved": 65,
            "dev_fold_rois": [0.0, -0.04],
            "holdout_roi": 0.25,
            "holdout_units": 5.0,
            "holdout_metrics": {"n_resolved": 20, "roi": 0.25, "units": 5.0},
        },
    ]
    guard = build_overfitting_guard(candidates)
    assert guard["exploratory"]["best"]["policy"] == "exploratory_best"
    assert guard["cross_validated"]["best"]["policy"] == "dev_best"
    assert guard["confirmatory"]["best"]["policy"] == "holdout_best"


def test_decomposition_large_disagreement_slice() -> None:
    from market.policy_evaluation import decomposition_slices

    policy = policy_by_name("baseline")
    large = _prediction(1, edge=0.10, model_probability=0.60, market_probability=0.50)
    small = _prediction(2, edge=0.03, model_probability=0.53, market_probability=0.50)
    journal = [
        _journal(large, actual_home_win=True),
        _journal(small, actual_home_win=False),
    ]
    rows, _ = load_canonical_population([large, small], journal)
    decomp = decomposition_slices(rows, policy)
    assert "large_disagreement" in decomp
    assert decomp["large_disagreement"]["large_disagreement"]["n_play"] == 1
    assert decomp["large_disagreement"]["not_large_disagreement"]["n_play"] == 1

