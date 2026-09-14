"""Model quality dashboard — historical vs prospective probability evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path

import streamlit as st

from app.dashboard_analytics import (
    DashboardPaths,
    HISTORICAL_EVIDENCE_LABEL,
    PROSPECTIVE_EVIDENCE_LABEL,
    build_daily_monitoring_summary,
    build_prospective_model_quality,
    read_jsonl,
)
from app.performance import (
    FINAL_HOLDOUT_LABEL,
    load_calibration_comparison,
    load_holdout_summary,
    load_model_window_ranking,
)

DEFAULT_HOLDOUT_REPORT_PATH = Path("reports/experiments/v1-holdout-2026.json")
DEFAULT_DEVELOPMENT_REPORT_PATH = Path(
    "reports/experiments/v1-repaired-a910017bac839af5.json"
)


def _paths() -> DashboardPaths:
    return DashboardPaths(
        predictions=Path(os.environ.get("PREDICTIONS_STORE_PATH", DashboardPaths.predictions)),
        journal=Path(os.environ.get("PREDICTION_JOURNAL_PATH", DashboardPaths.journal)),
        holdout_report=Path(os.environ.get("HOLDOUT_REPORT_PATH", DEFAULT_HOLDOUT_REPORT_PATH)),
        development_report=Path(
            os.environ.get("PERFORMANCE_REPORT_PATH", DEFAULT_DEVELOPMENT_REPORT_PATH)
        ),
    )


def _render_metric_block(title: str, metrics: dict | None, *, sample_label: str) -> None:
    st.markdown(f"**{title}**")
    if metrics is None:
        st.info(f"No resolved predictions yet for {sample_label.lower()}.")
        return
    cols = st.columns(4)
    cols[0].metric("Log loss", round(metrics["log_loss"], 4))
    cols[1].metric("Brier", round(metrics["brier"], 4))
    cols[2].metric("Calibration (ECE)", round(metrics["ece"], 4))
    cols[3].metric("Sample size (N)", metrics["n"])
    st.caption(f"{sample_label}. Small N can move quickly — avoid over-reading short windows.")


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


def _number(value: float | int | None, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _daily_display_rows(rows: list[dict]) -> list[dict]:
    return [
        {
            "Date": row["run_date"],
            "Games": row["games"],
            "Resolved": row["resolved"],
            "Pending": row["pending"],
            "Log loss": _number(row["log_loss"]),
            "Brier": _number(row["brier"]),
            "ECE": _number(row["ece"]),
            "Baseline count": row["play_count"],
            "Baseline W-L-P": f"{row['play_wins']}-{row['play_losses']}-{row['play_pending']}",
            "Baseline win rate": _pct(row["play_win_rate"]),
            "Baseline ROI": _pct(row["play_roi"]),
            "Baseline units": _number(row["play_units"], 2),
            "Raw model count": row["raw_model_count"],
            "Raw model W-L-P": (
                f"{row['raw_model_wins']}-{row['raw_model_losses']}-{row['raw_model_pending']}"
            ),
            "Raw model win rate": _pct(row["raw_model_win_rate"]),
            "Raw model ROI": _pct(row["raw_model_roi"]),
            "Raw model units": _number(row["raw_model_units"], 2),
        }
        for row in sorted(rows, key=lambda item: item["run_date"], reverse=True)
    ]


def _aggregate_strategy(rows: list[dict], prefix: str) -> dict[str, float | int | None]:
    wins = sum(int(row[f"{prefix}_wins"]) for row in rows)
    losses = sum(int(row[f"{prefix}_losses"]) for row in rows)
    pending = sum(int(row[f"{prefix}_pending"]) for row in rows)
    units_values = [row[f"{prefix}_units"] for row in rows if row[f"{prefix}_units"] is not None]
    staked = sum(int(row[f"{prefix}_staked_units"]) for row in rows)
    finished = wins + losses
    return {
        "count": sum(int(row[f"{prefix}_count"]) for row in rows),
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "win_rate": wins / finished if finished else None,
        "units": float(sum(units_values)) if units_values else None,
        "roi": float(sum(units_values)) / staked if staked and units_values else None,
        "staked_units": staked,
    }


st.set_page_config(page_title="Model Quality", layout="wide")
st.title("Model Quality")
st.caption(
    "Probability quality is primary: log loss, Brier score, and calibration. "
    "Historical evaluation and prospective production monitoring are shown separately."
)

paths = _paths()
predictions = read_jsonl(paths.predictions)
journal = read_jsonl(paths.journal)
prospective = build_prospective_model_quality(predictions, journal)
daily_monitoring = build_daily_monitoring_summary(predictions, journal)

evidence_tab, monitoring_tab, trust_tab = st.tabs(
    ["Model Evidence", "Daily Monitoring", "Trust & Model Evidence"]
)

with evidence_tab:
    st.subheader(HISTORICAL_EVIDENCE_LABEL)
    st.caption("Locked development and final holdout reports — not live betting results.")

    holdout_path = paths.holdout_report
    if not holdout_path.exists():
        st.info(f"No final holdout report found at {holdout_path}.")
    else:
        holdout_report = json.loads(holdout_path.read_text(encoding="utf-8"))
        [summary] = load_holdout_summary(holdout_report)
        st.markdown(f"**Final 2026 holdout** · {FINAL_HOLDOUT_LABEL}")
        st.dataframe(
            [
                {
                    "Log Loss": round(summary["log_loss"], 5),
                    "Brier": round(summary["brier"], 5),
                    "ECE": round(summary["ece"], 5),
                    "N": summary["n_test"],
                    "Evidence": summary["evidence_label"],
                }
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.markdown("**Secondary holdout metrics**")
        st.dataframe(
            [
                {
                    "ROC-AUC": round(summary["roc_auc"], 4),
                    "Accuracy": round(summary["accuracy"], 4),
                    "Evidence": summary["evidence_label"],
                }
            ],
            use_container_width=True,
            hide_index=True,
        )

    dev_path = paths.development_report
    if not dev_path.exists():
        st.info(f"No development experiment report found at {dev_path}.")
    else:
        report = json.loads(dev_path.read_text(encoding="utf-8"))
        st.markdown("**Development model × window ranking**")
        st.dataframe(
            [
                {
                    "Model": row["model"],
                    "Window": row["window"],
                    "Log Loss": round(row["log_loss"], 5),
                    "Brier": round(row["brier"], 5),
                    "ECE": round(row["ece"], 5),
                    "N": row["n_test"],
                    "Evidence": row["evidence_label"],
                }
                for row in load_model_window_ranking(report)
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.markdown("**Secondary development metrics**")
        st.dataframe(
            [
                {
                    "Model": row["model"],
                    "Window": row["window"],
                    "ROC-AUC": round(row["roc_auc"], 4),
                    "Accuracy": round(row["accuracy"], 4),
                    "Evidence": row["evidence_label"],
                }
                for row in load_model_window_ranking(report)
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.markdown("**Development calibration / reliability comparison**")
        st.dataframe(
            [
                {
                    "Method": row["method"],
                    "Log Loss": round(row["log_loss"], 5),
                    "Brier": round(row["brier"], 5),
                    "ECE": round(row["ece"], 5),
                    "N": row["n_test"],
                    "Evidence": row["evidence_label"],
                }
                for row in load_calibration_comparison(report)
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.subheader(PROSPECTIVE_EVIDENCE_LABEL)
    st.caption(
        "Live production predictions joined to finished games in the journal. "
        "This is monitoring only — not holdout evidence and not used for retraining."
    )
    st.write(prospective["note"])
    meta_cols = st.columns(4)
    meta_cols[0].metric("Resolved N", prospective["resolved_count"])
    meta_cols[1].metric("Pending N", prospective["pending_count"])
    meta_cols[2].write(
        f"Evaluation start: {prospective['evaluation_start_date'] or '—'}"
    )
    meta_cols[3].write(
        f"Model version(s): {', '.join(prospective['model_versions']) or '—'}"
    )
    _render_metric_block(
        "Prospective probability metrics",
        prospective["metrics"],
        sample_label=f"{PROSPECTIVE_EVIDENCE_LABEL} · resolved predictions only",
    )
    if prospective["metrics"] is not None:
        st.markdown("**Secondary prospective metrics**")
        st.dataframe(
            [
                {
                    "ROC-AUC": "—"
                    if prospective["metrics"]["roc_auc"] is None
                    else round(prospective["metrics"]["roc_auc"], 4),
                    "Accuracy": f"{prospective['metrics']['accuracy']:.1%}",
                    "Evidence": PROSPECTIVE_EVIDENCE_LABEL,
                }
            ],
            use_container_width=True,
            hide_index=True,
        )

    if prospective["probability_buckets"]:
        st.markdown("**Prospective reliability by probability bucket (P(home))**")
        st.dataframe(prospective["probability_buckets"], use_container_width=True, hide_index=True)

with monitoring_tab:
    st.subheader("Daily Prospective Monitoring")
    st.caption(
        "Each date uses the latest stored prediction per game. Pending games are excluded "
        "from daily win rate and ROI until result enrichment writes a valid journal row."
    )
    if not daily_monitoring:
        st.info("No daily prediction artifacts found yet.")
    else:
        baseline_total = _aggregate_strategy(daily_monitoring, "play")
        raw_model_total = _aggregate_strategy(daily_monitoring, "raw_model")
        st.markdown("**Strategy comparison (resolved slates only)**")
        comparison_cols = st.columns(2)
        with comparison_cols[0]:
            st.markdown("**Baseline PLAY**")
            st.metric(
                "Win rate",
                _pct(baseline_total["win_rate"]),
                help="Edge-selected side when abs(edge) >= 2%",
            )
            st.write(
                f"Record: {baseline_total['wins']}-{baseline_total['losses']}-"
                f"{baseline_total['pending']} · ROI {_pct(baseline_total['roi'])} · "
                f"Units {_number(baseline_total['units'], 2)}"
            )
        with comparison_cols[1]:
            st.markdown("**Raw model favorite**")
            st.metric(
                "Win rate",
                _pct(raw_model_total["win_rate"]),
                help="Bet the model favorite on every game (P(home) >= 50% -> home)",
            )
            st.write(
                f"Record: {raw_model_total['wins']}-{raw_model_total['losses']}-"
                f"{raw_model_total['pending']} · ROI {_pct(raw_model_total['roi'])} · "
                f"Units {_number(raw_model_total['units'], 2)}"
            )
        st.dataframe(_daily_display_rows(daily_monitoring), use_container_width=True, hide_index=True)
        st.caption(
            "Model metrics use all resolved predictions. Baseline PLAY uses the "
            "edge-selected side when abs(edge) meets the board threshold. Raw model "
            "bets the model favorite on every game with valid odds. PASS rows are "
            "excluded from baseline counts only."
        )

with trust_tab:
    st.subheader("What This Project Predicts")
    st.write(
        "The model predicts `P(home team wins)` for each MLB moneyline game. "
        "That probability is separate from the Streamlit `PLAY/PASS` label, "
        "which is a market-selection rule based on whether model-vs-market disagreement "
        "is large enough to be actionable."
    )

    st.subheader("Why The Data Is Trustworthy")
    st.write(
        "Raw MLB and odds payloads are preserved as immutable Bronze inputs before they "
        "are normalized into Silver and Gold tables. The historical build must pass a "
        "certification gate before model work; the repaired 2021-2025 build has a PASS "
        "artifact at `state/data-certifications/certification-PASS-a910017bac839af5.json`."
    )
    st.write(
        "Joins use MLB `game_pk` as the canonical game identity instead of team/date alone. "
        "That is important for doubleheaders, postponed games, suspended games, and reschedules."
    )

    st.subheader("Why The Evaluation Is Fair")
    st.write(
        "Features are point-in-time safe: rolling inputs shift before rolling, so the "
        "current game cannot influence its own prediction. Prediction timestamps must be "
        "before first pitch, and odds snapshots must be before the prediction cutoff."
    )
    st.write(
        "Model selection used chronological validation on repaired 2021-2025 development "
        "evidence. The 2026 season was held out from selection and tuning, then evaluated "
        "once as the final holdout."
    )

    st.subheader("How To Read The Metrics")
    st.dataframe(
        [
            {
                "Metric": "Log loss",
                "Role": "Primary",
                "What it checks": "Penalizes confident wrong probabilities.",
            },
            {
                "Metric": "Brier score",
                "Role": "Primary",
                "What it checks": "Measures average probability error.",
            },
            {
                "Metric": "ECE",
                "Role": "Primary",
                "What it checks": "Checks calibration: whether predicted probabilities match observed rates.",
            },
            {
                "Metric": "ROC-AUC",
                "Role": "Secondary",
                "What it checks": "Ranks winners above losers independent of threshold.",
            },
            {
                "Metric": "Accuracy",
                "Role": "Secondary",
                "What it checks": "Counts modal winner correctness at the 0.5 probability boundary.",
            },
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Professor-Friendly Summary")
    st.write(
        "This is a probability estimator for a difficult and efficient market, "
        "not a high-accuracy classifier and not betting advice. The locked V1 evidence "
        "shows modest signal: development metrics are slightly stronger than the 2026 "
        "holdout, which is consistent with weak MLB signal, mild overfit, season drift, "
        "or some combination."
    )
    st.write(
        "Betting results answer a separate and noisier question: when the model disagrees "
        "with the market enough to trigger `PLAY`, what happened afterward? Those rows are "
        "useful monitoring, but they are not the primary proof that the model probabilities "
        "are good."
    )
