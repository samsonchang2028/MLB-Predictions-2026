"""Run the ML-013 failure-regime and redundancy study (extends ML-012).

Operator entry point. Reuses ``rerun_repaired_experiment.py``'s Gold matrix
loader (identical certified inputs already used by ML-007/008/011/012),
``validation.odds_mapping`` (DATA-009 archive matching) and
``market.engine`` (MARKET-001 no-vig math) read-only for the edge-bucket
dimension, and re-reads the already-generated ML-012 report for fold-
concentration analysis (never reruns ablation itself). Never reads 2026.
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

import duckdb  # noqa: E402

from experiments.failure_regimes import compute_failure_regimes  # noqa: E402
from experiments.redundancy import (  # noqa: E402
    ablation_fold_concentration,
    missingness_report,
    pairwise_correlation_scan,
    per_season_coverage_stability,
)
from market.engine import no_vig_two_way  # noqa: E402
from rerun_repaired_experiment import _build_matrix, _load_inputs  # noqa: E402
from validation.odds_mapping import (  # noqa: E402
    MATCHED,
    build_archive_events,
    load_archive_records,
    load_game_candidates,
    map_archive_events,
)

DEFAULT_ML012_REPORT = Path("reports") / "experiments" / "ml-012-feature-ablation.json"
DEFAULT_FAILURE_OUTPUT = Path("reports") / "experiments" / "ml-013-failure-regimes.json"
DEFAULT_REDUNDANCY_OUTPUT = Path("reports") / "experiments" / "ml-013-redundancy.json"
_PREFERRED_SPORTSBOOK = "DraftKings"


def _market_home_probability_by_game_pk(
    connection: Any, valid_game_pks: set[int]
) -> dict[int, float]:
    """MATCHED-archive no-vig opening market P(home_win), restricted to ``valid_game_pks``.

    ``valid_game_pks`` is the certified matrix's own development (2021-2025)
    game_pk set, so even though the underlying DB may also contain 2026 games
    (per ML-010), only development-fold games can ever appear in the result --
    2026 is asserted absent below regardless.
    """
    records = load_archive_records(connection)
    candidates = load_game_candidates(connection)
    events = build_archive_events(records)
    mappings = {m.event_id: m for m in map_archive_events(events, candidates)}

    lines_by_event: dict[tuple[Any, int], list[dict[str, Any]]] = {}
    for record in records:
        key = (record["archive_date"], record["source_game_index"])
        lines_by_event.setdefault(key, []).append(record)

    result: dict[int, float] = {}
    for event_id, lines in lines_by_event.items():
        mapping = mappings.get(event_id)
        if mapping is None or mapping.status != MATCHED or mapping.game_pk is None:
            continue
        game_pk = mapping.game_pk
        if game_pk not in valid_game_pks:
            continue
        line = _select_line(lines)
        if line is None:
            continue
        home = line.get("opening_home_american")
        away = line.get("opening_away_american")
        if home is None or away is None:
            continue
        try:
            market = no_vig_two_way(home, away)
        except ValueError:
            continue
        result[game_pk] = market.no_vig_home_probability
    return result


def _select_line(lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Prefer DraftKings (matches ``scripts/daily_predictions.py``'s pin); else the
    alphabetically-first sportsbook with a usable opening pair, deterministically."""
    usable = [
        line
        for line in lines
        if line.get("opening_home_american") is not None
        and line.get("opening_away_american") is not None
    ]
    if not usable:
        return None
    for line in usable:
        if line.get("sportsbook") == _PREFERRED_SPORTSBOOK:
            return line
    return min(usable, key=lambda line: str(line.get("sportsbook")))


def _json_default(value: Any) -> str:
    return str(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ML-013 failure-regime and redundancy study on the repaired build."
    )
    parser.add_argument("--database", default="data/mlb.duckdb")
    parser.add_argument(
        "--certification",
        default=None,
        help="defaults to rerun_repaired_experiment.DEFAULT_CERTIFICATION",
    )
    parser.add_argument("--ml012-report", default=str(DEFAULT_ML012_REPORT))
    parser.add_argument("--failure-output", default=str(DEFAULT_FAILURE_OUTPUT))
    parser.add_argument("--redundancy-output", default=str(DEFAULT_REDUNDANCY_OUTPUT))
    parser.add_argument("--random-state", type=int, default=0)
    parser.add_argument("--correlation-threshold", type=float, default=0.9)
    args = parser.parse_args(argv)

    started = time.time()
    from rerun_repaired_experiment import DEFAULT_CERTIFICATION

    certification_path = Path(args.certification or DEFAULT_CERTIFICATION)

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

    valid_game_pks = {row["game_pk"] for row in matrix["rows"]}
    print("[ml-013] mapping historical odds archive for edge bucket", flush=True)
    with duckdb.connect(args.database, read_only=True) as connection:
        market_map = _market_home_probability_by_game_pk(connection, valid_game_pks)
    print(f"[ml-013] market-matched games={len(market_map)}", flush=True)

    print("[ml-013] computing failure-regime slices", flush=True)
    failure_regimes = compute_failure_regimes(
        matrix,
        market_home_probability_by_game_pk=market_map,
        random_state=args.random_state,
    )

    print("[ml-013] computing redundancy analysis", flush=True)
    correlation = pairwise_correlation_scan(matrix, threshold=args.correlation_threshold)
    missingness = missingness_report(matrix)
    coverage_stability = per_season_coverage_stability(matrix)

    ml012_path = Path(args.ml012_report)
    ml012_report = json.loads(ml012_path.read_text(encoding="utf-8"))
    fold_concentration = ablation_fold_concentration(ml012_report)

    failure_report = {
        "status": "ML013_FAILURE_REGIME_EVIDENCE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "build_id": matrix["build_id"],
        "certification_artifact": str(certification_path),
        "random_state": args.random_state,
        "n_rows": len(matrix["rows"]),
        "n_market_matched_games": len(market_map),
        "input_counts": input_counts,
        **failure_regimes,
        "runtime_seconds": round(time.time() - started, 3),
        "holdout_2026": "not inspected",
    }

    redundancy_report = {
        "status": "ML013_REDUNDANCY_EVIDENCE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "build_id": matrix["build_id"],
        "certification_artifact": str(certification_path),
        "n_rows": len(matrix["rows"]),
        "correlation_scan": correlation,
        "missingness": missingness,
        "per_season_coverage_stability": coverage_stability,
        "ablation_fold_concentration": {
            "source_report": str(ml012_path),
            **fold_concentration,
        },
        "runtime_seconds": round(time.time() - started, 3),
        "holdout_2026": "not inspected",
    }

    failure_output = Path(args.failure_output)
    failure_output.parent.mkdir(parents=True, exist_ok=True)
    failure_output.write_text(
        json.dumps(failure_report, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"[write] {failure_output}", flush=True)

    redundancy_output = Path(args.redundancy_output)
    redundancy_output.parent.mkdir(parents=True, exist_ok=True)
    redundancy_output.write_text(
        json.dumps(redundancy_report, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"[write] {redundancy_output}", flush=True)
    print(f"[done] seconds={round(time.time() - started, 3)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
