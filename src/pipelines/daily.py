"""PIPE-001 - deterministic daily prediction pipeline.

One entry point, :func:`run_daily_predictions`, that turns today's regular-season
slate into **immutable** pregame prediction records. Every dependency is passed
in as a plain sequence / mapping / callable so the core is unit-testable with no
network access and no DuckDB:

- ``schedule`` / ``team_features`` / ``starter_features`` / ``bullpen_features`` /
  ``results`` are the same shapes FEAT-004 already consumes; the pipeline reuses
  :func:`features.build.build_feature_matrix` verbatim rather than re-plumbing the
  three component builders.
- ``refresh`` is an optional zero-arg callable representing the "current source
  data refresh" step. Wiring it to a real ingestion run is the *caller's* job;
  the core only invokes it once, before feature assembly.
- ``estimator`` is either an already-fitted model exposing ``predict_proba`` or a
  zero-arg loader returning one (the "model loading" step). Loading from a real
  registry / pickle is the caller's job.
- ``odds_snapshots`` maps ``game_pk`` -> a live snapshot mapping.
- ``store`` is an append-only prediction store (see :class:`InMemoryPredictionStore`
  / :class:`JsonLinesPredictionStore`).

Point-in-time correctness (ADR-002, non-negotiable)
---------------------------------------------------
For every game the pipeline requires
``odds_snapshot_timestamp < prediction_timestamp < game_start_timestamp``. A game
that cannot satisfy this (already started, no snapshot, only post-cutoff odds, or
an OPENING archive line) is **skipped with an explicit reason** and never
predicted. The market comparison delegates to :func:`market.evaluate_pregame`,
which enforces the same guard and refuses OPENING odds as a live input.

Declared-column-union inference (closes the FEAT-004 P2 finding)
---------------------------------------------------------------
The inference matrix ``X`` is built against the **declared training column union**
passed in as ``training_feature_columns`` -- never against the columns that happen
to appear in today's small slate. A training column missing from today's rows is
emitted as ``NaN`` in the declared order; a column that appears today but is absent
from the declared training union is treated as **schema drift and raises** (it is
an error, not something to silently feed the model).

The Gold completeness report runs in explicit ``inference`` mode here. A small
slate's cold-start/all-missing columns are visible as non-blocking WARNs; this
does not weaken training, because evaluation refuses anything except a strict
historical completeness PASS and today's vector still uses the trained union.

Immutability / idempotency
--------------------------
Records are keyed by ``(game_pk, prediction_timestamp)`` and writes are
append-only. Re-running an identical day appends nothing and mutates nothing
(identical re-writes are no-ops); a *conflicting* re-write for the same key raises
:class:`PredictionConflictError`.

Determinism
-----------
Identical inputs produce identical records, ordered by ``game_pk``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from features.build import build_feature_matrix
from market import MarketLabel, evaluate_pregame

# --------------------------------------------------------------------------- #
# Skip reasons (stable machine-readable codes)
# --------------------------------------------------------------------------- #
SKIP_MISSING_GAME_START = "missing_game_start_timestamp"
SKIP_NOT_BEFORE_FIRST_PITCH = "prediction_not_before_first_pitch"
SKIP_NO_ODDS_SNAPSHOT = "no_odds_snapshot"
SKIP_OPENING_ODDS = "opening_odds_not_live_input"
SKIP_ODDS_NOT_BEFORE_CUTOFF = "odds_snapshot_not_before_cutoff"
SKIP_INVALID_ODDS = "invalid_odds"

# Fields every immutable prediction record must carry (schema contract).
REQUIRED_RECORD_FIELDS: tuple[str, ...] = (
    "game_pk",
    "prediction_timestamp",
    "model_version",
    "feature_schema_version",
    "build_id",
    "model_probability",
    "odds_snapshot_timestamp",
    "market_probability",
    "edge",
    "source",
)


class PredictionConflictError(Exception):
    """Raised when a differing record is written for an existing key."""


@dataclass(frozen=True)
class DailyRunResult:
    """Outcome of one daily run.

    ``records`` are every prediction produced for today (ordered by ``game_pk``),
    whether newly appended or already present. ``written`` are only the records
    newly appended by this run. ``skipped`` are ``{"game_pk", "reason"}`` entries
    for games that could not be predicted (both ordered by ``game_pk``).
    """

    run_date: Any
    build_id: str
    records: tuple[dict[str, Any], ...]
    written: tuple[dict[str, Any], ...]
    skipped: tuple[dict[str, Any], ...]


# --------------------------------------------------------------------------- #
# Declared-column-union inference (FEAT-004 P2 closure)
# --------------------------------------------------------------------------- #
def build_inference_matrix(
    rows: Sequence[Mapping[str, Any]],
    training_feature_columns: Sequence[str],
) -> tuple[np.ndarray, list[str], list[Any]]:
    """Vectorize FEAT-004 rows against the DECLARED training column union.

    Columns are taken from ``training_feature_columns`` in the given order --
    **not** from the columns observed in ``rows``. A training column absent from a
    row becomes ``NaN``; a feature key observed in ``rows`` that is not in the
    declared union is schema drift and raises ``ValueError``.

    Returns ``(X, columns, game_pks)`` with rows ordered by ``game_pk`` so the
    probability at index ``i`` corresponds to ``game_pks[i]``.
    """
    columns = list(training_feature_columns)
    if len(set(columns)) != len(columns):
        raise ValueError("training_feature_columns contains duplicate columns")
    declared = set(columns)

    observed: set[str] = set()
    for row in rows:
        observed.update((row.get("features") or {}).keys())
    drift = sorted(observed - declared)
    if drift:
        raise ValueError(
            "schema drift: today's slate has feature column(s) absent from the "
            f"declared training union: {drift}"
        )

    ordered = sorted(rows, key=lambda r: r["game_pk"])
    game_pks = [row["game_pk"] for row in ordered]
    X = np.full((len(ordered), len(columns)), np.nan, dtype=float)
    for i, row in enumerate(ordered):
        features = row.get("features") or {}
        for j, column in enumerate(columns):
            numeric = _as_float(features.get(column))
            if numeric is not None:
                X[i, j] = numeric
    return X, columns, game_pks


# --------------------------------------------------------------------------- #
# Core entry point
# --------------------------------------------------------------------------- #
def run_daily_predictions(
    *,
    run_date: Any,
    schedule: Sequence[Mapping[str, Any]],
    team_features: Sequence[Mapping[str, Any]],
    starter_features: Sequence[Mapping[str, Any]],
    bullpen_features: Sequence[Mapping[str, Any]],
    certification: Mapping[str, Any],
    estimator: Any,
    model_version: str,
    training_feature_columns: Sequence[str],
    odds_snapshots: Mapping[Any, Mapping[str, Any]],
    prediction_timestamp: datetime,
    store: Any,
    results: Sequence[Mapping[str, Any]] = (),
    refresh: Callable[[], Any] | None = None,
    feature_schema_version: str | None = None,
) -> DailyRunResult:
    """Build today's point-in-time features and emit immutable predictions.

    Parameters mirror the required daily steps: today's ``schedule`` + probable
    starters (``starter_features``) + team/bullpen features, a ``refresh`` hook for
    the current-source-data refresh, a live ``odds_snapshots`` mapping, feature
    generation (via FEAT-004), model loading (``estimator``), probability
    prediction, market comparison, and the immutable prediction record write.

    ``results`` defaults to empty because today's games are unplayed; the FEAT-004
    label is simply ``None`` and is never used here (this is a prediction, not an
    evaluation).
    """
    if not isinstance(model_version, str) or not model_version.strip():
        raise ValueError("model_version must be a non-empty string")
    if not isinstance(prediction_timestamp, datetime):
        raise ValueError("prediction_timestamp must be a datetime")

    # Step: current source data refresh (caller wires the real ingestion).
    if refresh is not None:
        refresh()

    # Step: model loading (accept a fitted estimator or a zero-arg loader).
    model = _resolve_estimator(estimator)

    # Step: feature generation -- reuse FEAT-004 verbatim for today's slate.
    matrix = build_feature_matrix(
        schedule,
        team_features=team_features,
        starter_features=starter_features,
        bullpen_features=bullpen_features,
        results=list(results),
        certification=certification,
        completeness_mode="inference",
    )
    build_id = matrix["build_id"]
    schema_version = feature_schema_version or build_id
    rows = matrix["rows"]

    # Step: probability prediction against the DECLARED training column union.
    X, _columns, game_pks = build_inference_matrix(rows, training_feature_columns)
    if len(game_pks):
        probabilities = _positive_class_proba(model, X)
    else:
        probabilities = np.empty(0, dtype=float)
    p_by_game = {pk: float(p) for pk, p in zip(game_pks, probabilities)}

    schedule_index = _index_schedule(schedule)

    records: list[dict[str, Any]] = []
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for game_pk in game_pks:  # already sorted by game_pk (deterministic order)
        schedule_row = schedule_index[game_pk]
        game_start = schedule_row.get("game_start_timestamp")

        # Point-in-time guard 1: a valid first pitch strictly after the cutoff.
        if not isinstance(game_start, datetime):
            skipped.append({"game_pk": game_pk, "reason": SKIP_MISSING_GAME_START})
            continue
        if not prediction_timestamp < game_start:
            skipped.append(
                {"game_pk": game_pk, "reason": SKIP_NOT_BEFORE_FIRST_PITCH}
            )
            continue

        # Step: current odds snapshot.
        snapshot = odds_snapshots.get(game_pk)
        if snapshot is None:
            skipped.append({"game_pk": game_pk, "reason": SKIP_NO_ODDS_SNAPSHOT})
            continue

        label = _coerce_label(snapshot.get("label", MarketLabel.SNAPSHOT))
        if label is MarketLabel.OPENING:
            skipped.append({"game_pk": game_pk, "reason": SKIP_OPENING_ODDS})
            continue

        snapshot_ts = snapshot.get("snapshot_timestamp")
        # Point-in-time guard 2: odds must exist strictly before the cutoff.
        if not isinstance(snapshot_ts, datetime) or not snapshot_ts < prediction_timestamp:
            skipped.append(
                {"game_pk": game_pk, "reason": SKIP_ODDS_NOT_BEFORE_CUTOFF}
            )
            continue

        p_home = p_by_game[game_pk]

        # Step: market comparison (engine re-enforces the ADR-002 timestamp guard
        # and OPENING rejection; we never bypass it).
        try:
            evaluation = evaluate_pregame(
                home_american=snapshot.get("home_american"),
                away_american=snapshot.get("away_american"),
                model_probability_home=p_home,
                source=snapshot.get("source"),
                snapshot_timestamp=snapshot_ts,
                prediction_timestamp=prediction_timestamp,
                game_start_timestamp=game_start,
                label=label,
            )
        except ValueError as exc:
            skipped.append(
                {
                    "game_pk": game_pk,
                    "reason": f"{SKIP_INVALID_ODDS}: {exc}",
                }
            )
            continue

        market_probability = evaluation.market.no_vig_home_probability
        # edge is model P(home) - no-vig market P(home) (engine computes the same).
        record = {
            "game_pk": game_pk,
            "prediction_timestamp": prediction_timestamp,
            "model_version": model_version,
            "feature_schema_version": schema_version,
            "build_id": build_id,
            "model_probability": p_home,
            "odds_snapshot_timestamp": snapshot_ts,
            "market_probability": market_probability,
            "edge": p_home - market_probability,
            "source": evaluation.source,
            "home_team_id": schedule_row.get("home_team_id"),
            "away_team_id": schedule_row.get("away_team_id"),
            "game_start_timestamp": game_start,
            "home_american": evaluation.home.american,
            "away_american": evaluation.away.american,
            "run_date": run_date,
        }
        _assert_record_complete(record)

        outcome = store.append(record)
        records.append(record)
        if outcome == "appended":
            written.append(record)

    return DailyRunResult(
        run_date=run_date,
        build_id=build_id,
        records=tuple(records),
        written=tuple(written),
        skipped=tuple(skipped),
    )


# --------------------------------------------------------------------------- #
# Append-only prediction stores
# --------------------------------------------------------------------------- #
class InMemoryPredictionStore:
    """Append-only in-memory store keyed by ``(game_pk, prediction_timestamp)``.

    ``append`` returns ``"appended"`` for a new key, ``"skipped"`` for an identical
    re-write (idempotent no-op), and raises :class:`PredictionConflictError` on a
    conflicting re-write. Existing records are never mutated.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[Any, Any], dict[str, Any]] = {}
        self._order: list[tuple[Any, Any]] = []

    @staticmethod
    def _key(record: Mapping[str, Any]) -> tuple[Any, Any]:
        return (record["game_pk"], record["prediction_timestamp"])

    def existing(self, record: Mapping[str, Any]) -> dict[str, Any] | None:
        return self._records.get(self._key(record))

    def append(self, record: Mapping[str, Any]) -> str:
        key = self._key(record)
        prior = self._records.get(key)
        candidate = dict(record)
        if prior is None:
            self._records[key] = candidate
            self._order.append(key)
            return "appended"
        if prior == candidate:
            return "skipped"
        raise PredictionConflictError(
            f"conflicting re-write for key {key}: existing record differs from "
            "the new record; predictions are immutable"
        )

    def records(self) -> list[dict[str, Any]]:
        return [self._records[key] for key in self._order]

    def __len__(self) -> int:
        return len(self._order)


