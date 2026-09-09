"""MARKET-008 - closing line value validation CLI.

Reads immutable prediction/journal/odds_books JSONL artifacts, evaluates
selected-side CLV with strict timestamp integrity, and writes versioned reports.
Never mutates source artifacts.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from market.clv_evaluation import (  # noqa: E402
    build_clv_report,
    json_default,
    render_markdown_report,
)

DEFAULT_PREDICTIONS = Path("state/predictions/daily.jsonl")
DEFAULT_JOURNAL = Path("state/predictions/journal.jsonl")
DEFAULT_ODDS_BOOKS = Path("state/predictions/odds_books.jsonl")
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
    parser = argparse.ArgumentParser(description="Run the MARKET-008 CLV validation study.")
    parser.add_argument("--predictions", default=str(DEFAULT_PREDICTIONS))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--odds-books", default=str(DEFAULT_ODDS_BOOKS))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--output-dir", default=str(DEFAULT_REPORT_DIR))
    args = parser.parse_args(argv)

    started = time.time()
    predictions_path = Path(args.predictions)
    journal_path = Path(args.journal)
    odds_books_path = Path(args.odds_books)
    daily_records = _read_jsonl(predictions_path)
    journal_records = _read_jsonl(journal_path)
    odds_records = _read_jsonl(odds_books_path)
    print(
        f"[load] predictions={len(daily_records)} journal={len(journal_records)} "
        f"odds_books={len(odds_records)}",
        flush=True,
    )

    try:
        report = build_clv_report(
            daily_records=daily_records,
            journal_records=journal_records,
            odds_records=odds_records,
            run_id=args.run_id,
            input_paths={
                "predictions": str(predictions_path),
                "journal": str(journal_path),
                "odds_books": str(odds_books_path),
            },
            model_version=args.model_version,
        )
    except ValueError as exc:
        print(f"[error] integrity failure: {exc}", file=sys.stderr, flush=True)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"clv-study-{args.run_id}.json"
    md_path = output_dir / f"clv-study-{args.run_id}.md"

    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=json_default) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    strict = report["aggregate"]["strict_close"]
    outcome = report["aggregate"]["outcome_all_latest_per_game"]
    verdict = report["verdict"]
    print(f"[report] latest_per_game={report['population']['n_latest_per_game']}", flush=True)
    print(f"[report] strict_clv_n={strict['clv_n']}", flush=True)
    print(f"[report] outcome_n={outcome['n_resolved']}", flush=True)
    print(f"[report] verdict={verdict['verdict']}", flush=True)
    print(f"[write] {json_path}", flush=True)
    print(f"[write] {md_path}", flush=True)
    print(f"[done] seconds={round(time.time() - started, 3)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
