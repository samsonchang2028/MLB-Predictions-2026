"""OBS-001 - prediction journal: result enrichment over PIPE-001 predictions.

:func:`attach_results` reads the **immutable** prediction records PIPE-001
already writes (``pipelines.daily.InMemoryPredictionStore`` /
``JsonLinesPredictionStore``) and separately records, for each prediction whose
game has a certified result, whether the pick was correct. Enrichment records
are **new** rows in their own append-only store, joined back to the original
prediction by the same ``(game_pk, prediction_timestamp)`` identity PIPE-001
already uses -- the original prediction row is never read for writing and
never touched.

Reused, not re-derived: the append-only / idempotent / conflict-raising store
mechanics are byte-for-byte the same contract PIPE-001 already implements (same
two-field key), so this module reuses
``pipelines.daily.InMemoryPredictionStore`` / ``JsonLinesPredictionStore`` /
``PredictionConflictError`` directly instead of re-implementing them.

Scope: this is observability only. It does not compute simulated ROI or any
paper/backtest metric -- that is ``src/market``'s and the walk-forward
experiments' concern and is intentionally not touched here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pipelines.daily import (
    InMemoryPredictionStore as InMemoryJournalStore,
    JsonLinesPredictionStore as JsonLinesJournalStore,
    PredictionConflictError as JournalConflictError,
)

__all__ = [
    "InMemoryJournalStore",
    "JsonLinesJournalStore",
    "JournalConflictError",
    "JournalRunResult",
    "REQUIRED_ENRICHMENT_FIELDS",
    "SKIP_MISSING_TEAM_IDS",
    "SKIP_NO_RESULT",
    "attach_results",
]

# Skip reasons (stable machine-readable codes), mirroring PIPE-001's convention.
SKIP_MISSING_TEAM_IDS = "missing_team_ids"
SKIP_NO_RESULT = "no_result_available"

# Fields every enrichment record must carry (schema contract).
REQUIRED_ENRICHMENT_FIELDS: tuple[str, ...] = (
    "game_pk",
    "prediction_timestamp",
    "model_version",
    "enrichment_timestamp",
    "actual_home_win",
    "predicted_home_win",
    "correct",
)


@dataclass(frozen=True)
class JournalRunResult:
    """Outcome of one enrichment run, mirroring ``pipelines.daily.DailyRunResult``.

    ``records`` are every enrichment produced for the input predictions (ordered
    by ``(game_pk, prediction_timestamp)``), whether newly appended or already
    present. ``written`` are only the records newly appended by this run.
    ``skipped`` are predictions with no result available yet.
    """

    enrichment_timestamp: datetime
    records: tuple[dict[str, Any], ...]
    written: tuple[dict[str, Any], ...]
    skipped: tuple[dict[str, Any], ...]


def attach_results(
    *,
    predictions: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    enrichment_timestamp: datetime,
    store: Any,
) -> JournalRunResult:
    """Attach post-game results to already-written predictions.

    ``predictions`` are immutable PIPE-001 records (e.g. from
    ``prediction_store.records()``) -- read only, never written back to.
    ``results`` use the same shape FEAT-004/PIPE-001 already consume: rows keyed
    by ``(game_pk, team_id)`` carrying ``is_winner`` (bool) or ``score``. A
    prediction whose game has no result yet is skipped with
    ``SKIP_NO_RESULT`` (not an error -- most predictions are for games not yet
    played).
    """
    if not isinstance(enrichment_timestamp, datetime):
        raise ValueError("enrichment_timestamp must be a datetime")

    results_index = _index_results(results)
    # Snapshot of what the store already holds, for the idempotency check
    # below -- keyed the same way the store keys itself.
    existing_by_key = {_record_key(r): r for r in store.records()}

    records: list[dict[str, Any]] = []
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    ordered = sorted(
        predictions, key=lambda p: (p["game_pk"], p["prediction_timestamp"])
    )
    for prediction in ordered:
        game_pk = prediction["game_pk"]
        home_id = prediction.get("home_team_id")
        away_id = prediction.get("away_team_id")
        if home_id is None or away_id is None:
            skipped.append({"game_pk": game_pk, "reason": SKIP_MISSING_TEAM_IDS})
            continue

        actual_home_win = _derive_home_win(results_index, game_pk, home_id, away_id)
        if actual_home_win is None:
            skipped.append({"game_pk": game_pk, "reason": SKIP_NO_RESULT})
            continue

        home_result = results_index.get((game_pk, home_id))
        away_result = results_index.get((game_pk, away_id))
        predicted_home_win = prediction["model_probability"] >= 0.5
        record = {
            "game_pk": game_pk,
            "prediction_timestamp": prediction["prediction_timestamp"],
            "model_version": prediction["model_version"],
            "build_id": prediction.get("build_id"),
            "model_probability": prediction["model_probability"],
            "enrichment_timestamp": enrichment_timestamp,
            "actual_home_win": actual_home_win,
            "predicted_home_win": predicted_home_win,
            "correct": predicted_home_win == actual_home_win,
            "home_score": _score(home_result),
            "away_score": _score(away_result),
        }
        _assert_enrichment_complete(record)

        # Idempotency is about the *fact* enriched (actual/predicted/correct/
        # model_version/...), not the wall-clock moment this run happened to
        # observe it. A re-run over an already-journaled game naturally carries
        # a fresh `enrichment_timestamp`; if the substantive content is
        # unchanged that is a no-op, not a conflict. The store's own
        # whole-record equality check would otherwise treat every re-run as a
        # conflicting write, so re-observing the same fact is short-circuited
        # here before it ever reaches `store.append`. A genuine content change
        # still falls through to `store.append`, which raises as before.
        prior = existing_by_key.get(_record_key(record))
        if prior is not None and _comparable(prior) == _comparable(record):
            records.append(prior)
            continue

        outcome = store.append(record)
        records.append(record)
        if outcome == "appended":
            written.append(record)

    return JournalRunResult(
        enrichment_timestamp=enrichment_timestamp,
        records=tuple(records),
        written=tuple(written),
        skipped=tuple(skipped),
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _record_key(record: Mapping[str, Any]) -> tuple[Any, Any]:
    """Same (game_pk, prediction_timestamp) key the stores key on, normalized
    so an in-memory record (datetime) and a JSON-lines round-tripped record
    (ISO string) compare equal."""
    prediction_timestamp = record["prediction_timestamp"]
    if isinstance(prediction_timestamp, datetime):
        prediction_timestamp = prediction_timestamp.isoformat()
    return (record["game_pk"], prediction_timestamp)


def _comparable(record: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe view of a record, excluding `enrichment_timestamp`, for the
    idempotency comparison -- WHEN a fact was observed doesn't change WHAT was
    observed."""
    return {
        key: (value.isoformat() if isinstance(value, datetime) else value)
        for key, value in record.items()
        if key != "enrichment_timestamp"
    }


