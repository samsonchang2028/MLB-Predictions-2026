"""Unit tests for the game-level Gold feature matrix (FEAT-004)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from features.build import build_feature_matrix
from features.completeness import (
    _BULLPEN_BASES,
    _REST_BULLPEN_BASES,
    _REST_STARTER_BASES,
    _STARTER_BASES,
    _TEAM_BASES,
    FeatureCompletenessError,
)


def _dt(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def _cert(status: str = "PASS", fingerprint: str = "7225f7f46a5e27e9") -> dict:
    return {"status": status, "dataset": {"fingerprint": fingerprint}}


def _game(game_pk: int, home_id: int, away_id: int, date: str, game_type: str = "R") -> dict:
    return {
        "game_pk": game_pk,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "game_date": _dt(date),
        "game_type": game_type,
    }


def _team_row(game_pk: int, team_id: int, *, win_pct: float, run_diff: float) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": "home",
        "is_home": True,
        "game_date": _dt("2024-04-01T19:00:00"),
        "season": "2024",
        "win_pct_before": win_pct,
        "run_diff_avg_before": run_diff,
    }


def _starter_row(game_pk: int, team_id: int, *, pitcher_id: int, era: float | None) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": "home",
        "is_home": True,
        "game_date": _dt("2024-04-01T19:00:00"),
        "season": "2024",
        "starter_pitcher_id": pitcher_id,
        "starter_known": True,
        "season_era_before": era,
    }


def _bullpen_row(game_pk: int, team_id: int, *, ip_l7: float) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": "home",
        "is_home": True,
        "game_date": _dt("2024-04-01T19:00:00"),
        "bullpen_ip_L7": ip_l7,
    }


def _result(game_pk: int, team_id: int, *, score: int | None, is_winner: bool | None) -> dict:
    return {
        "game_pk": game_pk,
        "team_id": team_id,
        "side": "home",
        "score": score,
        "is_winner": is_winner,
    }


def _one_game_inputs():
    """Single game 1: home=10 beats away=20."""
    games = [_game(1, 10, 20, "2024-04-01T19:00:00")]
    team = [
        _team_row(1, 10, win_pct=0.6, run_diff=1.5),
        _team_row(1, 20, win_pct=0.4, run_diff=-0.5),
    ]
    starter = [
        _starter_row(1, 10, pitcher_id=100, era=3.0),
        _starter_row(1, 20, pitcher_id=200, era=4.5),
    ]
    bullpen = [
        _bullpen_row(1, 10, ip_l7=7.0),
        _bullpen_row(1, 20, ip_l7=6.0),
    ]
    results = [
        _result(1, 10, score=5, is_winner=True),
        _result(1, 20, score=3, is_winner=False),
    ]
    return games, team, starter, bullpen, results


def _build(**overrides):
    games, team, starter, bullpen, results = _one_game_inputs()
    kwargs = {
        "team_features": team,
        "starter_features": starter,
        "bullpen_features": bullpen,
        "results": results,
        "certification": _cert(),
        "completeness_mode": "inference",
    }
    kwargs.update(overrides)
    games = overrides.get("games", games)
    return build_feature_matrix(games, **{k: v for k, v in kwargs.items() if k != "games"})


def test_one_row_per_game_with_metadata() -> None:
    matrix = _build()
    assert len(matrix["rows"]) == 1
    row = matrix["rows"][0]
    assert row["game_pk"] == 1
    assert row["home_team_id"] == 10
    assert row["away_team_id"] == 20
    assert row["game_date"] == _dt("2024-04-01T19:00:00")
    assert row["prediction_timestamp"] == row["game_date"]


def test_result_exposes_gold_coverage_report() -> None:
    matrix = _build()
    assert matrix["feature_coverage"] == matrix["feature_completeness"]["columns"]
    assert set(matrix["feature_completeness"]["families"]) == {
        "team", "starter", "bullpen", "rest_schedule"
    }
    assert matrix["feature_completeness"]["mode"] == "inference"


def test_historical_mode_blocks_incomplete_declared_v1_contract() -> None:
    with pytest.raises(FeatureCompletenessError) as caught:
        _build(completeness_mode="historical")
    assert caught.value.report["status"] == "FAIL"
    assert caught.value.report["families"]["team"]["missing_required_columns"]


def test_home_away_and_diff_features_present() -> None:
    matrix = _build()
    features = matrix["rows"][0]["features"]
    # home/away component features across all three components
    assert features["home_team_win_pct_before"] == 0.6
    assert features["away_team_win_pct_before"] == 0.4
    assert features["home_starter_season_era_before"] == 3.0
    assert features["away_starter_season_era_before"] == 4.5
    # Bullpen keys already carry a ``bullpen_`` prefix; the component namespace
    # yields ``home_bullpen_bullpen_ip_L7`` (collision-safe, and avoids the
    # ``home_win`` target token that a bare ``home_win_pct_before`` would form).
    assert features["home_bullpen_bullpen_ip_L7"] == 7.0
    assert features["away_bullpen_bullpen_ip_L7"] == 6.0
    # explicit differentials = home - away
    assert features["diff_team_win_pct_before"] == pytest.approx(0.2)
    assert features["diff_team_run_diff_avg_before"] == pytest.approx(2.0)
    assert features["diff_starter_season_era_before"] == pytest.approx(-1.5)
    assert features["diff_bullpen_bullpen_ip_L7"] == pytest.approx(1.0)


def test_target_isolated_from_features() -> None:
    matrix = _build()
    row = matrix["rows"][0]
    assert row["target"] == {"home_win": True}
    # target must not leak into predictive features or the schema
    assert "home_win" not in row["features"]
    assert "home_win" not in matrix["feature_columns"]
    assert not any("home_win" in col for col in matrix["feature_columns"])
    assert all("home_win" not in key for key in row["features"])


def test_feature_columns_match_row_feature_keys() -> None:
    matrix = _build()
    columns = matrix["feature_columns"]
    for row in matrix["rows"]:
        assert sorted(row["features"]) == columns


def test_deterministic_sorted_schema() -> None:
    first = _build()["feature_columns"]
    second = _build()["feature_columns"]
    assert first == second
    assert first == sorted(first)


def test_diff_excludes_identity_and_boolean_columns() -> None:
    matrix = _build()
    columns = matrix["feature_columns"]
    # identity + boolean columns are carried per-side but never differenced
    assert "home_starter_starter_pitcher_id" in columns
    assert "away_starter_starter_pitcher_id" in columns
    assert "diff_starter_starter_pitcher_id" not in columns
    assert "home_starter_starter_known" in columns
    assert "diff_starter_starter_known" not in columns


def test_build_id_stamped_on_every_row() -> None:
    matrix = _build()
    assert matrix["build_id"] == "7225f7f46a5e27e9"
    assert matrix["certification_status"] == "PASS"
    for row in matrix["rows"]:
        assert row["build_id"] == "7225f7f46a5e27e9"


def test_missing_certification_prevents_publication() -> None:
    with pytest.raises(ValueError, match="certification status"):
        _build(certification=_cert(status="FAIL"))
    with pytest.raises(ValueError, match="certification status"):
        _build(certification={"dataset": {"fingerprint": "x"}})  # no status
    with pytest.raises(ValueError, match="dataset.fingerprint"):
        _build(certification={"status": "PASS"})  # no fingerprint
    with pytest.raises(ValueError, match="dataset.fingerprint"):
        _build(certification=_cert(fingerprint=""))


def test_duplicate_game_pk_rejected() -> None:
    games = [
        _game(1, 10, 20, "2024-04-01T19:00:00"),
        _game(1, 10, 30, "2024-04-02T19:00:00"),
    ]
    with pytest.raises(ValueError, match="duplicate game_pk"):
        _build(games=games)


def test_duplicate_component_key_rejected() -> None:
    _, team, _, _, _ = _one_game_inputs()
    team.append(_team_row(1, 10, win_pct=0.9, run_diff=9.0))  # duplicate (1, 10)
    with pytest.raises(ValueError, match="duplicate .* in team_features"):
        _build(team_features=team)


def test_missing_component_side_rejected() -> None:
    _, _, starter, _, _ = _one_game_inputs()
    starter = [row for row in starter if row["team_id"] != 20]  # drop away starter
    with pytest.raises(ValueError, match="missing away starter"):
        _build(starter_features=starter)


def test_non_regular_season_game_excluded() -> None:
    games = [
        _game(1, 10, 20, "2024-04-01T19:00:00", game_type="R"),
        _game(2, 30, 40, "2024-03-01T19:00:00", game_type="S"),
    ]
    matrix = _build(games=games)
    assert [row["game_pk"] for row in matrix["rows"]] == [1]


def test_home_win_from_score_fallback() -> None:
    results = [
        _result(1, 10, score=2, is_winner=None),  # is_winner unknown
        _result(1, 20, score=7, is_winner=None),
    ]
    matrix = _build(results=results)
    assert matrix["rows"][0]["target"]["home_win"] is False


def test_home_win_none_when_unlabeled() -> None:
    results = [
        _result(1, 10, score=None, is_winner=None),
        _result(1, 20, score=None, is_winner=None),
    ]
    matrix = _build(results=results)
    assert matrix["rows"][0]["target"]["home_win"] is None


# --- FEAT-005: component-coverage policy -------------------------------------
#
# A game whose whole component family has no source detail for *either* team is
# excluded and reported in ``matrix["excluded"]`` (option (b): no silent drop).
# A component present for one team but not the other on an otherwise-covered game
# is corruption and still raises (strictness preserved). See build.py docstring
# for the per-game classification of the four real certified-build games
# (632457/746577 legitimate Cancelled; 746596/746597 DATA-016 ingestion defects).


def _two_game_inputs():
    """Game 1 fully covered; game 2 covered too (caller drops a component)."""
    games = [
        _game(1, 10, 20, "2024-04-01T19:00:00"),
        _game(2, 30, 40, "2024-04-02T19:00:00"),
    ]
    team = [
        _team_row(1, 10, win_pct=0.6, run_diff=1.5),
        _team_row(1, 20, win_pct=0.4, run_diff=-0.5),
        _team_row(2, 30, win_pct=0.5, run_diff=0.0),
        _team_row(2, 40, win_pct=0.5, run_diff=0.0),
    ]
    starter = [
        _starter_row(1, 10, pitcher_id=100, era=3.0),
        _starter_row(1, 20, pitcher_id=200, era=4.5),
        _starter_row(2, 30, pitcher_id=300, era=2.5),
        _starter_row(2, 40, pitcher_id=400, era=5.0),
    ]
    bullpen = [
        _bullpen_row(1, 10, ip_l7=7.0),
        _bullpen_row(1, 20, ip_l7=6.0),
        _bullpen_row(2, 30, ip_l7=5.0),
        _bullpen_row(2, 40, ip_l7=4.0),
    ]
    results = [
        _result(1, 10, score=5, is_winner=True),
        _result(1, 20, score=3, is_winner=False),
        _result(2, 30, score=1, is_winner=False),
        _result(2, 40, score=2, is_winner=True),
    ]
    return games, team, starter, bullpen, results


def _build_two(*, bullpen=None, starter=None):
    games, team, s, b, results = _two_game_inputs()
    return build_feature_matrix(
        games,
        team_features=team,
        starter_features=starter if starter is not None else s,
        bullpen_features=bullpen if bullpen is not None else b,
        results=results,
        certification=_cert(),
        completeness_mode="inference",
    )


def test_result_exposes_empty_excluded_list_when_fully_covered() -> None:
    matrix = _build()
    # The policy is always visible in the result shape, even with no exclusions.
    assert matrix["excluded"] == []
    assert [row["game_pk"] for row in matrix["rows"]] == [1]


def test_game_missing_both_teams_bullpen_is_excluded_not_raised() -> None:
    # Single game with zero bullpen coverage for either team (the real 4-game
    # pattern). Must NOT raise and must NOT silently vanish.
    matrix = _build(bullpen_features=[])
    assert matrix["rows"] == []
    assert len(matrix["excluded"]) == 1
    entry = matrix["excluded"][0]
    assert entry["game_pk"] == 1
    assert entry["missing_components"] == ["bullpen"]
    assert "bullpen" in entry["reason"]
    # game identity is carried for auditing
    assert entry["home_team_id"] == 10
    assert entry["away_team_id"] == 20


def test_game_missing_both_teams_starter_and_bullpen_reports_both() -> None:
    matrix = _build(starter_features=[], bullpen_features=[])
    assert matrix["rows"] == []
    assert len(matrix["excluded"]) == 1
    # order follows the fixed component order (team, starter, bullpen)
    assert matrix["excluded"][0]["missing_components"] == ["starter", "bullpen"]


def test_covered_game_unaffected_while_other_is_excluded() -> None:
    # Game 2 loses bullpen for BOTH teams; game 1 stays fully covered.
    bullpen = [_bullpen_row(1, 10, ip_l7=7.0), _bullpen_row(1, 20, ip_l7=6.0)]
    matrix = _build_two(bullpen=bullpen)
    assert [row["game_pk"] for row in matrix["rows"]] == [1]
    # Fully covered game 1 keeps its features and target intact.
    row1 = matrix["rows"][0]
    assert row1["features"]["home_bullpen_bullpen_ip_L7"] == 7.0
    assert row1["target"] == {"home_win": True}
    # Game 2 is surfaced as excluded with a reason, not dropped silently.
    assert [e["game_pk"] for e in matrix["excluded"]] == [2]
    assert matrix["excluded"][0]["missing_components"] == ["bullpen"]


def test_one_sided_component_on_covered_game_still_raises() -> None:
    # Game 2 has bullpen for team 30 but NOT team 40: corruption, not absence.
    bullpen = [
        _bullpen_row(1, 10, ip_l7=7.0),
        _bullpen_row(1, 20, ip_l7=6.0),
        _bullpen_row(2, 30, ip_l7=5.0),
    ]
    with pytest.raises(ValueError, match="missing away bullpen"):
        _build_two(bullpen=bullpen)


def test_excluded_games_absent_from_feature_rows() -> None:
    # An excluded game must never leak into rows/targets (no partial rows).
    matrix = _build(bullpen_features=[])
    assert all(row["game_pk"] != 1 for row in matrix["rows"])
    excluded_pks = {e["game_pk"] for e in matrix["excluded"]}
    row_pks = {row["game_pk"] for row in matrix["rows"]}
    assert excluded_pks.isdisjoint(row_pks)


def test_unknown_new_game_with_no_pitcher_coverage_is_excluded_generically() -> None:
    # Tester regression check: FEAT-005's classification must NOT be a hardcoded
    # allowlist of the four known real game_pks (632457/746577/746596/746597).
    # A brand-new, never-before-seen game_pk with zero starter+bullpen coverage
    # for both teams must be handled by the same observable-coverage rule --
    # excluded and reported with a reason -- neither silently included with
    # None-valued pitching features nor silently vanished without a trace.
    games = [
        _game(1, 10, 20, "2024-04-01T19:00:00"),
        _game(919191, 55, 66, "2027-05-05T19:00:00"),  # unseen game_pk, future date
    ]
    team = [
        _team_row(1, 10, win_pct=0.6, run_diff=1.5),
        _team_row(1, 20, win_pct=0.4, run_diff=-0.5),
        _team_row(919191, 55, win_pct=0.5, run_diff=0.0),
        _team_row(919191, 66, win_pct=0.5, run_diff=0.0),
    ]
    _, _, starter, bullpen, results = _one_game_inputs()
    results.extend(
        [
            _result(919191, 55, score=None, is_winner=None),
            _result(919191, 66, score=None, is_winner=None),
        ]
    )
    matrix = build_feature_matrix(
        games,
        team_features=team,
        starter_features=starter,  # only covers game_pk=1
        bullpen_features=bullpen,  # only covers game_pk=1
        results=results,
        certification=_cert(),
        completeness_mode="inference",
    )
    assert [row["game_pk"] for row in matrix["rows"]] == [1]
    excluded = {entry["game_pk"]: entry for entry in matrix["excluded"]}
    assert 919191 in excluded
    assert excluded[919191]["missing_components"] == ["starter", "bullpen"]
    assert "starter" in excluded[919191]["reason"] and "bullpen" in excluded[919191]["reason"]


# --- FEAT-006 tester edge case: only ONE family broken end-to-end -----------
#
# Builds a matrix with the full declared V1 column contract (not the minimal
# fixtures above) so team/starter/rest are genuinely healthy and only bullpen
# is dead, then confirms the historical gate names bullpen specifically rather
# than failing generically.


def _full_team_row(game_pk: int, team_id: int, base: float) -> dict:
    row = {
        "game_pk": game_pk, "team_id": team_id, "side": "home", "is_home": True,
        "game_date": _dt("2024-04-01T19:00:00"), "season": "2024",
    }
    row.update({key: base + index * 0.01 for index, key in enumerate(_TEAM_BASES)})
    return row


def _full_starter_row(game_pk: int, team_id: int, base: float) -> dict:
    row = {
        "game_pk": game_pk, "team_id": team_id, "side": "home", "is_home": True,
        "game_date": _dt("2024-04-01T19:00:00"), "season": "2024",
    }
    for key in (*_STARTER_BASES, *_REST_STARTER_BASES):
        if key == "starter_pitcher_id":
            row[key] = team_id * 1000 + game_pk
        elif key in ("starter_known", "starter_is_probable"):
            row[key] = True
        else:
            row[key] = base + hash(key) % 7 * 0.1
    return row


def _full_bullpen_row(game_pk: int, team_id: int, base: float, *, dead: bool) -> dict:
    row = {
        "game_pk": game_pk, "team_id": team_id, "side": "home", "is_home": True,
        "game_date": _dt("2024-04-01T19:00:00"),
    }
    for key in (*_BULLPEN_BASES, *_REST_BULLPEN_BASES):
        row[key] = None if dead else base + hash(key) % 7 * 0.1
    return row


def _full_matrix_inputs(*, bullpen_dead: bool, n_games: int = 32):
    games, team, starter, bullpen, results = [], [], [], [], []
    for i in range(n_games):
        game_pk = 100 + i
        home_id, away_id = 1000 + i, 2000 + i
        games.append(_game(game_pk, home_id, away_id, "2024-04-01T19:00:00"))
        team.append(_full_team_row(game_pk, home_id, 0.3 + i * 0.011))
        team.append(_full_team_row(game_pk, away_id, 0.4 + i * 0.023))
        # Home/away bases must NOT differ by a fixed offset, or diff_ columns
        # (home - away) would be constant across every game and trip the
        # unexpected_constant check for an unrelated reason.
        starter.append(_full_starter_row(game_pk, home_id, 2.0 + i * 0.013))
        starter.append(_full_starter_row(game_pk, away_id, 3.0 + i * 0.029))
        bullpen.append(_full_bullpen_row(game_pk, home_id, 1.0 + i * 0.017, dead=bullpen_dead))
        bullpen.append(_full_bullpen_row(game_pk, away_id, 1.5 + i * 0.031, dead=bullpen_dead))
        results.append(_result(game_pk, home_id, score=5, is_winner=True))
        results.append(_result(game_pk, away_id, score=3, is_winner=False))
    return games, team, starter, bullpen, results


def test_only_bullpen_broken_gate_names_bullpen_not_generic_failure() -> None:
    games, team, starter, bullpen, results = _full_matrix_inputs(bullpen_dead=True)
    with pytest.raises(FeatureCompletenessError) as caught:
        build_feature_matrix(
            games, team_features=team, starter_features=starter,
            bullpen_features=bullpen, results=results, certification=_cert(),
            completeness_mode="historical",
        )
    report = caught.value.report
    assert report["families"]["team"]["status"] == "PASS"
    assert report["families"]["starter"]["status"] == "PASS"
    assert report["families"]["bullpen"]["status"] == "FAIL"
    assert any("bullpen" in issue for issue in report["blocking_issues"])
    # Every game still had bullpen component rows (both sides present, just
    # dead-valued) so FEAT-005's absent-coverage exclusion does not fire here
    # -- this is specifically the FEAT-006 dead-column gate, not FEAT-005.
    assert len(build_feature_matrix(
        games, team_features=team, starter_features=starter,
        bullpen_features=bullpen, results=results, certification=_cert(),
        completeness_mode="inference",
    )["excluded"]) == 0


def test_all_families_healthy_passes_full_gate() -> None:
    games, team, starter, bullpen, results = _full_matrix_inputs(bullpen_dead=False)
    matrix = build_feature_matrix(
        games, team_features=team, starter_features=starter,
        bullpen_features=bullpen, results=results, certification=_cert(),
        completeness_mode="historical",
    )
    assert matrix["feature_completeness"]["status"] == "PASS"
    assert len(matrix["rows"]) == 32
