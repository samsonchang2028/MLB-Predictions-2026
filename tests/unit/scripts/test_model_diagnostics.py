from scripts.model_diagnostics import confidence_distribution, interpretation, metric_gaps


def test_metric_gaps_compute_holdout_minus_development():
    dev = {"log_loss": 0.68, "brier": 0.24, "ece": 0.01, "secondary": {"roc_auc": 0.58}}
    holdout = {"log_loss": 0.69, "brier": 0.25, "ece": 0.02, "secondary": {"roc_auc": 0.55}}

    gaps = metric_gaps(dev, holdout)

    assert round(gaps["log_loss"], 3) == 0.01
    assert round(gaps["brier"], 3) == 0.01
    assert round(gaps["ece"], 3) == 0.01
    assert round(gaps["roc_auc"], 3) == -0.03


def test_confidence_distribution_bins_predictions_and_win_rates():
    dist = confidence_distribution([
        {"p_home_win": 0.39, "y_true": 1},
        {"p_home_win": 0.52, "y_true": 0},
        {"p_home_win": 0.71, "y_true": 1},
    ])
    by_bucket = {row["bucket"]: row for row in dist}

    assert by_bucket["[0.00,0.40)"]["count"] == 1
    assert by_bucket["[0.00,0.40)"]["home_win_rate"] == 1.0
    assert by_bucket["[0.50,0.55)"]["count"] == 1
    assert by_bucket["[0.70,1.00]"]["count"] == 1
    assert sum(row["share"] for row in dist) == 1.0


def test_interpretation_flags_modest_degradation_and_non_extreme_probabilities():
    dist = confidence_distribution([
        {"p_home_win": 0.52, "y_true": 1},
        {"p_home_win": 0.55, "y_true": 0},
    ])
    result = interpretation({"log_loss": 0.007, "roc_auc": -0.04}, dist)

    assert "modest_holdout_log_loss_degradation" in result["flags"]
    assert "holdout_auc_drop" in result["flags"]
    assert "probabilities_not_extreme" in result["flags"]
