"""Assemble the game-level Gold feature matrix (FEAT-004).

Composition choice
------------------
``build_feature_matrix`` accepts the **pre-built** per-``(game_pk, team_id)``
component feature lists (team / starter / bullpen) plus ``silver.games`` and the
post-game ``results`` used only to derive the label. It does **not** re-plumb the
three builders' heterogeneous raw inputs (``team_game_statistics``,
``pitcher_appearances`` + ``games`` + ``pitcher_starters``, ``pitcher_appearances``)
through a single fat signature.

Rationale: each component builder is independently tested and point-in-time safe;
this module's single job is a deterministic *pivot* from two team rows to one game
row plus differentials. Keeping the raw-input plumbing in the callers preserves
clean module boundaries (AGENTS.md) and keeps this aggregator a pure function.

Point-in-time safety (ADR-002)
------------------------------
The three component builders already guarantee that every feature value uses only
information available before first pitch. This aggregator introduces **no** new
inputs into the feature namespace: it only reindexes existing component features by
home/away identity and subtracts comparable numeric columns. The post-game
``results`` are consumed *exclusively* to derive the ``home_win`` label, which is
returned in a separate ``target`` slot and never appears in ``feature_columns`` or
in any row's ``features`` mapping (target-isolation contract, below).

Target-isolation contract
--------------------------
``build_feature_matrix`` returns ``{build_id, certification_status,
feature_columns, rows, excluded}``. Each row is::

    {
        "game_pk": <int>,
        "game_date": <datetime>,             # pregame reference timestamp
        "prediction_timestamp": <datetime>,  # == game_date (pregame reference)
        "home_team_id": <int>,
        "away_team_id": <int>,
        "build_id": <certification fingerprint>,   # traceability stamp
        "features": {col: value, ...},       # predictive features ONLY
        "target": {"home_win": <bool | None>},
    }

The label ``home_win`` lives only under ``row["target"]`` and is guaranteed absent
from both ``feature_columns`` and every ``row["features"]``.

Certification gate (ADR-004)
----------------------------
Publication requires a PASS certification carrying a ``dataset.fingerprint`` build
id. A missing/failed certification raises and prevents matrix publication; the
fingerprint is stamped on every row for traceability to the certified 2021-2025
build.

Cardinality
-----------
- ``games`` unique on ``game_pk``.
- each component and ``results`` unique on ``(game_pk, team_id)``.
- regular-season only (``game_type == 'R'``; missing type treated as regular,
  consistent with the starter builder).
- both home and away rows must exist for every included game in every component;
  a missing side raises (no partial/one-sided game rows). See the
  component-coverage policy below for absent-coverage versus one-sided corruption.
Any violation raises ``ValueError`` (fail loudly, no silent many-to-one collapse).

Component-coverage policy (FEAT-005)
------------------------------------
A real certified build contains regular-season games for which a whole component
family has *no* source detail for *either* team. In the certified 2021-2025 build
four games have zero parsed pitcher appearances, so neither team gets starter or
bullpen features. Per component a game is in exactly one of three states:

* **both sides present** -> usable component.
* **both sides absent** -> the upstream detail is missing for the whole game
  (absent source detail, not corruption).
* **exactly one side present** -> an inconsistency that signals corruption (one
  team has detail the other lacks on the same otherwise-covered game). This
  **still raises** ``ValueError``; the existing strictness is preserved.

Chosen policy: a game with any both-sides-absent component is **excluded from
``rows`` and returned in the ``excluded`` list** carrying its ``game_pk``,
``missing_components`` and a human-readable ``reason``. This is option (b) of the
task, not silent dropping (AGENTS.md): the drop is explicit, counted, reason
tagged and testable. Emitting the game instead with ``None`` component features
was rejected because it would (1) break FEAT-004's row-completeness guarantee that
every emitted row carries both sides of every component, and (2) feed a
pitching-signal-free game into training under imputation - exactly the silent
signal dilution ADR-005 warns against. Fully covered rows are byte-for-byte
unchanged.

The four affected games in the certified build (classified per FEAT-005 STEP 1;
the classification informs *which follow-up task owns the fix* but does not change
this aggregator's coverage-driven behavior, which keys only on observable
component presence):

* ``632457`` (2021-09-16, 144 vs 115) - **legitimate exclusion**: detail payload
  fetched, but ``detailed_state='Cancelled'`` (``status_code='CI'``); no pitching
  line exists and no result (score/is_winner NULL), so it is unlabeled anyway.
* ``746577`` (2024-09-29, 114 vs 117) - **legitimate exclusion**: detail payload
  fetched, ``detailed_state='Cancelled'`` (``status_code='CR'``); no pitching line,
  no result.
* ``746596`` (2024-08-26, 114 vs 118) - **ingestion defect (DATA-016)**: a
  completed ``Final`` game with a real score (4-9), but the game-detail fetch
  ``failed`` with a ``ConnectionError`` (DNS resolution), so no
  ``bronze.mlb_game_detail_payloads`` row exists. The payload exists upstream; we
  failed to fetch it. Should be re-fetched under DATA-016, not repaired here.
* ``746597`` (2024-08-23, 114 vs 140) - **ingestion defect (DATA-016)**: a
  completed ``Final`` game (score 3-5) whose detail fetch likewise ``failed`` with
  a ``ConnectionError``; no payload row. DATA-016 scope.

Consequently 632457/746577 are legitimate permanent exclusions, while
746596/746597 are transient ingestion gaps that a DATA-016 re-fetch should
re-populate (after which they leave the ``excluded`` list automatically). This
aggregator does not distinguish the two causes because it cannot observe game
lifecycle state; it reports the observable coverage gap and lets the caller audit.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# Component keys that are join keys / metadata rather than predictive features.
_NON_FEATURE_KEYS = frozenset(
    {"game_pk", "team_id", "side", "is_home", "game_date", "season"}
)

# Numeric-looking identity columns that must never produce a differential.
_DIFF_EXCLUDED = frozenset({"starter_pitcher_id"})

# Fixed component order for deterministic column naming.
_COMPONENTS = ("team", "starter", "bullpen")


def build_feature_matrix(
    games: Sequence[Mapping[str, Any]],
    *,
    team_features: Sequence[Mapping[str, Any]],
    starter_features: Sequence[Mapping[str, Any]],
    bullpen_features: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    certification: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one Gold feature row per regular-season MLB game.

    Parameters
    ----------
    games:
        ``silver.games`` rows. Required keys: ``game_pk``, ``home_team_id``,
        ``away_team_id``, ``game_date`` (pregame reference). Optional
        ``game_type`` (only ``'R'`` kept; missing treated as regular season).
    team_features / starter_features / bullpen_features:
        Outputs of ``build_team_features`` / ``build_starter_features`` /
        ``build_bullpen_features``; each keyed by ``(game_pk, team_id)``.
    results:
        ``silver.team_game_statistics`` rows (``game_pk``, ``team_id``,
        ``score``, ``is_winner``). Used **only** to derive ``home_win``.
    certification:
        Certification artifact mapping (ADR-004). Must have ``status == 'PASS'``
        and ``dataset.fingerprint``; otherwise publication is refused.
    """
    build_id = _certified_build_id(certification)

    game_index = _index_games(games)
    component_index = {
        "team": _index_component(team_features, "team_features"),
        "starter": _index_component(starter_features, "starter_features"),
        "bullpen": _index_component(bullpen_features, "bullpen_features"),
    }
    results_index = _index_component(results, "results")

    feature_columns = _feature_schema(game_index, component_index)

    rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for game_pk in sorted(game_index):
        game = game_index[game_pk]
        home_id = game["home_team_id"]
        away_id = game["away_team_id"]

        home_side, away_side, missing_components = _game_component_values(
            game_pk, home_id, away_id, component_index
        )
        if missing_components:
            excluded.append(
                {
                    "game_pk": game_pk,
                    "game_date": game["game_date"],
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "missing_components": missing_components,
                    "reason": (
                        "incomplete component coverage: no "
                        + ", ".join(missing_components)
                        + " features for either team"
                    ),
                }
            )
            continue

        features = _assemble_features(feature_columns, home_side, away_side)

        rows.append(
            {
                "game_pk": game_pk,
                "game_date": game["game_date"],
                "prediction_timestamp": game["game_date"],
                "home_team_id": home_id,
                "away_team_id": away_id,
                "build_id": build_id,
                "features": features,
                "target": {
                    "home_win": _home_win(results_index, game_pk, home_id, away_id)
                },
            }
        )

    _assert_unique_game_rows(rows)
    return {
        "build_id": build_id,
        "certification_status": "PASS",
        "feature_columns": feature_columns,
        "rows": rows,
        "excluded": excluded,
    }


