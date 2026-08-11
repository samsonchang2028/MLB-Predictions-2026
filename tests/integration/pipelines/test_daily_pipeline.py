"""Integration test for the PIPE-001 daily pipeline.

Wires a real fitted logistic-regression estimator (shared model contract) and a
persistent :class:`JsonLinesPredictionStore` end-to-end over a synthetic slate,
with no network and no DuckDB. Verifies a complete persisted record schema and
cross-process idempotency (re-running the same day leaves the JSON-lines file
byte-identical).
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from features.build import build_feature_matrix
from market import MarketLabel
from models import logistic
from pipelines.daily import (
    JsonLinesPredictionStore,
    REQUIRED_RECORD_FIELDS,
    run_daily_predictions,
)


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


PREDICTION_TS = _dt("2024-04-01T15:00:00")
FIRST_PITCH = _dt("2024-04-01T19:00:00")
SNAPSHOT_TS = _dt("2024-04-01T14:00:00")


def _cert() -> dict:
    return {"status": "PASS", "dataset": {"fingerprint": "abc123def456abc1"}}


def _slate(game_pks):
    schedule, team, starter, bullpen = [], [], [], []
    for gpk in game_pks:
        home, away = gpk * 10, gpk * 10 + 1
        schedule.append(
            {
                "game_pk": gpk,
                "home_team_id": home,
                "away_team_id": away,
                "game_date": _dt("2024-04-01T00:00:00"),
                "game_type": "R",
                "game_start_timestamp": FIRST_PITCH,
            }
        )
        team += [
            {"game_pk": gpk, "team_id": home, "win_pct_before": 0.58, "run_diff_avg_before": 1.2},
            {"game_pk": gpk, "team_id": away, "win_pct_before": 0.47, "run_diff_avg_before": -0.3},
        ]
        starter += [
            {"game_pk": gpk, "team_id": home, "starter_pitcher_id": home, "starter_known": True, "season_era_before": 3.2},
            {"game_pk": gpk, "team_id": away, "starter_pitcher_id": away, "starter_known": True, "season_era_before": 4.1},
        ]
        bullpen += [
            {"game_pk": gpk, "team_id": home, "bullpen_ip_L7": 6.5},
            {"game_pk": gpk, "team_id": away, "bullpen_ip_L7": 7.2},
        ]
    return schedule, team, starter, bullpen


def _odds():
    return {
        "home_american": -130,
        "away_american": 115,
        "source": "book_integration",
        "snapshot_timestamp": SNAPSHOT_TS,
        "label": MarketLabel.SNAPSHOT,
    }


def _fit_real_model(training_columns):
    """Fit the real logistic model on deterministic synthetic training data."""
    rng = np.random.default_rng(0)
    n, d = 200, len(training_columns)
    X = rng.normal(size=(n, d))
    y = (X[:, 0] + rng.normal(scale=0.5, size=n) > 0).astype(int)
    model = logistic.build_model(random_state=0)
    model.fit(X, y)
    return model


def test_daily_pipeline_end_to_end_with_real_model_and_json_store(tmp_path) -> None:
    schedule, team, starter, bullpen = _slate([2, 1])  # out of order on purpose
    matrix = build_feature_matrix(
        schedule, team_features=team, starter_features=starter,
        bullpen_features=bullpen, results=[], certification=_cert(),
        completeness_mode="inference",
    )
    training_columns = list(matrix["feature_columns"])
    model = _fit_real_model(training_columns)

    store_path = tmp_path / "predictions.jsonl"
    store = JsonLinesPredictionStore(store_path)

    result = run_daily_predictions(
        run_date="2024-04-01",
        schedule=schedule,
        team_features=team,
        starter_features=starter,
        bullpen_features=bullpen,
        certification=_cert(),
        estimator=model,
        model_version="logistic-2024-04-01",
        training_feature_columns=training_columns,
        odds_snapshots={1: _odds(), 2: _odds()},
        prediction_timestamp=PREDICTION_TS,
        store=store,
    )

    assert [r["game_pk"] for r in result.records] == [1, 2]
    assert len(result.written) == 2
    assert result.skipped == ()
    for record in result.records:
        for field in REQUIRED_RECORD_FIELDS:
            assert record.get(field) is not None, field
        assert 0.0 <= record["model_probability"] <= 1.0

    # Persisted to disk.
    assert store_path.exists()
    first_bytes = store_path.read_bytes()
    assert len(store) == 2

    # Cross-process idempotency: a fresh store over the same file re-runs and
    # appends nothing; the file is byte-identical afterwards.
    reopened = JsonLinesPredictionStore(store_path)
    rerun = run_daily_predictions(
        run_date="2024-04-01",
        schedule=schedule,
        team_features=team,
        starter_features=starter,
        bullpen_features=bullpen,
        certification=_cert(),
        estimator=model,
        model_version="logistic-2024-04-01",
        training_feature_columns=training_columns,
        odds_snapshots={1: _odds(), 2: _odds()},
        prediction_timestamp=PREDICTION_TS,
        store=reopened,
    )
    assert rerun.written == ()
    assert len(reopened) == 2
    assert store_path.read_bytes() == first_bytes  # file unchanged
