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

from app.board import (
    DEFAULT_EDGE_THRESHOLD,
    _format_pacific,
    _matchup,
    _record_problem,
    _team_label,
)
from features.build import _COMPONENTS as FEATURE_COMPONENTS
from market import american_to_decimal, no_vig_two_way

FEATURE_SIDE_PREFIXES = ("home_", "away_", "diff_")
NOTABLE_STAT_GAP_COUNT = 3


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
    home_team = _team_label(home_team_id)
    away_team = _team_label(away_team_id)
    edge = record["edge"]
    model_side_key = "home" if edge >= 0 else "away"
    model_side_team = home_team if model_side_key == "home" else away_team
    play = abs(edge) >= DEFAULT_EDGE_THRESHOLD
    # Probabilities restated in the favored side's terms (P(home) flipped to
    # P(away) = 1 - P(home) when the model favors away) so the verdict reads
    # naturally: "model gives TEAM an X% chance", not always home-relative.
    model_probability_favored = (
        record["model_probability"] if model_side_key == "home" else 1 - record["model_probability"]
    )
    market_probability_favored = (
        record["market_probability"] if model_side_key == "home" else 1 - record["market_probability"]
    )

    raw_features = _load_raw_features(features_path, game_pk, run_date)
    odds_books = _load_odds_books(
        odds_books_path,
        game_pk,
        run_date,
        model_probability=record["model_probability"],
    )

    return {
        "game_pk": record["game_pk"],
        "run_date": record.get("run_date"),
        "matchup": _matchup(away_team_id, home_team_id),
        "home_team": home_team,
        "away_team": away_team,
        "game_start_pacific": _format_pacific(record.get("game_start_timestamp")),
        "model_probability": record["model_probability"],
        "market_probability": record["market_probability"],
        "edge": edge,
        "model_version": record["model_version"],
        "canonical_source": record.get("source"),
        "canonical_home_american": record.get("home_american"),
        "canonical_away_american": record.get("away_american"),
        "model_side_team": model_side_team,
        "model_probability_favored": model_probability_favored,
        "market_probability_favored": market_probability_favored,
        "play": play,
        "action_label": f"PLAY {model_side_team}" if play else "PASS",
        "edge_threshold": DEFAULT_EDGE_THRESHOLD,
        "best_price": _best_price_for_side(odds_books, model_side_key),
        "notable_stat_gaps": _notable_stat_gaps(raw_features, home_team, away_team)
        if raw_features
        else [],
        "features": _group_features(raw_features) if raw_features is not None else None,
        "odds_books": odds_books,
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


def _load_raw_features(path: Path, game_pk: Any, run_date: Any) -> dict[str, Any] | None:
    matches = _jsonl_matches(path, game_pk, run_date)
    if not matches:
        return None
    # game_features.jsonl has no prediction_timestamp (features are a
    # deterministic function of build_id, not of when the script ran); the
    # file is append-only, so the last match in file order is the latest.
    return matches[-1].get("features") or {}


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


def _notable_stat_gaps(
    features: dict[str, Any], home_team: str, away_team: str
) -> list[dict[str, Any]]:
    """The biggest home/away stat differentials, as supporting context only.

    Not a claim about *why* the model landed on its probability -- this
    codebase has no feature-importance/SHAP layer, so the model itself is a
    black box beyond its output number. This just surfaces the largest raw
    stat gaps between the two teams for a human to sanity-check the verdict
    against, ranked by ``|diff_*|`` (already computed by FEAT-004).
    """
    gaps: list[dict[str, Any]] = []
    for column, diff_value in features.items():
        if not column.startswith("diff_"):
            continue
        if isinstance(diff_value, bool) or not isinstance(diff_value, (int, float)):
            continue
        suffix = column[len("diff_"):]
        home_value = features.get(f"home_{suffix}")
        away_value = features.get(f"away_{suffix}")
        if home_value is None or away_value is None:
            continue
        gaps.append(
            {
                "label": suffix.replace("_", " ").capitalize(),
                "home_team": home_team,
                "home_value": home_value,
                "away_team": away_team,
                "away_value": away_value,
                "diff": diff_value,
            }
        )
    gaps.sort(key=lambda gap: abs(gap["diff"]), reverse=True)
    return gaps[:NOTABLE_STAT_GAP_COUNT]


def _best_price_for_side(
    odds_books: list[dict[str, Any]], side: str
) -> dict[str, Any] | None:
    """The book offering the best (highest-payout) price for ``side``."""
    field = "home_american" if side == "home" else "away_american"
    candidates = [book for book in odds_books if book.get(field) is not None]
    if not candidates:
        return None
    best = max(candidates, key=lambda book: american_to_decimal(book[field]))
    return {"bookmaker": best["bookmaker"], "price": best[field]}


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
