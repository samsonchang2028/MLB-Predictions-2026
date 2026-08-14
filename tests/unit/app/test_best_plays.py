from __future__ import annotations

from app.best_plays import build_best_plays_report


def _row(game_pk: int, difference: float, *, play: bool = True) -> dict:
    return {
        "game_pk": game_pk,
        "pick": f"Team {game_pk}",
        "matchup": f"Away {game_pk} @ Home {game_pk}",
        "game_start_pacific": "2026-08-14 19:10 PDT",
        "difference": difference,
        "recommendation": f"PLAY Team {game_pk}" if play else "PASS",
        "result_status": "Pending",
        "result_label": None,
        "play": play,
    }


def test_best_plays_rank_by_absolute_displayed_difference_with_stable_ties() -> None:
    report = build_best_plays_report(
        [
            _row(3, 0.04),
            _row(1, 0.02),
            _row(2, -0.04),
        ],
        limit=3,
    )

    assert report["status"] == "ok"
    assert [row["game_pk"] for row in report["rows"]] == [2, 3, 1]
    assert [row["Rank"] for row in report["rows"]] == [1, 2, 3]


def test_best_plays_limit_rows_without_hiding_recommendation() -> None:
    report = build_best_plays_report(
        [_row(1, 0.01, play=False), _row(2, 0.05), _row(3, 0.03)],
        limit=2,
    )

    assert [row["game_pk"] for row in report["rows"]] == [2, 3]
    assert report["rows"][0]["Recommendation"] == "PLAY Team 2"


def test_best_plays_no_predictions_empty_state() -> None:
    assert build_best_plays_report([]) == {"status": "no_predictions", "rows": []}


def test_best_plays_all_pass_state_keeps_rows_visible_as_pass() -> None:
    report = build_best_plays_report([_row(1, 0.01, play=False), _row(2, 0.015, play=False)])

    assert report["status"] == "all_pass"
    assert [row["Recommendation"] for row in report["rows"]] == ["PASS", "PASS"]
