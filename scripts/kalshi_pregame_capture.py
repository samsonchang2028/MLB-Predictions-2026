"""PIPE-006 - Kalshi pregame capture, near each game's own first pitch.

Deliberately NOT part of ``daily_predictions.py``'s once-daily batch fetch.
Kalshi's price is meaningful as a closing-style, near-game-time read, and a
15-game slate has first pitches spread across the whole day -- one shared
batch timestamp cannot be "near first pitch" for every game at once. This is
a *scheduling* problem, not just a code change (see
``docs/researcha/ops-001-automation-and-scheduling.md``'s arbitrage/live-
monitoring section, the same class of problem).

Scheduling mechanism (orchestrator-resolved, see the task file): this is a
standalone script meant to be invoked frequently (e.g. every 10-15 minutes)
by an external scheduler (cron / Windows Task Scheduler) -- registering that
scheduler is a later OPS-001/OPS-003 task, not this one. This script's own
job is to make one invocation's "capture whatever is due right now" logic
correct and idempotent:

- Each due game's `game_start_timestamp` is compared against `now` to decide
  whether it falls in the near-first-pitch capture window (see
  ``DEFAULT_WINDOW_START_MINUTES`` / ``DEFAULT_WINDOW_END_MINUTES`` below).
- A game already captured for this ``run_date`` (a `kalshi` row already
  exists in ``odds_books.jsonl``) is never re-captured, so running this
  script every 10-15 minutes does not repeatedly poll the same game.
- One Kalshi API call per invocation covers the whole slate (Kalshi has no
  per-game endpoint worth calling individually); it is the due-window check
  above -- not the fetch -- that gives each game its OWN near-first-pitch
  timestamp across the day's many invocations.

Failure isolation (PIPE-004 principle, applied to this script's own loop):
a Kalshi outage, a malformed payload, or one unmatched/ambiguous market must
never raise -- each failure is logged and skipped at the narrowest possible
scope (the whole run / one market / one game), same "comparison-only data
must never break anything else" boundary PIPE-004 established for The Odds
API's multi-book comparison fetch. This never touches `daily.jsonl`,
`market_probability`, or `edge` -- see ``build_kalshi_capture_records``.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import daily_predictions as dp  # noqa: E402
from ingestion.kalshi import KalshiDataError, ingest_kalshi_market_snapshots  # noqa: E402
from ingestion.kalshi.matching import (  # noqa: E402
    MATCHED,
    KalshiGameCandidate,
    KalshiMatchingError,
    _city_name_matches,
    kalshi_game_candidates_from_schedule,
    match_kalshi_market,
    normalize_kalshi_team_name,
)
from market import probability_to_american, snapshot_is_pregame_valid  # noqa: E402
from storage import connect_database  # noqa: E402

DEFAULT_DATABASE = Path("data") / "mlb.duckdb"
DEFAULT_ODDS_BOOKS_OUTPUT = Path("state") / "predictions" / "odds_books.jsonl"
DEFAULT_SERIES_TICKER = "KXMLBGAME"
KALSHI_API_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
KALSHI_BOOKMAKER = "kalshi"

# Near-first-pitch capture window, as minutes before first pitch. A game
# becomes "due" once `now` is between window_start (earliest) and
# window_end (latest, closest to first pitch) minutes before its own first
# pitch. Proposed default, documented per the task file (not silently
# invented): 60-15 minutes before first pitch. Rationale: (1) 60 minutes
# gives a genuinely "close to game time" read rather than a morning
# snapshot, matching Kalshi's closing-style value described in the task;
# (2) the 15-minute floor leaves buffer so a scheduler invoking this script
# every 10-15 minutes reliably lands at least one invocation inside the
# window before first pitch, and so a slightly-delayed run still captures a
# genuinely pregame price instead of missing the window entirely once the
# game starts. This is a starting default -- confirm/tune with the user
# once real Kalshi liquidity/timing behavior is observed.
DEFAULT_WINDOW_START_MINUTES = 60.0
DEFAULT_WINDOW_END_MINUTES = 15.0

SKIP_NO_LIQUIDITY = "no_liquidity"
SKIP_INCOMPLETE_SIDES = "incomplete_sides"
SKIP_NOT_PREGAME = "snapshot_not_before_first_pitch"
CAPTURED = "captured"


def load_schedule(database: str | Path, run_date: date) -> list[dict[str, Any]]:
    """Today's active regular-season slate: game_pk, first pitch, team identity."""
    with duckdb.connect(str(database), read_only=True) as connection:
        schedule = dp._dict_rows(
            connection,
            """
            SELECT game_pk, game_date, game_date AS game_start_timestamp, source_game_json
            FROM silver.games
            WHERE official_date = ?
              AND game_type = 'R'
              AND abstract_game_state IN ('Preview', 'Live')
            ORDER BY game_date, game_pk
            """,
            [run_date],
        )
    for row in schedule:
        row["game_start_timestamp"] = dp._utc_instant(row["game_start_timestamp"])
    return schedule


