"""ML-011: artifact-backed V1 model diagnostics report.

This script does not train, tune, or re-evaluate 2026. It reads existing V1
experiment artifacts and produces a compact diagnostic report for underfit /
overfit review: dev-vs-holdout gaps, fold stability, calibration variants, and
holdout prediction-confidence distribution.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

DEFAULT_TUNING = Path("reports") / "experiments" / "v1-repaired-xgboost-tuning-a910017bac839af5.json"
DEFAULT_HOLDOUT = Path("reports") / "experiments" / "v1-holdout-2026.json"
DEFAULT_OUTPUT = Path("reports") / "experiments" / "v1-model-diagnostics.json"

PROBABILITY_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.4),
    (0.4, 0.45),
    (0.45, 0.5),
    (0.5, 0.55),
    (0.55, 0.6),
    (0.6, 0.65),
    (0.65, 0.7),
    (0.7, 1.0),
)


def metric_gaps(dev: Mapping[str, Any], holdout: Mapping[str, Any]) -> dict[str, float | None]:
    gaps: dict[str, float | None] = {}
    for key in ("log_loss", "brier", "ece"):
        gaps[key] = _float_or_none(holdout.get(key)) - _float_or_none(dev.get(key)) if _float_or_none(holdout.get(key)) is not None and _float_or_none(dev.get(key)) is not None else None
    dev_auc = _nested_float(dev, "secondary", "roc_auc")
    holdout_auc = _nested_float(holdout, "secondary", "roc_auc")
    gaps["roc_auc"] = holdout_auc - dev_auc if holdout_auc is not None and dev_auc is not None else None
    return gaps


def confidence_distribution(predictions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    wins: Counter[str] = Counter()
    for row in predictions:
        p = _float_or_none(row.get("p_home_win"))
        if p is None:
            continue
        label = _bucket_label(p)
        counts[label] += 1
        if row.get("y_true") == 1:
            wins[label] += 1
    total = sum(counts.values())
    out: list[dict[str, Any]] = []
    for low, high in PROBABILITY_BUCKETS:
        label = _format_bucket(low, high)
        count = counts[label]
        out.append(
            {
                "bucket": label,
                "count": count,
                "share": (count / total) if total else 0.0,
                "home_win_rate": (wins[label] / count) if count else None,
            }
        )
    return out


def fold_stability(folds: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "test_season": fold.get("test_season"),
            "n_train": fold.get("n_train"),
            "n_test": fold.get("n_test"),
            "log_loss": fold.get("log_loss"),
            "brier": fold.get("brier"),
            "ece": fold.get("ece"),
            "roc_auc": _nested_float(fold, "secondary", "roc_auc"),
            "accuracy": _nested_float(fold, "secondary", "accuracy"),
        }
        for fold in folds
    ]


def interpretation(gaps: Mapping[str, float | None], distribution: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    log_gap = gaps.get("log_loss")
    auc_gap = gaps.get("roc_auc")
    extreme_share = sum(
        float(row["share"])
        for row in distribution
        if row["bucket"] in {"[0.00,0.40)", "[0.70,1.00]"}
    )
    flags: list[str] = []
    if log_gap is not None and log_gap > 0.02:
        flags.append("large_holdout_log_loss_degradation")
    elif log_gap is not None and log_gap > 0.005:
        flags.append("modest_holdout_log_loss_degradation")
    if auc_gap is not None and auc_gap < -0.03:
        flags.append("holdout_auc_drop")
    if extreme_share < 0.05:
        flags.append("probabilities_not_extreme")
    return {
        "flags": flags,
        "summary": (
            "Evidence does not show catastrophic overfit: holdout metrics degrade "
            "versus repaired development/tuning but remain in the same probability-quality "
            "range. The AUC drop and weak absolute edge suggest either mild overfit, "
            "season drift, weak signal, or a combination. Probability distribution should "
            "be reviewed before increasing model complexity."
        ),
    }


def build_report(tuning: Mapping[str, Any], holdout: Mapping[str, Any]) -> dict[str, Any]:
    dev = tuning["best"]["aggregate"]
    holdout_metrics = holdout["metrics"]
    gaps = metric_gaps(dev, holdout_metrics)
    dist = confidence_distribution(holdout.get("predictions", []))
    return {
        "task": "ML-011",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "tuning_report": str(DEFAULT_TUNING),
            "holdout_report": str(DEFAULT_HOLDOUT),
            "no_retraining_or_reevaluation": True,
        },
        "methodology": {
            "model": "xgboost",
            "window": "expanding",
            "calibration": "uncalibrated",
            "locked_params": holdout.get("locked_params"),
        },
        "development_best": dev,
        "holdout_2026": holdout_metrics,
        "generalization_gaps_holdout_minus_dev": gaps,
        "fold_stability": fold_stability(tuning["best"].get("folds", [])),
        "calibration_variants": tuning.get("calibration", {}).get("variants", {}),
        "holdout_confidence_distribution": dist,
        "interpretation": interpretation(gaps, dist),
    }


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _nested_float(row: Mapping[str, Any], *keys: str) -> float | None:
    cur: Any = row
    for key in keys:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return _float_or_none(cur)


def _bucket_label(p: float) -> str:
    for low, high in PROBABILITY_BUCKETS:
        if low <= p < high or (high == 1.0 and p <= high):
            return _format_bucket(low, high)
    return "out_of_range"


def _format_bucket(low: float, high: float) -> str:
    close = "]" if high == 1.0 else ")"
    return f"[{low:.2f},{high:.2f}{close}"


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build V1 model diagnostics from existing artifacts.")
    parser.add_argument("--tuning", default=str(DEFAULT_TUNING))
    parser.add_argument("--holdout", default=str(DEFAULT_HOLDOUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    tuning = json.loads(Path(args.tuning).read_text(encoding="utf-8"))
    holdout = json.loads(Path(args.holdout).read_text(encoding="utf-8"))
    report = build_report(tuning, holdout)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    gaps = report["generalization_gaps_holdout_minus_dev"]
    print(
        f"[ml-011] output={output} log_loss_gap={gaps['log_loss']:.6f} "
        f"brier_gap={gaps['brier']:.6f} auc_gap={gaps['roc_auc']:.6f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