def _certified_build_id(certification: Mapping[str, Any]) -> str:
    """Return the certified build fingerprint, refusing publication otherwise."""
    if not isinstance(certification, Mapping):
        raise ValueError("certification must be provided as a mapping")
    status = certification.get("status")
    if status != "PASS":
        raise ValueError(
            f"cannot publish Gold feature matrix: certification status is "
            f"{status!r}, expected 'PASS'"
        )
    dataset = certification.get("dataset")
    fingerprint = dataset.get("fingerprint") if isinstance(dataset, Mapping) else None
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise ValueError(
            "cannot publish Gold feature matrix: certification missing "
            "dataset.fingerprint build id"
        )
    return fingerprint


def _index_games(
    games: Sequence[Mapping[str, Any]],
) -> dict[Any, dict[str, Any]]:
    required = ("game_pk", "home_team_id", "away_team_id", "game_date")
    index: dict[Any, dict[str, Any]] = {}
    for position, row in enumerate(games):
        missing = [name for name in required if name not in row]
        if missing:
            raise ValueError(f"games[{position}] missing required fields {missing}")
        game_pk = row["game_pk"]
        if game_pk in index:
            raise ValueError(f"duplicate game_pk={game_pk} in games")
        game_type = row.get("game_type")
        if game_type is not None and game_type != "R":
            continue  # regular season only, consistent with component builders
        home_id = row["home_team_id"]
        away_id = row["away_team_id"]
        if home_id == away_id:
            raise ValueError(f"game_pk={game_pk} has identical home/away team ids")
        index[game_pk] = {
            "home_team_id": home_id,
            "away_team_id": away_id,
            "game_date": row["game_date"],
        }
    return index