def already_captured_game_pks(
    path: Path, run_date: date, *, bookmaker: str = KALSHI_BOOKMAKER
) -> set[Any]:
    """``game_pk`` values already holding a ``kalshi`` row for this run_date.

    Reading the existing artifact (rather than tracking capture state
    separately) is what makes "must not fetch Kalshi data for a game more
    than once close to first pitch" hold across repeated invocations of this
    script, without a second state file.
    """
    if not path.exists():
        return set()
    run_date_str = str(run_date)
    captured: set[Any] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("bookmaker") == bookmaker and str(row.get("run_date")) == run_date_str:
            captured.add(row.get("game_pk"))
    return captured


def games_due_for_capture(
    schedule: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    already_captured: Iterable[Any],
    window_start_minutes: float = DEFAULT_WINDOW_START_MINUTES,
    window_end_minutes: float = DEFAULT_WINDOW_END_MINUTES,
) -> list[dict[str, Any]]:
    """Games whose own first pitch puts them in the near-first-pitch window right now.

    Each game gets its own due window based on its own ``game_start_timestamp``
    -- not one shared slate-wide timestamp -- so a 1pm getaway game and a
    7pm night game each become due at their own, different wall-clock time.
    """
    captured = set(already_captured)
    due: list[dict[str, Any]] = []
    for game in schedule:
        if game["game_pk"] in captured:
            continue
        minutes_until_first_pitch = (
            game["game_start_timestamp"] - now
        ).total_seconds() / 60.0
        if window_end_minutes <= minutes_until_first_pitch <= window_start_minutes:
            due.append(dict(game))
    return due


def fetch_kalshi_payload(*, series_ticker: str, timeout: float) -> Any:
    params = {"series_ticker": series_ticker, "status": "open"}
    url = KALSHI_API_URL + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "mlb-predictions-local-operator/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Kalshi API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Kalshi API request failed: {exc.reason}") from exc


