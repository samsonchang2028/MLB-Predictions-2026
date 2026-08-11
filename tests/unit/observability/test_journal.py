"""Unit tests for the OBS-001 prediction journal (result enrichment).

Covers: append-only behavior, that original prediction fields are never
touched by enrichment, idempotent re-runs, model_version round-trip, missing
result handling (games not yet played), and conflicting-write detection
(inherited from PIPE-001's store).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from observability.journal import (
    InMemoryJournalStore,
    JournalConflictError,
    REQUIRED_ENRICHMENT_FIELDS,
    SKIP_MISSING_TEAM_IDS,
    SKIP_NO_RESULT,
    attach_results,
)


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


PREDICTION_TS = _dt("2024-04-01T15:00:00")
ENRICHMENT_TS = _dt("2024-04-02T09:00:00")


def _prediction(game_pk: int, *, home_id: int, away_id: int, p_home: float) -> dict:
    return {
        "game_pk": game_pk,
        "prediction_timestamp": PREDICTION_TS,
        "model_version": "logistic-2024-04-01",
        "build_id": "buildabc123",
        "model_probability": p_home,
        "home_team_id": home_id,
        "away_team_id": away_id,
    }


def _result(game_pk: int, team_id: int, *, is_winner: bool) -> dict:
    return {"game_pk": game_pk, "team_id": team_id, "is_winner": is_winner}


def test_attaches_result_and_marks_correct_pick() -> None:
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.7)]
    results = [_result(1, 10, is_winner=True), _result(1, 11, is_winner=False)]

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert len(outcome.written) == 1
    record = outcome.written[0]
    for field in REQUIRED_ENRICHMENT_FIELDS:
        assert record.get(field) is not None, field
    assert record["actual_home_win"] is True
    assert record["predicted_home_win"] is True
    assert record["correct"] is True
    assert record["model_version"] == "logistic-2024-04-01"


def test_incorrect_pick_marked_false() -> None:
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.3)]
    results = [_result(1, 10, is_winner=True), _result(1, 11, is_winner=False)]

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    record = outcome.written[0]
    assert record["predicted_home_win"] is False
    assert record["actual_home_win"] is True
    assert record["correct"] is False


def test_game_without_a_result_is_skipped_not_errored() -> None:
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.6)]

    outcome = attach_results(
        predictions=predictions,
        results=[],
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert outcome.written == ()
    assert outcome.skipped == ({"game_pk": 1, "reason": SKIP_NO_RESULT},)


def test_prediction_missing_team_ids_is_skipped() -> None:
    prediction = _prediction(1, home_id=10, away_id=11, p_home=0.6)
    del prediction["home_team_id"]

    outcome = attach_results(
        predictions=[prediction],
        results=[_result(1, 10, is_winner=True)],
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert outcome.written == ()
    assert outcome.skipped == ({"game_pk": 1, "reason": SKIP_MISSING_TEAM_IDS},)


def test_original_prediction_record_is_never_mutated() -> None:
    prediction = _prediction(1, home_id=10, away_id=11, p_home=0.7)
    before = copy.deepcopy(prediction)
    results = [_result(1, 10, is_winner=True), _result(1, 11, is_winner=False)]

    attach_results(
        predictions=[prediction],
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert prediction == before


def test_append_only_idempotent_rerun_writes_nothing_new() -> None:
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.7)]
    results = [_result(1, 10, is_winner=True), _result(1, 11, is_winner=False)]
    store = InMemoryJournalStore()

    first = attach_results(
        predictions=predictions, results=results,
        enrichment_timestamp=ENRICHMENT_TS, store=store,
    )
    second = attach_results(
        predictions=predictions, results=results,
        enrichment_timestamp=ENRICHMENT_TS, store=store,
    )

    assert len(first.written) == 1
    assert second.written == ()
    assert len(second.records) == 1
    assert len(store) == 1


def test_conflicting_rewrite_for_same_key_raises() -> None:
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.7)]
    store = InMemoryJournalStore()
    attach_results(
        predictions=predictions,
        results=[_result(1, 10, is_winner=True), _result(1, 11, is_winner=False)],
        enrichment_timestamp=ENRICHMENT_TS,
        store=store,
    )

    # Same (game_pk, prediction_timestamp) key, but the outcome flips -- a
    # genuinely conflicting enrichment write must raise, not silently overwrite.
    with pytest.raises(JournalConflictError):
        attach_results(
            predictions=predictions,
            results=[_result(1, 10, is_winner=False), _result(1, 11, is_winner=True)],
            enrichment_timestamp=ENRICHMENT_TS,
            store=store,
        )


def test_duplicate_result_rows_for_same_team_raise() -> None:
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.7)]
    results = [_result(1, 10, is_winner=True), _result(1, 10, is_winner=True)]

    with pytest.raises(ValueError, match="duplicate"):
        attach_results(
            predictions=predictions,
            results=results,
            enrichment_timestamp=ENRICHMENT_TS,
            store=InMemoryJournalStore(),
        )
