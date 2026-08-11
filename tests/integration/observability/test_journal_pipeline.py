"""Integration test: PIPE-001 daily predictions -> OBS-001 journal enrichment.

Wires a real fitted logistic-regression estimator through
``run_daily_predictions`` into a persistent ``JsonLinesPredictionStore``, then
runs ``attach_results`` against a separate persistent journal store. Verifies
the two JSON-lines files are independent (enrichment never touches the
predictions file) and that model_version survives the full round trip.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from features.build import build_feature_matrix
from market import MarketLabel
from models import logistic
from observability.journal import JsonLinesJournalStore, attach_results
from pipelines.daily import JsonLinesPredictionStore, run_daily_predictions


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


PREDICTION_TS = _dt("2024-04-01T15:00:00")
FIRST_PITCH = _dt("2024-04-01T19:00:00")
SNAPSHOT_TS = _dt("2024-04-01T14:00:00")
ENRICHMENT_TS = _dt("2024-04-02T09:00:00")


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
    rng = np.random.default_rng(0)
    n, d = 200, len(training_columns)
    X = rng.normal(size=(n, d))
    y = (X[:, 0] + rng.normal(scale=0.5, size=n) > 0).astype(int)
    model = logistic.build_model(random_state=0)
    model.fit(X, y)
    return model


def test_journal_enriches_persisted_predictions_without_touching_the_predictions_file(
    tmp_path,
) -> None:
    schedule, team, starter, bullpen = _slate([1, 2])
    matrix = build_feature_matrix(
        schedule, team_features=team, starter_features=starter,
        bullpen_features=bullpen, results=[], certification=_cert(),
        completeness_mode="inference",
    )
    training_columns = list(matrix["feature_columns"])
    model = _fit_real_model(training_columns)

    predictions_path = tmp_path / "predictions.jsonl"
    prediction_store = JsonLinesPredictionStore(predictions_path)

    daily_result = run_daily_predictions(
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
        store=prediction_store,
    )
    assert len(daily_result.written) == 2
    predictions_bytes_before = predictions_path.read_bytes()

    # Game 1's home team (10) won; game 2's home team (20) lost.
    results = [
        {"game_pk": 1, "team_id": 10, "is_winner": True},
        {"game_pk": 1, "team_id": 11, "is_winner": False},
        {"game_pk": 2, "team_id": 20, "is_winner": False},
        {"game_pk": 2, "team_id": 21, "is_winner": True},
    ]

    journal_path = tmp_path / "journal.jsonl"
    journal_store = JsonLinesJournalStore(journal_path)

    journal_result = attach_results(
        predictions=prediction_store.records(),
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=journal_store,
    )

    assert len(journal_result.written) == 2
    assert {r["game_pk"] for r in journal_result.written} == {1, 2}
    for record in journal_result.written:
        assert record["model_version"] == "logistic-2024-04-01"
        assert record["actual_home_win"] == (record["game_pk"] == 1)

    # The predictions file is byte-identical -- enrichment never wrote to it.
    assert predictions_path.read_bytes() == predictions_bytes_before
    # The journal is a separate, independently persisted file.
    assert journal_path.exists()
    assert journal_path != predictions_path
    assert len(journal_store) == 2

    # Cross-process idempotency of the journal itself.
    reopened = JsonLinesJournalStore(journal_path)
    rerun = attach_results(
        predictions=prediction_store.records(),
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=reopened,
    )
    assert rerun.written == ()
    assert journal_path.read_bytes() == journal_path.read_bytes()
