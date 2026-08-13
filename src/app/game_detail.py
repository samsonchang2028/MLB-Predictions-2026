"""APP-005 - data loading/shaping for the Streamlit game detail page.

Reads the immutable PIPE-001 prediction record for one game plus the two
PIPE-004 detail artifacts (per-game feature breakdown, multi-book odds
comparison) and shapes them for display. The only probability math here is
:func:`market.no_vig_two_way` (MARKET-001, already the source of truth for
odds -> probability conversion) applied to comparison books; the canonical
``model_probability``/``market_probability``/``edge`` are read verbatim from
the PIPE-001 record, same convention as :mod:`app.board`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.board import _format_pacific, _matchup, _record_problem, _team_label
from features.build import _COMPONENTS as FEATURE_COMPONENTS
from market import no_vig_two_way

FEATURE_SIDE_PREFIXES = ("home_", "away_", "diff_")


def load_game_detail(
    game_pk: Any,
    run_date: Any,
    *,
    predictions_store: Any,
    features_path: Path,
    odds_books_path: Path,
) -> dict[str, Any] | None:
    """Shape one game's prediction + feature breakdown + multi-book odds.

    Returns ``None`` if no matching prediction record exists for
    ``(game_pk, run_date)``. ``features``/``odds_books`` are ``None``/empty
    when the PIPE-004 artifacts have no matching row (e.g. predictions made
    before PIPE-004 shipped) -- callers should show a fallback, not treat
    that as an error.
    """
    record = _find_prediction(predictions_store, game_pk, run_date)
    if record is None:
        return None

    home_team_id = record.get("home_team_id")
    away_team_id = record.get("away_team_id")

    return {
        "game_pk": record["game_pk"],
        "run_date": record.get("run_date"),
        "matchup": _matchup(away_team_id, home_team_id),
        "home_team": _team_label(home_team_id),
        "away_team": _team_label(away_team_id),
        "game_start_pacific": _format_pacific(record.get("game_start_timestamp")),
        "model_probability": record["model_probability"],
        "market_probability": record["market_probability"],
        "edge": record["edge"],
        "model_version": record["model_version"],
        "canonical_source": record.get("source"),
        "canonical_home_american": record.get("home_american"),
        "canonical_away_american": record.get("away_american"),
        "features": _load_features(features_path, game_pk, run_date),
        "odds_books": _load_odds_books(
            odds_books_path,
            game_pk,
            run_date,
            model_probability=record["model_probability"],
        ),
    }


def _find_prediction(store: Any, game_pk: Any, run_date: Any) -> dict[str, Any] | None:
    """Latest valid prediction record for ``(game_pk, run_date)``.

    Malformed/stale records are excluded the same way the daily board's
    ``load_daily_board_with_diagnostics`` (APP-001A) excludes them from
    display, rather than reaching this page's field access below and raising.
    """
    matches = [
        record
        for record in store.records()
        if str(record.get("game_pk")) == str(game_pk)
        and str(record.get("run_date")) == str(run_date)
        and _record_problem(record) is None
    ]
    if not matches:
        return None
    return sorted(matches, key=lambda r: str(r.get("prediction_timestamp")))[-1]


def _load_features(path: Path, game_pk: Any, run_date: Any) -> dict[str, dict[str, Any]] | None:
    matches = _jsonl_matches(path, game_pk, run_date)
    if not matches:
        return None
    # game_features.jsonl has no prediction_timestamp (features are a
    # deterministic function of build_id, not of when the script ran); the
    # file is append-only, so the last match in file order is the latest.
    return _group_features(matches[-1].get("features") or {})


def _group_features(features: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Group raw ``{home,away,diff}_{component}_{key}`` columns by component.

    Mirrors the naming convention ``features.build.build_feature_matrix`` uses
    for ``feature_columns`` -- there is no separate curated display-name list
    to read; the column name itself (side prefix retained) is the label.
    """
    grouped: dict[str, dict[str, Any]] = {component: {} for component in FEATURE_COMPONENTS}
    for column, value in features.items():
        for side_prefix in FEATURE_SIDE_PREFIXES:
            if not column.startswith(side_prefix):
                continue
            remainder = column[len(side_prefix):]
            component = next(
                (c for c in FEATURE_COMPONENTS if remainder.startswith(c + "_")),
                None,
            )
            if component is not None:
                grouped[component][column] = value
            break
    return grouped


def _load_odds_books(
    path: Path, game_pk: Any, run_date: Any, *, model_probability: float
) -> list[dict[str, Any]]:
    # odds_books.jsonl is upserted (one current row per (run_date, game_pk,
    # bookmaker) -- see append_jsonl_records' on_conflict="overwrite" writer),
    # so each bookmaker already appears at most once here.
    matches = _jsonl_matches(path, game_pk, run_date)

    books: list[dict[str, Any]] = []
    for row in matches:
        bookmaker = row.get("bookmaker")
        try:
            market = no_vig_two_way(row.get("home_american"), row.get("away_american"))
        except ValueError:
            continue
        books.append(
            {
                "bookmaker": bookmaker,
                "home_american": row.get("home_american"),
                "away_american": row.get("away_american"),
                "implied_home_probability": market.no_vig_home_probability,
                "model_vs_book_delta": model_probability - market.no_vig_home_probability,
                "snapshot_timestamp": row.get("snapshot_timestamp"),
                "snapshot_pacific": _format_pacific(row.get("snapshot_timestamp")),
            }
        )
    books.sort(key=lambda b: b["bookmaker"] or "")
    return books


def _jsonl_matches(path: Path, game_pk: Any, run_date: Any) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    matches: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if str(row.get("game_pk")) == str(game_pk) and str(row.get("run_date")) == str(run_date):
            matches.append(row)
    return matches
