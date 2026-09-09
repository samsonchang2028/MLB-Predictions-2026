"""MARKET-006 - production PLAY policy study CLI.

Reads immutable prediction/journal JSONL artifacts, evaluates the fixed policy
grid with chronological walk-forward validation, and writes versioned reports.
Never mutates source artifacts.
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

from market.policy_evaluation import build_study_report, render_markdown_report  # noqa: E402

DEFAULT_PREDICTIONS = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL = Path("state/predictions/journal.jsonl")
DEFAULT_REPORT_DIR = Path("reports/market")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"missing required input: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the MARKET-006 PLAY policy study.")
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--policy",
        default=None,
        help="optional single-policy shortcut (e.g. baseline)",
    )
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--output-dir", default=str(DEFAULT_REPORT_DIR))
    args = parser.parse_args(argv)

    started = time.time()
    predictions_path = Path(args.predictions)
    journal_path = Path(args.journal)
    daily_records = _read_jsonl(predictions_path)
    journal_records = _read_jsonl(journal_path)
    print(
        f"[load] predictions={len(daily_records)} journal={len(journal_records)}",
        flush=True,
    )

    try:
        report = build_study_report(
            daily_records=daily_records,
            journal_records=journal_records,
            run_id=args.run_id,
            input_paths={
                "predictions": str(predictions_path),
                "journal": str(journal_path),
            },
            model_version=args.model_version,
            single_policy=args.policy,
        )
    except ValueError as exc:
        print(f"[error] integrity failure: {exc}", file=sys.stderr, flush=True)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"play-policy-study-{args.run_id}.json"
    md_path = output_dir / f"play-policy-study-{args.run_id}.md"

    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    population_n = report["population"]["n_resolved_latest_per_game"]
    print(f"[report] resolved_population={population_n}", flush=True)
    evaluation = report.get("evaluation")
    if evaluation is not None:
        print(f"[report] candidates={evaluation['candidate_count']}", flush=True)
        print(f"[report] verdict={evaluation['verdict']['verdict']}", flush=True)
    print(f"[write] {json_path}", flush=True)
    print(f"[write] {md_path}", flush=True)
    print(f"[done] seconds={round(time.time() - started, 3)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