def _index_results(
    results: Sequence[Mapping[str, Any]],
) -> dict[tuple[Any, Any], Mapping[str, Any]]:
    """Index result rows by (game_pk, team_id); reject duplicate keys."""
    index: dict[tuple[Any, Any], Mapping[str, Any]] = {}
    for row in results:
        key = (row["game_pk"], row["team_id"])
        if key in index:
            raise ValueError(f"duplicate (game_pk, team_id)={key} in results")
        index[key] = row
    return index


def _derive_home_win(
    results_index: Mapping[tuple[Any, Any], Mapping[str, Any]],
    game_pk: Any,
    home_id: Any,
    away_id: Any,
) -> bool | None:
    # ponytail: same derivation as features.build._home_win (is_winner, else
    # score comparison); duplicated as ~10 lines rather than importing a
    # private cross-module symbol. Keep in sync if that logic changes.
    home = results_index.get((game_pk, home_id))
    if home is None:
        return None
    is_winner = home.get("is_winner")
    if isinstance(is_winner, bool):
        return is_winner
    home_score = home.get("score")
    away = results_index.get((game_pk, away_id))
    away_score = away.get("score") if away is not None else None
    if _is_number(home_score) and _is_number(away_score) and home_score != away_score:
        return home_score > away_score
    return None


def _is_number(value: Any) -> bool:
    # Matches features.build._is_number: bool is an int subclass in Python,
    # so it must be excluded explicitly.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _score(row: Mapping[str, Any] | None) -> int | float | None:
    if row is None:
        return None
    value = row.get("score")
    if _is_number(value):
        return value
    return None


def _assert_enrichment_complete(record: Mapping[str, Any]) -> None:
    missing = [
        field for field in REQUIRED_ENRICHMENT_FIELDS if record.get(field) is None
    ]
    if missing:
        raise ValueError(f"enrichment record missing required fields {missing}")
