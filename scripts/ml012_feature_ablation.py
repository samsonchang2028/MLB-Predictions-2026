"""Run the ML-012 feature-family ablation study on the repaired 2021-2025 build.

Operator entry point. Reuses ``rerun_repaired_experiment.py``'s Gold matrix
loader (identical certified inputs already used by ML-007/008/011) and drives
``experiments.ablation.run_ablation`` to produce a single JSON report. Never
reads 2026.
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

from experiments.ablation import run_ablation  # noqa: E402
from rerun_repaired_experiment import (  # noqa: E402
    DEFAULT_CERTIFICATION,
    _build_matrix,
    _load_inputs,
)

DEFAULT_OUTPUT = Path("reports") / "experiments" / "ml-012-feature-ablation.json"


def _json_default(value: Any) -> str:
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ML-012 feature-family ablation study on the repaired build."
    )
    parser.add_argument("--database", default="data/mlb.duckdb")
    parser.add_argument("--certification", default=str(DEFAULT_CERTIFICATION))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--random-state", type=int, default=0)
    args = parser.parse_args(argv)

    started = time.time()
    certification_path = Path(args.certification)
    output_path = Path(args.output)

    print(f"[load] database={args.database}", flush=True)
    inputs = _load_inputs(args.database, certification_path)
    print("[gold] building repaired historical matrix", flush=True)
    matrix, input_counts = _build_matrix(inputs)
    print(
        "[gold] "
        f"rows={len(matrix['rows'])} excluded={len(matrix['excluded'])} "
        f"columns={len(matrix['feature_columns'])} "
        f"completeness={matrix['feature_completeness']['status']}",
        flush=True,
    )

    print("[ml-012] running feature-family ablation", flush=True)
    ablation = run_ablation(matrix, random_state=args.random_state)
    family_sizes = {name: len(cols) for name, cols in ablation["families"].items()}
    print(f"[ml-012] families={family_sizes}", flush=True)
    flagged = ablation["leakage_scan"]["flagged_columns"]
    print(f"[ml-012] leakage scan flagged {len(flagged)} column(s)", flush=True)

    report = {
        "status": "ML012_FEATURE_ABLATION_EVIDENCE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "build_id": matrix["build_id"],
        "certification_artifact": str(certification_path),
        "random_state": args.random_state,
        "n_rows": len(matrix["rows"]),
        "n_feature_columns": len(matrix["feature_columns"]),
        "input_counts": input_counts,
        "families": ablation["families"],
        "primary_model": ablation["primary_model"],
        "windows": ablation["windows"],
        "fold_metrics": ablation["fold_metrics"],
        "variant_aggregates": ablation["variant_aggregates"],
        "sanity_check_fold_metrics": ablation["sanity_check_fold_metrics"],
        "sanity_check_variant_aggregates": ablation["sanity_check_variant_aggregates"],
        "leakage_scan": ablation["leakage_scan"],
        "runtime_seconds": round(time.time() - started, 3),
        "holdout_2026": "not inspected",
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"[write] {output_path}", flush=True)
    print(f"[done] seconds={report['runtime_seconds']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