class JsonLinesPredictionStore:
    """Append-only JSON-lines store (one record per line).

    Same idempotency/conflict semantics as :class:`InMemoryPredictionStore`, but
    persisted to ``path``. ``datetime`` fields are serialized as ISO-8601 strings.
    Existing lines are loaded on construction so re-runs across processes stay
    idempotent.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._index: dict[tuple[Any, str], dict[str, Any]] = {}
        self._order: list[tuple[Any, str]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                serial = json.loads(line)
                key = self._key(serial)
                if key not in self._index:
                    self._order.append(key)
                self._index[key] = serial

    @staticmethod
    def _key(serial: Mapping[str, Any]) -> tuple[Any, str]:
        return (serial["game_pk"], serial["prediction_timestamp"])

    def append(self, record: Mapping[str, Any]) -> str:
        serial = _serialize_record(record)
        key = self._key(serial)
        prior = self._index.get(key)
        if prior is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(serial, sort_keys=True) + "\n")
            self._index[key] = serial
            self._order.append(key)
            return "appended"
        if prior == serial:
            return "skipped"
        raise PredictionConflictError(
            f"conflicting re-write for key {key}: existing record differs from "
            "the new record; predictions are immutable"
        )

    def records(self) -> list[dict[str, Any]]:
        return [self._index[key] for key in self._order]

    def __len__(self) -> int:
        return len(self._order)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _resolve_estimator(estimator: Any) -> Any:
    """Return a fitted estimator from a model object or a zero-arg loader."""
    if hasattr(estimator, "predict_proba"):
        return estimator
    if callable(estimator):
        loaded = estimator()
        if not hasattr(loaded, "predict_proba"):
            raise ValueError(
                "estimator loader did not return an object with predict_proba"
            )
        return loaded
    raise ValueError(
        "estimator must expose predict_proba or be a zero-arg loader returning one"
    )


def _positive_class_proba(estimator: Any, X: np.ndarray) -> np.ndarray:
    """Return ``P(home_win == 1)`` robustly (mirrors the shared model contract)."""
    proba = estimator.predict_proba(X)
    classes = list(getattr(estimator, "classes_", [0, 1]))
    if 1 not in classes:
        return np.zeros(proba.shape[0], dtype=float)
    if proba.shape[1] == 1:
        return np.full(proba.shape[0], 1.0 if classes[0] == 1 else 0.0)
    return proba[:, classes.index(1)].astype(float)


def _coerce_label(label: Any) -> MarketLabel:
    if isinstance(label, MarketLabel):
        return label
    if isinstance(label, str):
        return MarketLabel(label)
    raise ValueError(f"odds snapshot label must be a MarketLabel or str, got {label!r}")


def _index_schedule(
    schedule: Sequence[Mapping[str, Any]],
) -> dict[Any, Mapping[str, Any]]:
    index: dict[Any, Mapping[str, Any]] = {}
    for position, row in enumerate(schedule):
        if "game_pk" not in row:
            raise ValueError(f"schedule[{position}] missing game_pk")
        game_pk = row["game_pk"]
        if game_pk in index:
            raise ValueError(f"duplicate game_pk={game_pk} in schedule")
        index[game_pk] = row
    return index


def _assert_record_complete(record: Mapping[str, Any]) -> None:
    missing = [field for field in REQUIRED_RECORD_FIELDS if record.get(field) is None]
    if missing:
        raise ValueError(f"prediction record missing required fields {missing}")


def _serialize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """JSON-safe copy of a record (dates/datetimes -> ISO-8601 strings)."""
    serial: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, (datetime, date)):
            serial[key] = value.isoformat()
        else:
            serial[key] = value
    return serial


def _as_float(value: Any) -> float | None:
    """Coerce a numeric feature value to float; non-numeric -> ``None`` (NaN)."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return None
