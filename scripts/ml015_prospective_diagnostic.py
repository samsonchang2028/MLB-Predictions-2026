"""ML-015 - run the prospective model/market diagnostic study.

Operator entry point. Reads the real, live production prediction journal
(``state/predictions/daily.jsonl`` + ``journal.jsonl`` + ``game_features.jsonl``
+ ``skipped.jsonl``), builds the ML-015 report via
``experiments.prospective_diagnostic.build_report`` (read-only; never
modifies journal/result files), compares against the locked 2026 holdout
report for display only, and writes
``reports/experiments/ml-015-prospective-diagnostic.json``.

``state/predictions/*.jsonl`` is gitignored (local/homelab-only runtime
data), so a worktree checkout will not have it -- pass explicit ``--daily``/
``--journal``/etc. paths pointing at the real repo checkout when running from
an isolated worktree.
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

from experiments.prospective_diagnostic import build_report, historical_comparison  # noqa: E402

DEFAULT_DAILY = Path("state") / "predictions" / "daily.jsonl"
DEFAULT_JOURNAL = Path("state") / "predictions" / "journal.jsonl"
DEFAULT_GAME_FEATURES = Path("state") / "predictions" / "game_features.jsonl"
DEFAULT_SKIPPED = Path("state") / "predictions" / "skipped.jsonl"
DEFAULT_HOLDOUT = Path("reports") / "experiments" / "v1-holdout-2026.json"
DEFAULT_OUTPUT = Path("reports") / "experiments" / "ml-015-prospective-diagnostic.json"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the ML-015 prospective diagnostic study.")
    parser.add_argument("--daily", default=str(DEFAULT_DAILY))
    parser.add_argument("--journal", default=str(DEFAULT_JOURNAL))
    parser.add_argument("--game-features", default=str(DEFAULT_GAME_FEATURES))
    parser.add_argument("--skipped", default=str(DEFAULT_SKIPPED))
    parser.add_argument("--holdout", default=str(DEFAULT_HOLDOUT))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--model-version", default=None, help="restrict to one model_version; default all")
    args = parser.parse_args(argv)

    started = time.time()
    daily_records = _read_jsonl(Path(args.daily))
    journal_records = _read_jsonl(Path(args.journal))
    game_features_records = _read_jsonl(Path(args.game_features))
    skipped_records = _read_jsonl(Path(args.skipped))
    print(
        f"[load] daily={len(daily_records)} journal={len(journal_records)} "
        f"game_features={len(game_features_records)} skipped={len(skipped_records)}",
        flush=True,
    )

    report = build_report(
        daily_records=daily_records,
        journal_records=journal_records,
        game_features_records=game_features_records,
        skipped_records=skipped_records,
        model_version=args.model_version,
    )
    print(f"[report] n_resolved_eligible_predictions={report['n_resolved_eligible_predictions']}", flush=True)

    holdout_path = Path(args.holdout)
    holdout_report = json.loads(holdout_path.read_text(encoding="utf-8"))
    comparison = historical_comparison(report["part1_all_predictions"], holdout_report)

    output = {
        "status": "ML015_PROSPECTIVE_DIAGNOSTIC_EVIDENCE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "daily": str(args.daily),
            "journal": str(args.journal),
            "game_features": str(args.game_features),
            "skipped": str(args.skipped),
            "holdout_2026": str(holdout_path),
        },
        **report,
        "historical_comparison_2026_holdout": comparison,
        "runtime_seconds": round(time.time() - started, 3),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[write] {output_path}", flush=True)
    print(f"[done] seconds={round(time.time() - started, 3)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
