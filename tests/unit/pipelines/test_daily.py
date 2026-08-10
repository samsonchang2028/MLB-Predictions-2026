"""Unit tests for the PIPE-001 daily prediction pipeline.

These exercise the core with injected fakes only -- no network, no DuckDB, and a
deterministic stub estimator -- covering: the end-to-end happy path with a
complete record schema, declared-training-column-union inference (FEAT-004 P2
closure) including schema-drift rejection, the ADR-002 point-in-time guards
(first pitch + odds cutoff), OPENING-odds rejection, append-only immutability /
idempotency (identical re-write is a no-op, conflicting re-write raises), the
no-vig market probability + edge arithmetic, determinism, and deterministic
ordering by ``game_pk``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from market import MarketLabel, no_vig_two_way
from pipelines.daily import (
    InMemoryPredictionStore,
    PredictionConflictError,
    REQUIRED_RECORD_FIELDS,
    SKIP_NOT_BEFORE_FIRST_PITCH,
    SKIP_NO_ODDS_SNAPSHOT,
    SKIP_ODDS_NOT_BEFORE_CUTOFF,
    SKIP_OPENING_ODDS,
    build_inference_matrix,
    run_daily_predictions,
)


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


PREDICTION_TS = _dt("2024-04-01T15:00:00")
FIRST_PITCH = _dt("2024-04-01T19:00:00")
SNAPSHOT_TS = _dt("2024-04-01T14:00:00")


def _cert() -> dict:
    return {"status": "PASS", "dataset": {"fingerprint": "deadbeefcafef00d"}}


def _game(game_pk: int, home_id: int, away_id: int, *, first_pitch: datetime = FIRST_PITCH) -> dict:
    return {
        "game_pk": game_pk,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "game_date": _dt("2024-04-01T00:00:00"),
        "game_type": "R",
        "game_start_timestamp": first_pitch,
    }


def _team_row(game_pk: int, team_id: int, *, win_pct: float, run_diff: float) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "win_pct_before": win_pct,
        "run_diff_avg_before": run_diff,
    }


def _starter_row(game_pk: int, team_id: int, *, pitcher_id: int, era: float) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "starter_pitcher_id": pitcher_id,
        "starter_known": True,
        "season_era_before": era,
    }


def _bullpen_row(game_pk: int, team_id: int, *, ip_l7: float) -> dict:
    return {"game_pk": game_pk, "team_id": team_id, "bullpen_ip_L7": ip_l7}


def _slate(game_pks=(1, 2)):
    """Two-game synthetic slate; team ids are 10*game + {0 home, 1 away}."""
    schedule, team, starter, bullpen = [], [], [], []
    for gpk in game_pks:
        home, away = gpk * 10, gpk * 10 + 1
        schedule.append(_game(gpk, home, away))
        team += [
            _team_row(gpk, home, win_pct=0.6, run_diff=1.5),
            _team_row(gpk, away, win_pct=0.4, run_diff=-0.5),
        ]
        starter += [
            _starter_row(gpk, home, pitcher_id=home, era=3.0),
            _starter_row(gpk, away, pitcher_id=away, era=4.5),
        ]
        bullpen += [
            _bullpen_row(gpk, home, ip_l7=7.0),
            _bullpen_row(gpk, away, ip_l7=6.0),
        ]
    return schedule, team, starter, bullpen


class StubEstimator:
    """Deterministic estimator: p_home is a stable logistic of nan-summed X."""

    classes_ = np.array([0, 1])

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        s = np.nansum(np.asarray(X, dtype=float), axis=1)
        p = 1.0 / (1.0 + np.exp(-0.01 * s))
        p = np.clip(p, 0.01, 0.99)
        return np.column_stack([1.0 - p, p])


def _training_columns(schedule, team, starter, bullpen, extra=()):
    """The FEAT-004 feature columns for this slate, optionally widened."""
    from features.build import build_feature_matrix

    matrix = build_feature_matrix(
        schedule,
        team_features=team,
        starter_features=starter,
        bullpen_features=bullpen,
        results=[],
        certification=_cert(),
    )
    return list(matrix["feature_columns"]) + list(extra)


def _odds(home_american=-110, away_american=-110, *, source="book_a",
          snapshot_timestamp=SNAPSHOT_TS, label=MarketLabel.SNAPSHOT) -> dict:
    return {
        "home_american": home_american,
        "away_american": away_american,
        "source": source,
        "snapshot_timestamp": snapshot_timestamp,
        "label": label,
    }


def _run(*, schedule, team, starter, bullpen, odds, store=None,
         training_columns=None, estimator=None, refresh=None):
    store = InMemoryPredictionStore() if store is None else store
    training_columns = training_columns or _training_columns(
        schedule, team, starter, bullpen
    )
    return (
        run_daily_predictions(
            run_date="2024-04-01",
            schedule=schedule,
            team_features=team,
            starter_features=starter,
            bullpen_features=bullpen,
            certification=_cert(),
            estimator=estimator or StubEstimator(),
            model_version="stub-v1",
            training_feature_columns=training_columns,
            odds_snapshots=odds,
            prediction_timestamp=PREDICTION_TS,
            store=store,
            refresh=refresh,
        ),
        store,
    )


def test_end_to_end_happy_path_complete_records() -> None:
    schedule, team, starter, bullpen = _slate((1, 2))
    odds = {1: _odds(), 2: _odds()}
    result, store = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen, odds=odds
    )

    assert len(result.records) == 2
    assert len(result.written) == 2
    assert result.skipped == ()
    assert len(store) == 2
    for record in result.records:
        for field in REQUIRED_RECORD_FIELDS:
            assert record.get(field) is not None, field
        assert record["prediction_timestamp"] == PREDICTION_TS
        assert record["model_version"] == "stub-v1"
        # feature/schema version defaults to the certified FEAT-004 build id.
        assert record["feature_schema_version"] == result.build_id
        assert record["build_id"] == "deadbeefcafef00d"
        assert 0.0 <= record["model_probability"] <= 1.0
        assert record["odds_snapshot_timestamp"] == SNAPSHOT_TS
        assert record["source"] == "book_a"


def test_refresh_hook_invoked_before_build() -> None:
    schedule, team, starter, bullpen = _slate((1,))
    calls = []
    _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen,
        odds={1: _odds()}, refresh=lambda: calls.append("refreshed"),
    )
    assert calls == ["refreshed"]


def test_inference_uses_declared_training_columns_missing_filled_nan() -> None:
    schedule, team, starter, bullpen = _slate((1,))
    matrix_columns = _training_columns(schedule, team, starter, bullpen)
    # Declare a WIDER union than today's slate: an extra training column absent
    # from today's rows must still appear, in order, filled with NaN.
    declared = matrix_columns + ["diff_team_extra_metric_before"]

    from features.build import build_feature_matrix

    rows = build_feature_matrix(
        schedule, team_features=team, starter_features=starter,
        bullpen_features=bullpen, results=[], certification=_cert(),
    )["rows"]

    X, columns, game_pks = build_inference_matrix(rows, declared)
    assert columns == declared  # full declared set, in declared order
    assert game_pks == [1]
    extra_idx = columns.index("diff_team_extra_metric_before")
    assert np.isnan(X[0, extra_idx])  # missing training column -> NaN
    # a present training column keeps its real value
    present_idx = columns.index("home_team_win_pct_before")
    assert X[0, present_idx] == 0.6


def test_schema_drift_raises() -> None:
    schedule, team, starter, bullpen = _slate((1,))
    full_columns = _training_columns(schedule, team, starter, bullpen)
    # Drop one column that today's slate actually produces -> that observed
    # column is now absent from the declared union -> schema drift error.
    declared = [c for c in full_columns if c != "home_team_win_pct_before"]
    with pytest.raises(ValueError, match="schema drift"):
        _run(
            schedule=schedule, team=team, starter=starter, bullpen=bullpen,
            odds={1: _odds()}, training_columns=declared,
        )


def test_game_already_started_is_skipped_not_predicted() -> None:
    # first pitch equal to the prediction cutoff (and one strictly before).
    schedule, team, starter, bullpen = _slate((1, 2))
    schedule[0]["game_start_timestamp"] = PREDICTION_TS  # equal -> invalid
    schedule[1]["game_start_timestamp"] = _dt("2024-04-01T14:30:00")  # before
    odds = {1: _odds(), 2: _odds()}
    result, store = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen, odds=odds
    )
    assert result.records == ()
    assert len(store) == 0
    reasons = {s["game_pk"]: s["reason"] for s in result.skipped}
    assert reasons == {1: SKIP_NOT_BEFORE_FIRST_PITCH, 2: SKIP_NOT_BEFORE_FIRST_PITCH}


def test_odds_snapshot_not_before_cutoff_is_refused() -> None:
    schedule, team, starter, bullpen = _slate((1, 2))
    odds = {
        1: _odds(snapshot_timestamp=PREDICTION_TS),  # equal -> invalid
        2: _odds(snapshot_timestamp=_dt("2024-04-01T16:00:00")),  # after cutoff
    }
    result, store = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen, odds=odds
    )
    assert result.records == ()
    assert len(store) == 0
    assert all(s["reason"] == SKIP_ODDS_NOT_BEFORE_CUTOFF for s in result.skipped)


def test_opening_odds_rejected_as_live_input() -> None:
    schedule, team, starter, bullpen = _slate((1,))
    odds = {1: _odds(label=MarketLabel.OPENING)}
    result, store = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen, odds=odds
    )
    assert result.records == ()
    assert len(store) == 0
    assert result.skipped == ({"game_pk": 1, "reason": SKIP_OPENING_ODDS},)


def test_missing_odds_snapshot_is_skipped() -> None:
    schedule, team, starter, bullpen = _slate((1, 2))
    odds = {1: _odds()}  # game 2 has no snapshot
    result, store = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen, odds=odds
    )
    assert [r["game_pk"] for r in result.records] == [1]
    assert result.skipped == ({"game_pk": 2, "reason": SKIP_NO_ODDS_SNAPSHOT},)


def test_market_probability_is_no_vig_and_edge_is_model_minus_market() -> None:
    schedule, team, starter, bullpen = _slate((1,))
    odds = {1: _odds(home_american=-150, away_american=130)}
    result, _ = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen, odds=odds
    )
    record = result.records[0]
    expected_market = no_vig_two_way(-150, 130).no_vig_home_probability
    assert record["market_probability"] == pytest.approx(expected_market)
    assert record["edge"] == pytest.approx(
        record["model_probability"] - record["market_probability"]
    )


def test_idempotent_rerun_appends_nothing_and_does_not_mutate() -> None:
    schedule, team, starter, bullpen = _slate((1, 2))
    odds = {1: _odds(), 2: _odds()}
    store = InMemoryPredictionStore()
    columns = _training_columns(schedule, team, starter, bullpen)

    first, _ = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen,
        odds=odds, store=store, training_columns=columns,
    )
    snapshot = [dict(r) for r in store.records()]
    assert len(first.written) == 2

    second, _ = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen,
        odds=odds, store=store, training_columns=columns,
    )
    assert second.written == ()  # nothing new appended
    assert len(second.records) == 2  # still reports today's predictions
    assert len(store) == 2  # no duplicates
    assert [dict(r) for r in store.records()] == snapshot  # unchanged


def test_conflicting_rewrite_raises() -> None:
    schedule, team, starter, bullpen = _slate((1,))
    store = InMemoryPredictionStore()
    columns = _training_columns(schedule, team, starter, bullpen)
    _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen,
        odds={1: _odds()}, store=store, training_columns=columns,
    )
    # Same key (game_pk + prediction_timestamp) but different odds -> different
    # record -> conflicting re-write must raise.
    with pytest.raises(PredictionConflictError):
        _run(
            schedule=schedule, team=team, starter=starter, bullpen=bullpen,
            odds={1: _odds(home_american=-200, away_american=170)},
            store=store, training_columns=columns,
        )


def test_determinism_two_runs_identical_records() -> None:
    schedule, team, starter, bullpen = _slate((1, 2))
    odds = {1: _odds(), 2: _odds()}
    columns = _training_columns(schedule, team, starter, bullpen)
    run_a, _ = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen,
        odds=odds, store=InMemoryPredictionStore(), training_columns=columns,
    )
    run_b, _ = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen,
        odds=odds, store=InMemoryPredictionStore(), training_columns=columns,
    )
    assert run_a.records == run_b.records


def test_records_ordered_by_game_pk() -> None:
    # feed the slate out of order; records must come back sorted by game_pk.
    schedule, team, starter, bullpen = _slate((2, 1))
    odds = {1: _odds(), 2: _odds()}
    result, _ = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen, odds=odds
    )
    assert [r["game_pk"] for r in result.records] == [1, 2]


def test_estimator_loader_callable_is_supported() -> None:
    schedule, team, starter, bullpen = _slate((1,))
    loaded = {"count": 0}

    def loader():
        loaded["count"] += 1
        return StubEstimator()

    result, _ = _run(
        schedule=schedule, team=team, starter=starter, bullpen=bullpen,
        odds={1: _odds()}, estimator=loader,
    )
    assert loaded["count"] == 1
    assert len(result.records) == 1
