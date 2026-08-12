"""Small XGBoost hyperparameter search on the repaired 2021-2025 build.

This is a local ML-only operator script. It rebuilds the repaired historical
Gold matrix from local DuckDB/Silver, evaluates a modest hand-curated XGBoost
grid on the established expanding development folds, compares candidates by
the project primary metric order (log loss, then Brier, then ECE), and evaluates
calibration for the best candidate. It never reads 2026.
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

from evaluation.calibration import compare_calibration  # noqa: E402
from evaluation.runner import run_evaluation  # noqa: E402
from evaluation.splits import expanding_folds  # noqa: E402
from models import xgboost_model  # noqa: E402
from rerun_repaired_experiment import (  # noqa: E402
    DEFAULT_CERTIFICATION,
    _build_matrix,
    _load_inputs,
)


DEFAULT_OUTPUT = Path("reports") / "experiments" / "v1-repaired-xgboost-tuning-a910017bac839af5.json"


def _candidate_grid() -> list[dict[str, Any]]:
    """Curated first-pass grid: broad enough to test regularization/complexity,
    small enough to keep selection noise and runtime under control."""
    candidates: list[dict[str, Any]] = []
    for max_depth in (2, 3, 4):
        for learning_rate, n_estimators in ((0.03, 200), (0.05, 150), (0.08, 100)):
            for reg_lambda in (1.0, 5.0):
                candidates.append(
                    {
                        "max_depth": max_depth,
                        "learning_rate": learning_rate,
                        "n_estimators": n_estimators,
                        "reg_lambda": reg_lambda,
                        "min_child_weight": 1.0,
                        "subsample": 1.0,
                        "colsample_bytree": 1.0,
                    }
                )
    # Add one conservative shallow/subsampled candidate and the current default
    # explicitly for before/after traceability.
    candidates.append(
        {
            "max_depth": 2,
            "learning_rate": 0.03,
            "n_estimators": 300,
            "reg_lambda": 10.0,
            "min_child_weight": 3.0,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
        }
    )
    candidates.append({})
    return candidates


class _TunedXGBoost:
    MODEL_NAME = "xgboost_tuned"

    def __init__(self, params: dict[str, Any]):
        self.params = dict(params)

    def build_model(self, random_state: int = 0) -> Any:
        return xgboost_model.build_model(random_state=random_state, **self.params)


def _rank_key(row: dict[str, Any]) -> tuple[float, float, float, int]:
    aggregate = row["aggregate"]
    return aggregate["log_loss"], aggregate["brier"], aggregate["ece"], row["candidate_id"]


def _evaluate_candidate(
    matrix: dict[str, Any],
    candidate_id: int,
    params: dict[str, Any],
    *,
    random_state: int,
) -> dict[str, Any]:
    model = _TunedXGBoost(params)
    report = run_evaluation(
        model,
        matrix,
        expanding_folds(),
        random_state=random_state,
        model_name="xgboost",
    )
    return {
        "candidate_id": candidate_id,
        "params": params,
        "aggregate": report["aggregate"],
        "folds": report["folds"],
    }


def _json_default(value: Any) -> str:
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tune XGBoost on repaired 2021-2025 expanding folds."
    )
    parser.add_argument("--database", default="data/mlb.duckdb")
    parser.add_argument("--certification", default=str(DEFAULT_CERTIFICATION))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--random-state", type=int, default=0)
    args = parser.parse_args(argv)

    started = time.time()
    print("[load] repaired Gold matrix", flush=True)
    inputs = _load_inputs(args.database, Path(args.certification))
    matrix, input_counts = _build_matrix(inputs)
    print(
        "[gold] "
        f"rows={len(matrix['rows'])} columns={len(matrix['feature_columns'])} "
        f"completeness={matrix['feature_completeness']['status']}",
        flush=True,
    )

    results: list[dict[str, Any]] = []
    candidates = _candidate_grid()
    for idx, params in enumerate(candidates, start=1):
        print(f"[tune] candidate {idx}/{len(candidates)} params={params}", flush=True)
        result = _evaluate_candidate(
            matrix, idx, params, random_state=args.random_state
        )
        a = result["aggregate"]
        print(
            f"[tune] candidate {idx} log_loss={a['log_loss']:.6f} "
            f"brier={a['brier']:.6f} ece={a['ece']:.6f}",
            flush=True,
        )
        results.append(result)

    ranking = sorted(results, key=_rank_key)
    best = ranking[0]
    print(
        f"[best] candidate={best['candidate_id']} params={best['params']} "
        f"log_loss={best['aggregate']['log_loss']:.6f}",
        flush=True,
    )
    print("[calibration] best tuned candidate", flush=True)
    calibration = compare_calibration(
        _TunedXGBoost(best["params"]),
        matrix,
        expanding_folds(),
        random_state=args.random_state,
    )

    report = {
        "status": "REPAIRED_XGBOOST_TUNING_EVIDENCE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "build_id": matrix["build_id"],
        "certification_artifact": args.certification,
        "random_state": args.random_state,
        "folds": "expanding",
        "selection_metric_order": ["log_loss", "brier", "ece"],
        "n_rows": len(matrix["rows"]),
        "n_feature_columns": len(matrix["feature_columns"]),
        "input_counts": input_counts,
        "candidate_count": len(candidates),
        "ranking": ranking,
        "best": best,
        "calibration": calibration,
        "runtime_seconds": round(time.time() - started, 3),
        "holdout_2026": "not inspected",
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"[write] {output}", flush=True)
    print(f"[done] seconds={report['runtime_seconds']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
