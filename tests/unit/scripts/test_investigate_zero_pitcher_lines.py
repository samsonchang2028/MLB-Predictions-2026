from scripts.investigate_zero_pitcher_lines import build_report


def test_build_report_summarizes_guard_failures_and_zero_silver_rows():
    rows = [
        {
            "game_pk": 1,
            "season": "2021",
            "abstract_game_state": "Final",
            "detailed_state": "Final",
            "error_type": "ValueError",
            "error_message": "game-detail game_pk 1 home pitcher 10 has an all-zero pitching line (no outs or batters faced): hollow payload",
            "silver_pitcher_appearance_rows": 0,
        },
        {
            "game_pk": 2,
            "season": "2022",
            "abstract_game_state": "Final",
            "detailed_state": "Completed Early",
            "error_type": "ValueError",
            "error_message": "game-detail game_pk 2 away pitcher 11 has an all-zero pitching line (no outs or batters faced): hollow payload",
            "silver_pitcher_appearance_rows": 0,
        },
    ]

    report = build_report(rows, run_id="DATA-018-reingest-2021-2025")

    assert report["summary"]["failed_games"] == 2
    assert report["summary"]["all_zero_pitching_line_guard_failures"] == 2
    assert report["summary"]["zero_silver_pitcher_appearance_games"] == 2
    assert report["summary"]["abstract_final_games"] == 2
    assert report["by_season"] == {"2021": 1, "2022": 1}
    assert report["by_error_type"] == {"ValueError": 2}
    assert "guard rejections" in report["summary"]["interpretation"]
