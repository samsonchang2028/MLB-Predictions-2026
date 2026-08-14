from __future__ import annotations

from app.about import MODEL_SUMMARY, evidence_labels, methodology_sections, primary_metric_names


def test_about_copy_explains_pick_without_profit_or_guarantee_claims() -> None:
    copy = "\n".join(section.body for section in methodology_sections())

    assert "model estimates the chance that the home team wins" in copy
    assert "market-implied chance" in copy
    assert "not a guaranteed winner" in copy
    assert "not a staking policy" in copy
    assert "profit" not in copy.lower()


def test_about_copy_defines_pass_as_no_play_not_win_loss() -> None:
    pass_section = next(
        section for section in methodology_sections() if section.title == "What PASS means"
    )

    assert "display threshold" in pass_section.body
    assert "should not be counted as won or lost plays" in pass_section.body


def test_about_limitations_list_v1_exclusions() -> None:
    copy = "\n".join(section.body for section in methodology_sections())

    for phrase in [
        "moneyline only",
        "totals",
        "props",
        "weather features",
        "Monte Carlo",
        "Kalshi/arbitrage",
        "bankroll/staking",
    ]:
        assert phrase in copy


def test_evidence_labels_keep_daily_journal_separate_from_selection_evidence() -> None:
    labels = evidence_labels()

    assert [row.label for row in labels] == [
        "Development/tuning evidence",
        "Final 2026 holdout evidence",
        "Current daily prediction/result journal",
    ]
    assert "not model-selection evidence" in labels[-1].description
    assert "not to change the model" in labels[1].description


def test_about_primary_metrics_and_model_identity_match_v1_methodology() -> None:
    assert MODEL_SUMMARY == (
        "V1 tuned XGBoost · expanding training window · uncalibrated probabilities"
    )
    assert primary_metric_names() == ["Log loss", "Brier score", "Calibration"]
