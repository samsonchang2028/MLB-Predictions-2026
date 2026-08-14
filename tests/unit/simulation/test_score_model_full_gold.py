"""SIM-003: score model uses the full Gold feature column union."""

from __future__ import annotations

import numpy as np

from simulation.score_model import fit_score_model


def _run_rate_features(**overrides: float) -> dict[str, float]:
    features = {
        "home_team_runs_scored_avg_before": 4.5,
        "home_team_runs_scored_avg_L7": 4.2,
        "away_team_runs_scored_avg_before": 4.0,
        "away_team_runs_scored_avg_L7": 3.8,
        "home_team_runs_allowed_avg_before": 4.1,
        "home_team_runs_allowed_avg_L7": 4.0,
        "away_team_runs_allowed_avg_before": 4.3,
        "away_team_runs_allowed_avg_L7": 4.1,
    }
    features.update(overrides)
    return features


def _gold_training_rows(n: int = 60) -> list[dict]:
    rows: list[dict] = []
    for i in range(n):
        starter_era_diff = -0.5 + (i % 10) * 0.2
        bullpen_whip_diff = 0.1 + (i % 7) * 0.05
        home_offense = 3.5 + (i % 5) * 0.3
        away_offense = 3.2 + (i % 4) * 0.25
        features = _run_rate_features(
            home_team_runs_scored_avg_before=home_offense,
            home_team_runs_scored_avg_L7=home_offense - 0.1,
            away_team_runs_scored_avg_before=away_offense,
            away_team_runs_scored_avg_L7=away_offense - 0.1,
            home_team_runs_allowed_avg_before=away_offense + 0.2,
            home_team_runs_allowed_avg_L7=away_offense + 0.1,
            away_team_runs_allowed_avg_before=home_offense + 0.2,
            away_team_runs_allowed_avg_L7=home_offense + 0.1,
            diff_starter_era=starter_era_diff,
            home_starter_era_L5=3.8 + (i % 3) * 0.1,
            away_starter_era_L5=4.1 + (i % 4) * 0.1,
            diff_bullpen_whip=bullpen_whip_diff,
            home_bullpen_whip_L3=1.25,
            away_bullpen_whip_L3=1.30,
        )
        rows.append(
            {
                "features": features,
                "home_runs": int(round(home_offense + starter_era_diff * -0.5 + (i % 3))),
                "away_runs": int(round(away_offense + bullpen_whip_diff + (i % 2))),
            }
        )
    return rows


def test_fit_uses_full_gold_column_union():
    rows = _gold_training_rows()
    model = fit_score_model(rows, random_state=0)

    expected_columns = sorted(
        {key for row in rows for key in row["features"]},
    )
    assert len(expected_columns) > 8
    assert model.feature_names == tuple(expected_columns)
    assert len(model.feature_names) == 14


def test_starter_feature_change_shifts_lambda():
    rows = _gold_training_rows()
    model = fit_score_model(rows, random_state=0)

    base = _run_rate_features(
        diff_starter_era=0.0,
        home_starter_era_L5=3.9,
        away_starter_era_L5=4.0,
        diff_bullpen_whip=0.15,
        home_bullpen_whip_L3=1.25,
        away_bullpen_whip_L3=1.30,
    )
    worse_starter = {**base, "diff_starter_era": 1.5, "home_starter_era_L5": 5.4}
    better_starter = {**base, "diff_starter_era": -1.5, "home_starter_era_L5": 2.4}

    lambda_base_home, lambda_base_away = model.expected_rates(base)
    lambda_worse_home, _ = model.expected_rates(worse_starter)
    lambda_better_home, _ = model.expected_rates(better_starter)

    assert not np.isclose(lambda_worse_home, lambda_base_home)
    assert not np.isclose(lambda_better_home, lambda_base_home)
    assert not np.isclose(lambda_better_home, lambda_worse_home)


def test_explicit_feature_columns_override():
    rows = _gold_training_rows()
    subset = sorted(rows[0]["features"])[:10]
    model = fit_score_model(rows, feature_columns=subset, random_state=0)
    assert model.feature_names == tuple(subset)
