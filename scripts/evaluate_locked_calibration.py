"""ML-014: pre-2026 calibration evaluation for the ADR-006 locked model.

This script rebuilds the repaired 2021-2025 Gold matrix, evaluates raw,
Platt/sigmoid, and isotonic probabilities on the established expanding folds,
and writes a durable diagnostics report. It never loads or scores 2026.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from evaluation.calibration import compare_refit_calibration  # noqa: E402
from evaluation.holdout import LOCKED_PARAMS  # noqa: E402
from evaluation.splits import expanding_folds  # noqa: E402
from models import xgboost_model  # noqa: E402
from rerun_repaired_experiment import (  # noqa: E402
    DEFAULT_CERTIFICATION,
    _build_matrix,
    _load_inputs,
)

DEFAULT_OUTPUT = (
    Path("reports")
    / "experiments"
    / "v1-locked-calibration-pre2026-a910017bac839af5.json"
)


def _build_locked_xgboost(random_state: int = 0) -> Any:
    return xgboost_model.build_model(random_state=random_state, **LOCKED_PARAMS)


def _json_default(value: Any) -> str:
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate calibration for locked XGBoost using pre-2026 folds."
    )
    parser.add_argument("--database", default="data/mlb.duckdb")
    parser.add_argument("--certification", default=str(DEFAULT_CERTIFICATION))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--random-state", type=int, default=0)
    parser.add_argument("--calibration-fraction", type=float, default=0.2)
    args = parser.parse_args(argv)

    started = time.time()
    certification_path = Path(args.certification)
    print("[load] repaired certified 2021-2025 Gold matrix", flush=True)
    inputs = _load_inputs(args.database, certification_path)
    matrix, input_counts = _build_matrix(inputs)
    seasons = sorted({int(row["game_date"].year) for row in matrix["rows"]})
    if seasons != [2021, 2022, 2023, 2024, 2025]:
        raise ValueError(
            "calibration selection requires exactly 2021-2025; "
            f"loaded seasons={seasons}"
        )

    print(
        f"[gold] rows={len(matrix['rows'])} features={len(matrix['feature_columns'])} "
        f"seasons={seasons} completeness={matrix['feature_completeness']['status']}",
        flush=True,
    )
    comparison = compare_refit_calibration(
        _build_locked_xgboost,
        matrix,
        expanding_folds(),
        random_state=args.random_state,
        calibration_fraction=args.calibration_fraction,
        model_name="adr-006-tuned-xgboost",
    )

    for method in ("raw", "sigmoid", "isotonic"):
        metrics = comparison["variants"][method]["aggregate"]
        print(
            f"[metrics] method={method} log_loss={metrics['log_loss']:.6f} "
            f"brier={metrics['brier']:.6f} ece={metrics['ece']:.6f} "
            f"n={metrics['n_test']}",
            flush=True,
        )
    print(f"[recommendation] {comparison['recommendation']}", flush=True)

    report = {
        "status": "PRE_2026_CALIBRATION_EVIDENCE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "build_id": matrix["build_id"],
        "certification_artifact": str(certification_path),
        "locked_hyperparameters": dict(LOCKED_PARAMS),
        "input_counts": input_counts,
        "n_rows": len(matrix["rows"]),
        "n_feature_columns": len(matrix["feature_columns"]),
        "selection_metric_order": ["log_loss", "brier", "ece"],
        "selection_rule": (
            "calibrated pooled log loss and Brier must both beat raw, and log "
            "loss must improve in a strict majority of temporal folds"
        ),
        "holdout_2026": "not loaded, not scored, and not used for selection",
        "production_promotion": "not authorized by this evaluation",
        "comparison": comparison,
        "runtime_seconds": round(time.time() - started, 3),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"[write] {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