def _index_component(
    rows: Sequence[Mapping[str, Any]], label: str
) -> dict[tuple[Any, Any], Mapping[str, Any]]:
    """Index rows by (game_pk, team_id); reject duplicate keys (no many-to-one)."""
    index: dict[tuple[Any, Any], Mapping[str, Any]] = {}
    for position, row in enumerate(rows):
        for name in ("game_pk", "team_id"):
            if name not in row:
                raise ValueError(f"{label}[{position}] missing field {name!r}")
        key = (row["game_pk"], row["team_id"])
        if key in index:
            raise ValueError(f"duplicate (game_pk, team_id)={key} in {label}")
        index[key] = row
    return index


def _feature_keys(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Sorted predictive-feature keys shared across a component's rows."""
    keys: set[str] = set()
    for row in rows:
        keys.update(k for k in row if k not in _NON_FEATURE_KEYS)
    return sorted(keys)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _diff_eligible(
    key: str,
    component_index: Mapping[str, Mapping[tuple[Any, Any], Mapping[str, Any]]],
    component: str,
) -> bool:
    """A key yields a differential iff it is a real numeric metric.

    Excluded: identity columns (``_DIFF_EXCLUDED``), any column that is ever a
    bool, and columns with no observed numeric value. Determined from observed
    values so the schema is deterministic for a given dataset.
    """
    if key in _DIFF_EXCLUDED:
        return False
    saw_number = False
    for row in component_index[component].values():
        value = row.get(key)
        if isinstance(value, bool):
            return False
        if _is_number(value):
            saw_number = True
    return saw_number


def _feature_schema(
    game_index: Mapping[Any, Mapping[str, Any]],
    component_index: Mapping[str, Mapping[tuple[Any, Any], Mapping[str, Any]]],
) -> list[str]:
    """Deterministic, sorted feature-column names (excludes target/metadata)."""
    columns: list[str] = []
    for component in _COMPONENTS:
        keys = _feature_keys(list(component_index[component].values()))
        for key in keys:
            columns.append(f"home_{component}_{key}")
            columns.append(f"away_{component}_{key}")
            if _diff_eligible(key, component_index, component):
                columns.append(f"diff_{component}_{key}")
    return sorted(columns)


def _game_component_values(
    game_pk: Any,
    home_id: Any,
    away_id: Any,
    component_index: Mapping[str, Mapping[tuple[Any, Any], Mapping[str, Any]]],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]], list[str]]:
    """Resolve home/away component rows and classify per-component coverage.

    For each component the game is in exactly one of three states (see the
    module-level component-coverage policy):

    * **both sides present** -> added to ``home_side`` / ``away_side``.
    * **both sides absent** -> recorded in ``missing_components`` (absent source
      detail for the whole game; the caller excludes the game and reports it).
    * **exactly one side present** -> raises ``ValueError`` (a one-sided component
      on an otherwise-covered game signals corruption, not absent detail).

    Returns ``(home_side, away_side, missing_components)`` where
    ``missing_components`` is the ordered list of components absent on both sides
    (empty => fully covered).
    """
    home_side: dict[str, Mapping[str, Any]] = {}
    away_side: dict[str, Mapping[str, Any]] = {}
    missing_components: list[str] = []
    for component in _COMPONENTS:
        index = component_index[component]
        home_row = index.get((game_pk, home_id))
        away_row = index.get((game_pk, away_id))
        if home_row is None and away_row is None:
            missing_components.append(component)
            continue
        if home_row is None:
            raise ValueError(
                f"game_pk={game_pk} missing home {component} features "
                f"for team_id={home_id}"
            )
        if away_row is None:
            raise ValueError(
                f"game_pk={game_pk} missing away {component} features "
                f"for team_id={away_id}"
            )
        home_side[component] = home_row
        away_side[component] = away_row
    return home_side, away_side, missing_components


def _assemble_features(
    feature_columns: Sequence[str],
    home_side: Mapping[str, Mapping[str, Any]],
    away_side: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Fill every schema column for one game (stable keys across all rows)."""
    features: dict[str, Any] = {}
    for column in feature_columns:
        prefix, component, key = column.split("_", 2)
        if prefix == "home":
            features[column] = home_side[component].get(key)
        elif prefix == "away":
            features[column] = away_side[component].get(key)
        else:  # diff
            home_value = home_side[component].get(key)
            away_value = away_side[component].get(key)
            if _is_number(home_value) and _is_number(away_value):
                features[column] = home_value - away_value
            else:
                features[column] = None
    return features


def _home_win(
    results_index: Mapping[tuple[Any, Any], Mapping[str, Any]],
    game_pk: Any,
    home_id: Any,
    away_id: Any,
) -> bool | None:
    """Derive the label from certified post-game results (None if undecided)."""
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


def _assert_unique_game_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    seen: set[Any] = set()
    for row in rows:
        game_pk = row["game_pk"]
        if game_pk in seen:
            raise ValueError(f"feature matrix produced duplicate game_pk={game_pk}")
        seen.add(game_pk)
