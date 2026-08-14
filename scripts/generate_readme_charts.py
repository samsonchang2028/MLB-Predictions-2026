"""Generate README comparison charts from committed experiment reports."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "images"

COLORS = {
    "xgboost": "#1f6f4a",
    "random_forest": "#2c5f8a",
    "logistic_regression": "#b35c1e",
}
WINDOW_ORDER = ["expanding", "rolling_3", "rolling_2"]
MODEL_ORDER = ["xgboost", "random_forest", "logistic_regression"]
MODEL_LABEL = {
    "xgboost": "XGBoost",
    "random_forest": "Random Forest",
    "logistic_regression": "Logistic",
}
WINDOW_LABEL = {
    "expanding": "Expanding",
    "rolling_3": "Rolling 3",
    "rolling_2": "Rolling 2",
}


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "#f7f7f5",
            "axes.edgecolor": "#333333",
            "axes.grid": True,
            "grid.color": "#d9d9d4",
            "grid.linewidth": 0.8,
            "font.size": 11,
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.labelsize": 11,
        }
    )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _style()

    repaired = json.loads(
        (ROOT / "reports/experiments/v1-repaired-a910017bac839af5.json").read_text(
            encoding="utf-8"
        )
    )
    holdout = json.loads(
        (ROOT / "reports/experiments/v1-holdout-2026.json").read_text(encoding="utf-8")
    )

    rank = pd.DataFrame(repaired["ranking"])
    width = 0.24

    # 1) ROC-AUC by model x window
    fig, ax = plt.subplots(figsize=(10, 5.2))
    x = np.arange(len(WINDOW_ORDER))
    for i, model in enumerate(MODEL_ORDER):
        vals = []
        for w in WINDOW_ORDER:
            row = rank[(rank["model"] == model) & (rank["window"] == w)]
            vals.append(float(row["roc_auc"].iloc[0]) if len(row) else np.nan)
        bars = ax.bar(
            x + (i - 1) * width,
            vals,
            width,
            label=MODEL_LABEL[model],
            color=COLORS[model],
            edgecolor="white",
        )
        for bar, v in zip(bars, vals):
            if np.isnan(v):
                continue
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                v + 0.0015,
                f"{v:.3f}",
                ha="center",
                va="bottom",
                fontsize=8.5,
            )
    ax.axhline(0.5, color="#666666", linestyle="--", linewidth=1, label="Chance (0.50)")
    ax.set_xticks(x)
    ax.set_xticklabels([WINDOW_LABEL[w] for w in WINDOW_ORDER])
    ax.set_ylabel("ROC-AUC")
    ax.set_ylim(0.52, 0.585)
    ax.set_title(
        "ROC-AUC by model and training window\n"
        "(2024–2025 common test seasons, repaired build)"
    )
    ax.legend(frameon=False, loc="upper right")
    fig.tight_layout()
    fig.savefig(OUT / "roc_auc_by_model_window.png", dpi=160)
    plt.close(fig)

    # 2) Log loss + Brier
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 5.0))
    metrics = [
        ("log_loss", "Log loss (lower better)", 0.684, 0.692),
        ("brier", "Brier score (lower better)", 0.2455, 0.2495),
    ]
    for ax, (col, title, ymin, ymax) in zip(axes, metrics):
        for i, model in enumerate(MODEL_ORDER):
            vals = []
            for w in WINDOW_ORDER:
                row = rank[(rank["model"] == model) & (rank["window"] == w)]
                vals.append(float(row[col].iloc[0]) if len(row) else np.nan)
            ax.bar(
                x + (i - 1) * width,
                vals,
                width,
                label=MODEL_LABEL[model],
                color=COLORS[model],
                edgecolor="white",
            )
        ax.set_xticks(x)
        ax.set_xticklabels([WINDOW_LABEL[w] for w in WINDOW_ORDER])
        ax.set_title(title)
        ax.set_ylim(ymin, ymax)
        ax.legend(frameon=False, fontsize=9)
    axes[0].axhline(0.6912, color="#666666", linestyle="--", linewidth=1)
    axes[0].text(2.35, 0.69135, "base rate", fontsize=8, color="#555555")
    fig.suptitle(
        "Primary probability metrics — model × window (repaired 2021–2025)",
        fontweight="bold",
        y=1.02,
    )
    fig.tight_layout()
    fig.savefig(OUT / "logloss_brier_by_model_window.png", dpi=160, bbox_inches="tight")
    plt.close(fig)

    # 3) Dev vs holdout for locked V1
    dev = {
        "log_loss": 0.68124,
        "brier": 0.24408,
        "ece": 0.01578,
        "roc_auc": 0.58446,
        "accuracy": 0.56396,
    }
    m = holdout["metrics"]
    sec = m.get("secondary", {})
    ho = {
        "log_loss": m["log_loss"],
        "brier": m["brier"],
        "ece": m["ece"],
        "roc_auc": sec["roc_auc"],
        "accuracy": sec["accuracy"],
    }

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    w = 0.35
    lower_keys = [("log_loss", "Log loss"), ("brier", "Brier"), ("ece", "ECE")]
    xl = np.arange(len(lower_keys))
    axes[0].bar(
        xl - w / 2,
        [dev[k] for k, _ in lower_keys],
        w,
        label="Dev (tuned expanding)",
        color="#1f6f4a",
    )
    axes[0].bar(
        xl + w / 2,
        [ho[k] for k, _ in lower_keys],
        w,
        label="2026 holdout",
        color="#8a3b2c",
    )
    axes[0].set_xticks(xl)
    axes[0].set_xticklabels([lab for _, lab in lower_keys])
    axes[0].set_title("Lower is better")
    axes[0].legend(frameon=False)

    higher_keys = [("roc_auc", "ROC-AUC"), ("accuracy", "Accuracy")]
    xh = np.arange(len(higher_keys))
    axes[1].bar(
        xh - w / 2,
        [dev[k] for k, _ in higher_keys],
        w,
        label="Dev (tuned expanding)",
        color="#1f6f4a",
    )
    axes[1].bar(
        xh + w / 2,
        [ho[k] for k, _ in higher_keys],
        w,
        label="2026 holdout",
        color="#8a3b2c",
    )
    axes[1].set_xticks(xh)
    axes[1].set_xticklabels([lab for _, lab in higher_keys])
    axes[1].set_ylim(0.5, 0.62)
    axes[1].set_title("Higher is better")
    axes[1].legend(frameon=False)
    fig.suptitle(
        "Locked V1 XGBoost — development folds vs final 2026 holdout",
        fontweight="bold",
    )
    fig.tight_layout()
    fig.savefig(OUT / "v1_dev_vs_holdout.png", dpi=160)
    plt.close(fig)

    # 4) ROC-AUC leaderboard
    rank_sorted = rank.sort_values("roc_auc", ascending=True)
    fig, ax = plt.subplots(figsize=(9, 5.2))
    y = np.arange(len(rank_sorted))
    colors = [COLORS[m] for m in rank_sorted["model"]]
    labels = [
        f"{MODEL_LABEL[m]} · {WINDOW_LABEL[w]}"
        for m, w in zip(rank_sorted["model"], rank_sorted["window"])
    ]
    ax.barh(y, rank_sorted["roc_auc"], color=colors, edgecolor="white")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.set_xlabel("ROC-AUC")
    ax.set_xlim(0.54, 0.58)
    ax.axvline(0.5, color="#666666", linestyle="--", linewidth=1)
    for yi, v in zip(y, rank_sorted["roc_auc"]):
        ax.text(v + 0.0008, yi, f"{v:.4f}", va="center", fontsize=9)
    ax.set_title("ROC-AUC leaderboard (common 2024–2025 test seasons)")
    fig.tight_layout()
    fig.savefig(OUT / "roc_auc_leaderboard.png", dpi=160)
    plt.close(fig)

    # 5) Monte Carlo smoke — sim E[total] vs actual (2026-08-13 slate, 8 games)
    matchups = [
        "BOS@TOR",
        "SEA@NYY",
        "PHI@MIN",
        "PIT@MIA",
        "MIL@LAD",
        "TEX@LAA",
        "CLE@DET",
        "CIN@CWS",
    ]
    sim_totals = [8.24, 9.21, 8.88, 8.72, 9.10, 8.73, 8.90, 8.82]
    actual_totals = [7, 1, 8, 14, 9, 7, 3, 17]
    x = np.arange(len(matchups))
    width = 0.36
    fig, ax = plt.subplots(figsize=(11, 5.2))
    ax.bar(x - width / 2, sim_totals, width, label="Sim E[total]", color="#5b7c99")
    ax.bar(x + width / 2, actual_totals, width, label="Actual total", color="#c45c3e")
    ax.set_xticks(x)
    ax.set_xticklabels(matchups, rotation=35, ha="right")
    ax.set_ylabel("Runs")
    ax.set_ylim(0, 18)
    ax.set_title("Monte Carlo smoke (2026-08-13) — compressed sim totals vs reality")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(OUT / "monte_carlo_smoke_totals.png", dpi=160)
    plt.close(fig)

    for path in sorted(OUT.glob("*.png")):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
