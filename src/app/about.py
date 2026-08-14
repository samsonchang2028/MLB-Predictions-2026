"""Plain-English copy helpers for the Streamlit About page.

The About page explains the locked V1 methodology and product limitations.
It intentionally does not read DuckDB, retrain models, recompute metrics, or
derive new evaluation claims.
"""

from __future__ import annotations

from dataclasses import dataclass


MODEL_SUMMARY = "V1 tuned XGBoost · expanding training window · uncalibrated probabilities"


@dataclass(frozen=True)
class AboutSection:
    title: str
    body: str


@dataclass(frozen=True)
class EvidenceLabel:
    label: str
    description: str


def methodology_sections() -> list[AboutSection]:
    """Return user-facing methodology copy for Streamlit rendering."""
    return [
        AboutSection(
            title="What this app predicts",
            body=(
                "The model estimates the chance that the home team wins an MLB "
                "moneyline game. The dashboard then compares that model chance "
                "with the market-implied chance from available moneyline odds."
            ),
        ),
        AboutSection(
            title="How to read a pick",
            body=(
                "Pick means the side the model prefers relative to the market "
                "price. It is not a guaranteed winner, not a sportsbook "
                "recommendation, and not a staking policy."
            ),
        ),
        AboutSection(
            title="What PASS means",
            body=(
                "PASS means the model-market difference did not clear the "
                "current display threshold. PASS/no-play rows should not be "
                "counted as won or lost plays."
            ),
        ),
        AboutSection(
            title="What V1 does not include yet",
            body=(
                "V1 is moneyline only. It does not model totals, props, weather "
                "features, Monte Carlo simulations, Kalshi/arbitrage workflows, "
                "or bankroll/staking decisions."
            ),
        ),
    ]


def evidence_labels() -> list[EvidenceLabel]:
    """Return the evidence labels the UI should keep separated."""
    return [
        EvidenceLabel(
            label="Development/tuning evidence",
            description=(
                "Repaired 2021-2025 certified data used to select and tune the "
                "locked V1 methodology before the final holdout was inspected."
            ),
        ),
        EvidenceLabel(
            label="Final 2026 holdout evidence",
            description=(
                "The one-time untouched 2026 evaluation used only to report final "
                "V1 probability-quality metrics, not to change the model."
            ),
        ),
        EvidenceLabel(
            label="Current daily prediction/result journal",
            description=(
                "Operational records for displayed daily picks and outcomes. "
                "This is product monitoring context, not model-selection evidence."
            ),
        ),
    ]


def primary_metric_names() -> list[str]:
    """Return the probability-quality metrics that should be emphasized."""
    return ["Log loss", "Brier score", "Calibration"]
