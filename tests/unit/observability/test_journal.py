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


def _prediction(game_pk: int, *, home_id: int, away_id: int, p_home: float, edge: float | None = None) -> dict:
    record = {
        "game_pk": game_pk,
        "prediction_timestamp": PREDICTION_TS,
        "model_version": "logistic-2024-04-01",
        "build_id": "buildabc123",
        "model_probability": p_home,
        "home_team_id": home_id,
        "away_team_id": away_id,
    }
    if edge is not None:
        record["edge"] = edge
    return record


def _result(game_pk: int, team_id: int, *, is_winner: bool, score: int | None = None) -> dict:
    row = {"game_pk": game_pk, "team_id": team_id, "is_winner": is_winner}
    if score is not None:
        row["score"] = score
    return row


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


def test_attaches_final_scores_when_available() -> None:
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.7)]
    results = [
        _result(1, 10, is_winner=True, score=5),
        _result(1, 11, is_winner=False, score=3),
    ]

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert outcome.written[0]["home_score"] == 5
    assert outcome.written[0]["away_score"] == 3


def test_correctness_scores_edge_side_not_probability_threshold() -> None:
    """Regression for 2026-08-13: the displayed pick follows edge sign.

    A team can have P(home) > 0.5 while the market price makes the away side
    the value pick. Result journaling must score the same side shown in the UI.
    """
    predictions = [_prediction(823508, home_id=147, away_id=136, p_home=0.543, edge=-0.06)]
    results = [
        _result(823508, 147, is_winner=False, score=0),
        _result(823508, 136, is_winner=True, score=1),
    ]

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    record = outcome.written[0]
    assert record["predicted_home_win"] is False
    assert record["actual_home_win"] is False
    assert record["correct"] is True


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


# --------------------------------------------------------------------------- #
# OBS-001 Tester: additional adversarial coverage
# --------------------------------------------------------------------------- #
def test_probability_exactly_at_threshold_resolves_deterministically() -> None:
    """model_probability == 0.5 must resolve via the documented `>= 0.5` rule."""
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.5)]
    results = [_result(1, 10, is_winner=True), _result(1, 11, is_winner=False)]

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert outcome.written[0]["predicted_home_win"] is True


def test_result_without_a_matching_prediction_in_this_call_is_simply_ignored() -> None:
    """Out-of-order enrichment: a result row for a game whose prediction is not
    part of this call must not crash or fabricate anything -- and the
    prediction that IS present but lacks a result is cleanly skipped."""
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.6)]
    results = [_result(99, 990, is_winner=True)]  # unrelated game_pk

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert outcome.written == ()
    assert outcome.skipped == ({"game_pk": 1, "reason": SKIP_NO_RESULT},)


def test_same_game_pk_two_prediction_timestamps_get_independent_records() -> None:
    """A re-run/odds-refresh re-snapshot legitimately produces two PIPE-001
    prediction rows for the same game_pk with different prediction_timestamp.
    Each must get its own correctly-keyed journal record, not collide."""
    ts_a = PREDICTION_TS
    ts_b = _dt("2024-04-01T18:00:00")
    predictions = [
        {**_prediction(4, home_id=40, away_id=41, p_home=0.55), "prediction_timestamp": ts_a},
        {**_prediction(4, home_id=40, away_id=41, p_home=0.65), "prediction_timestamp": ts_b},
    ]
    results = [_result(4, 40, is_winner=True), _result(4, 41, is_winner=False)]

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert len(outcome.written) == 2
    timestamps = {r["prediction_timestamp"] for r in outcome.written}
    assert timestamps == {ts_a, ts_b}
    for record in outcome.written:
        assert record["correct"] is True


def test_ambiguous_result_with_tied_scores_and_no_is_winner_is_skipped() -> None:
    """A postponed/suspended-then-tied-score row with no `is_winner` reaching
    this stage must be skipped cleanly, not crash or fabricate `correct`."""
    predictions = [_prediction(1, home_id=10, away_id=11, p_home=0.6)]
    results = [
        {"game_pk": 1, "team_id": 10, "score": 3},
        {"game_pk": 1, "team_id": 11, "score": 3},
    ]

    outcome = attach_results(
        predictions=predictions,
        results=results,
        enrichment_timestamp=ENRICHMENT_TS,
        store=InMemoryJournalStore(),
    )

    assert outcome.written == ()
    assert outcome.skipped == ({"game_pk": 1, "reason": SKIP_NO_RESULT},)


def test_rerun_with_a_different_enrichment_timestamp_is_still_idempotent() -> None:
    """GAP (documents current behavior, currently FAILS): a realistic journal
    job re-runs daily with a fresh wall-clock `enrichment_timestamp` (e.g.
    `datetime.now(timezone.utc)`), re-processing predictions whose results it
    already journaled (there is no cheap "not yet journaled" query exposed).
    Because `enrichment_timestamp` is baked into the record used for the
    store's equality-based idempotency check, this second run raises
    JournalConflictError even though the actual/predicted outcome and
    correctness verdict are byte-identical -- it is not a genuine conflict,
    just a different observation time of the same fact. True idempotency for
    this journal's *content* (game_pk, prediction_timestamp, actual/predicted/
    correct) should not depend on re-passing the exact same enrichment
    timestamp every run.
    """
    predictions = [_prediction(6, home_id=60, away_id=61, p_home=0.7)]
    results = [_result(6, 60, is_winner=True), _result(6, 61, is_winner=False)]
    store = InMemoryJournalStore()

    attach_results(
        predictions=predictions, results=results,
        enrichment_timestamp=ENRICHMENT_TS, store=store,
    )
    later = _dt("2024-04-03T09:00:00")
    # Desired: re-observing the same already-correct fact on a later day is a
    # no-op, not a conflict.
    second = attach_results(
        predictions=predictions, results=results,
        enrichment_timestamp=later, store=store,
    )
    assert second.written == ()
    assert len(store) == 1


def test_derive_home_win_agrees_with_features_build_home_win() -> None:
    """GAP (documents current behavior, currently FAILS on the bool-score
    case): journal._derive_home_win is a hand-duplicated copy of
    features.build._home_win (see the `ponytail:` comment in journal.py). The
    duplication has already drifted: features.build._home_win uses
    `_is_number`, which explicitly excludes `bool` (`isinstance(x, (int,
    float)) and not isinstance(x, bool)`), whereas journal._derive_home_win
    inlines a plain `isinstance(score, (int, float))` check -- and `bool` is a
    subclass of `int` in Python, so a boolean-valued `score` field is silently
    accepted as numeric here but rejected there. Real MLB scores are plain
    ints so this is low-probability in practice, but it is a real, provable
    divergence between the two copies -- exactly the drift risk the
    duplication invites.
    """
    from features.build import _home_win
    from observability.journal import _derive_home_win

    results_index = {
        (7, 70): {"score": True},
        (7, 71): {"score": False},
    }

    assert _derive_home_win(results_index, 7, 70, 71) == _home_win(
        results_index, 7, 70, 71
    )