def _latest_kalshi_markets(connection: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    """Read matching inputs back from Bronze (the current price per market).

    Reads from ``bronze.kalshi_market_snapshots`` after ingestion, like every
    other Bronze-layer consumer in this repo, rather than matching directly
    against the just-fetched raw payload in memory.
    """
    return dp._dict_rows(
        connection,
        """
        SELECT market_ticker, event_ticker, side, no_sub_title, occurrence_datetime,
               yes_bid, yes_ask, snapshot_timestamp
        FROM bronze.kalshi_market_snapshots
        QUALIFY row_number() OVER (
            PARTITION BY market_ticker ORDER BY snapshot_timestamp DESC
        ) = 1
        """,
    )


def _market_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    """Bronze row -> the raw-market-object shape ``match_kalshi_market`` expects.

    A pre-PIPE-006 row (``occurrence_datetime`` NULL, migrated additively)
    falls through to an empty string here, which ``match_kalshi_market``'s
    own required-field validation rejects with ``KalshiMatchingError`` --
    reusing its existing error path instead of a second explicit guard.
    """
    occurrence_datetime = row.get("occurrence_datetime")
    return {
        "ticker": row.get("market_ticker"),
        "event_ticker": row.get("event_ticker"),
        "yes_sub_title": row.get("side"),
        "no_sub_title": row.get("no_sub_title"),
        "occurrence_datetime": occurrence_datetime.isoformat() if occurrence_datetime else "",
    }


def _midpoint_probability(row: Mapping[str, Any]) -> float | None:
    """Bid/ask midpoint as this side's implied win probability, or ``None`` if unusable.

    Per ``docs/researcha/kalshi-integration.md`` Section 3: a Kalshi yes
    price already *is* a probability (no American-odds intermediate step);
    the midpoint is the standard bid/ask-market analog of stripping "vig"
    (here, spread) to get one point estimate. ``None`` covers both an
    entirely empty side of a thin MLB book (bid == ask == 0) and a
    degenerate 0/1 boundary that ``probability_to_american`` cannot convert.
    """
    probability = float((row["yes_bid"] + row["yes_ask"]) / 2)
    if probability <= 0.0 or probability >= 1.0:
        return None
    return probability


def build_kalshi_capture_records(
    market_rows: Sequence[Mapping[str, Any]],
    candidates: Sequence[KalshiGameCandidate],
    due_game_pks: Iterable[Any],
    *,
    run_date: date,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Match Bronze market rows to due games and build odds_books.jsonl records.

    Never raises: a bad/unmatched/ambiguous market row, a game missing one of
    its two Kalshi sides, a no-liquidity price, or a post-first-pitch
    snapshot is isolated to that one market/game and recorded in ``stats``,
    never allowed to stop any other game's capture (mirrors PIPE-004's
    comparison-data failure-isolation principle).
    """
    due = set(due_game_pks)
    candidates_by_pk = {candidate.game_pk: candidate for candidate in candidates}
    stats: Counter[str] = Counter()

    matched_by_game: dict[Any, list[Mapping[str, Any]]] = {}
    for row in market_rows:
        try:
            result = match_kalshi_market(_market_dict(row), candidates)
        except KalshiMatchingError:
            stats["match_errors"] += 1
            continue
        if result.status != MATCHED:
            # Same "unmatched_events.<reason>" shape as matching.py's own
            # summarize_match_results / daily_predictions.py's odds_stats.
            stats[f"unmatched_events.{result.reason}"] += 1
            continue
        if result.game_pk not in due:
            stats["skipped_not_due"] += 1
            continue
        matched_by_game.setdefault(result.game_pk, []).append(row)

    records: list[dict[str, Any]] = []
    for game_pk, rows in matched_by_game.items():
        candidate = candidates_by_pk[game_pk]
        record, reason = _game_capture_record(
            game_pk, rows, candidate, run_date=run_date, now=now
        )
        stats[reason] += 1
        if record is not None:
            records.append(record)
    records.sort(key=lambda record: record["game_pk"])
    return records, dict(stats)


def _game_capture_record(
    game_pk: Any,
    rows: Sequence[Mapping[str, Any]],
    candidate: KalshiGameCandidate,
    *,
    run_date: date,
    now: datetime,
) -> tuple[dict[str, Any] | None, str]:
    home_probability: float | None = None
    away_probability: float | None = None
    latest_snapshot: datetime | None = None
    for row in rows:
        probability = _midpoint_probability(row)
        if probability is None:
            return None, SKIP_NO_LIQUIDITY
        # `side` is Kalshi's bare city-style name (e.g. "Seattle"), not this
        # repo's full club name (e.g. "seattle mariners") -- word-subset
        # containment, same as match_kalshi_market's own team-pair check,
        # not equality.
        side_norm = normalize_kalshi_team_name(str(row["side"]))
        if _city_name_matches(side_norm, candidate.home_team_norm):
            home_probability = probability
        elif _city_name_matches(side_norm, candidate.away_team_norm):
            away_probability = probability
        snapshot_timestamp = row["snapshot_timestamp"]
        if latest_snapshot is None or snapshot_timestamp > latest_snapshot:
            latest_snapshot = snapshot_timestamp

    if home_probability is None or away_probability is None:
        # Kalshi issues two markets (one per team) per game; only one side
        # matched/was captured (e.g. thin-liquidity dropped the other).
        return None, SKIP_INCOMPLETE_SIDES

    # Point-in-time correctness (reusing snapshot_is_pregame_valid's
    # reasoning, per the task's critical correctness constraint): a Kalshi
    # price captured mid-game or post-game would be misleading to display as
    # a pregame comparison read, even though it never enters evaluate_pregame.
    # `now` (this capture run's own clock) stands in for "prediction_timestamp"
    # -- the instant this comparison read is actually being made.
    if not snapshot_is_pregame_valid(latest_snapshot, now, candidate.start_time):
        return None, SKIP_NOT_PREGAME

    try:
        home_american = probability_to_american(home_probability)
        away_american = probability_to_american(away_probability)
    except ValueError:
        return None, SKIP_NO_LIQUIDITY

    return (
        {
            "run_date": run_date,
            "game_pk": game_pk,
            "bookmaker": KALSHI_BOOKMAKER,
            "home_american": home_american,
            "away_american": away_american,
            "snapshot_timestamp": latest_snapshot,
            "source": KALSHI_BOOKMAKER,
        },
        CAPTURED,
    )


def _run_capture(args: argparse.Namespace) -> int:
    run_date = dp.parse_date(args.date)
    now = dp.parse_timestamp(args.now)

    schedule = load_schedule(args.database, run_date)
    print(f"[schedule] games={len(schedule)} run_date={run_date}", flush=True)
    if not schedule:
        print("[done] no active regular-season games found for date", flush=True)
        return 0

    captured = already_captured_game_pks(Path(args.odds_books_output), run_date)
    due = games_due_for_capture(
        schedule,
        now=now,
        already_captured=captured,
        window_start_minutes=args.window_start_minutes,
        window_end_minutes=args.window_end_minutes,
    )
    print(
        f"[due] games={len(due)} already_captured={len(captured)} "
        f"window=({args.window_end_minutes}, {args.window_start_minutes}) minutes-before-first-pitch",
        flush=True,
    )
    if not due:
        print("[done] no games due for a near-first-pitch Kalshi capture right now", flush=True)
        return 0

    if args.kalshi_json:
        payload = json.loads(Path(args.kalshi_json).read_text(encoding="utf-8"))
        print(f"[kalshi] loaded payload={args.kalshi_json}", flush=True)
    else:
        print(f"[kalshi] fetching series_ticker={args.series_ticker}", flush=True)
        try:
            payload = fetch_kalshi_payload(series_ticker=args.series_ticker, timeout=args.timeout)
        except RuntimeError as exc:
            # Comparison-only data: a total Kalshi outage must never fail
            # this run (or, since this is its own script, any other run) --
            # log and let the next scheduled invocation retry.
            print(f"[kalshi] fetch failed, skipping this run: {exc}", flush=True)
            return 0

    with connect_database(args.database) as connection:
        try:
            inserted = ingest_kalshi_market_snapshots(connection, payload)
        except KalshiDataError as exc:
            print(f"[kalshi] payload malformed, skipping this run: {exc}", flush=True)
            return 0
        print(f"[kalshi] bronze_inserted={inserted}", flush=True)
        market_rows = _latest_kalshi_markets(connection)

    candidates = kalshi_game_candidates_from_schedule(schedule)
    due_game_pks = {game["game_pk"] for game in due}
    records, stats = build_kalshi_capture_records(
        market_rows, candidates, due_game_pks, run_date=run_date, now=now
    )
    print(f"[match] {json.dumps(stats, sort_keys=True)}", flush=True)

    written = dp.append_jsonl_records(
        Path(args.odds_books_output),
        records,
        key_fields=("run_date", "game_pk", "bookmaker"),
        on_conflict="overwrite",
    )
    print(f"[odds-books] written={written} output={args.odds_books_output}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture each due game's Kalshi price once, close to that specific "
            "game's first pitch. Run this frequently (e.g. every 10-15 minutes) "
            "via an external scheduler (cron / Task Scheduler); this script only "
            "captures whatever is due on THIS invocation."
        )
    )
    parser.add_argument("--date", default=None, help="Official MLB date YYYY-MM-DD; default local today.")
    parser.add_argument("--database", default=str(DEFAULT_DATABASE))
    parser.add_argument("--odds-books-output", default=str(DEFAULT_ODDS_BOOKS_OUTPUT))
    parser.add_argument("--series-ticker", default=DEFAULT_SERIES_TICKER)
    parser.add_argument(
        "--kalshi-json", default=None, help="Optional saved Kalshi payload for offline replay/tests."
    )
    parser.add_argument(
        "--now", default=None, help="ISO timestamp with timezone; default now UTC (tests/replay)."
    )
    parser.add_argument("--window-start-minutes", type=float, default=DEFAULT_WINDOW_START_MINUTES)
    parser.add_argument("--window-end-minutes", type=float, default=DEFAULT_WINDOW_END_MINUTES)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    return _run_capture(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[error] {exc}", file=sys.stderr)
        raise SystemExit(1)
