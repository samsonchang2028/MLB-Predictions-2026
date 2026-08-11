"""APP-001 - data loading/shaping for the Streamlit daily prediction board.

Reads today's IMMUTABLE PIPE-001 prediction records from any prediction store
exposing ``.records()`` (``InMemoryPredictionStore`` / ``JsonLinesPredictionStore``,
see :mod:`pipelines.daily`) and shapes them into simple display rows for the
Streamlit page in ``app.daily_board_page``.

No probability/edge math happens here. ``model_probability``, ``market_probability``,
and ``edge`` are read verbatim from the PIPE-001 record -- MARKET-001
(``src/market/``) already computed them; this module only selects, labels, and
sorts fields for display.

Pass/play threshold
--------------------
Neither ``src/market/`` nor ``src/pipelines/daily.py`` defines a pass/play
betting threshold -- MARKET-001 stops at edge/EV, and no staking-policy ADR
exists yet. ``DEFAULT_EDGE_THRESHOLD`` is therefore a SYNTHETIC, DISPLAY-ONLY
default (not a business decision, not model output): the board marks a game
"PLAY" when ``abs(edge) >= edge_threshold``, else "PASS". Treat this as a
placeholder UI label, not a recommendation; promote it to a real policy via an
ADR before it is used for anything beyond a dashboard indicator.
"""

from __future__ import annotations

from typing import Any

# ponytail: MLB's 30 team_ids are a fixed, unchanging set with no existing
# id->name lookup anywhere in the repo (checked ingestion/storage/features).
# A static map is the whole solution; promote to a real teams table only if
# something beyond display ever needs it.
TEAM_ABBREVIATIONS: dict[int, str] = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC", 119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD", 136: "SEA", 137: "SF", 138: "STL",
    139: "TB", 140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

DEFAULT_EDGE_THRESHOLD = 0.02


def load_daily_board(
    store: Any,
    *,
    run_date: Any = None,
    edge_threshold: float = DEFAULT_EDGE_THRESHOLD,
) -> list[dict[str, Any]]:
    """Shape PIPE-001 prediction records into display rows, sorted by game_pk.

    ``store`` is anything exposing ``.records()`` -> a sequence of prediction
    record mappings (PIPE-001's schema). ``run_date`` filters to one day's
    slate when given; ``None`` returns every record in the store. All
    probability/market/edge values are passed through unchanged.
    """
    rows: list[dict[str, Any]] = []
    for record in store.records():
        if run_date is not None and record.get("run_date") != run_date:
            continue
        edge = record["edge"]
        rows.append(
            {
                "game_pk": record["game_pk"],
                "matchup": _matchup(record.get("away_team_id"), record.get("home_team_id")),
                "model_probability": record["model_probability"],
                "market_probability": record["market_probability"],
                "edge": edge,
                "odds_snapshot_timestamp": record["odds_snapshot_timestamp"],
                "model_version": record["model_version"],
                "play": abs(edge) >= edge_threshold,
            }
        )
    rows.sort(key=lambda r: r["game_pk"])
    return rows


def _team_label(team_id: Any) -> str:
    if team_id in TEAM_ABBREVIATIONS:
        return TEAM_ABBREVIATIONS[team_id]
    return f"Team {team_id}"


def _matchup(away_team_id: Any, home_team_id: Any) -> str:
    return f"{_team_label(away_team_id)} @ {_team_label(home_team_id)}"
