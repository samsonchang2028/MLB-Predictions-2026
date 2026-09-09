# MARKET-008 Context Pack — CLV Validation

## Mission

Validate whether model-market edge predicts positive closing line value (CLV)
before any PLAY policy promotion. Observability only.

## Diagnosis to preserve

- MODEL LAYER: healthy enough to monitor (ML-015)
- MARKET / PLAY LAYER: not production-ready (MARKET-006)
- 2% PLAY threshold: legacy display-only baseline
- ADR-007: PROPOSED, blocked — no policy passed gates
- NEXT EVIDENCE: does edge beat the closing line?

## Reuse (do not reinvent)

| Need | Module |
|------|--------|
| Latest-per-game board rows | `app.board.load_daily_board_with_diagnostics`, `_latest_row_per_game` |
| Side semantics | `market.play_policy`: `raw_model_side`, `market_side`, `edge_selected_side`, `is_crossover`, `model_market_agree` |
| No-vig from American | `market.engine.no_vig_two_way` |
| Timestamp parse | `app.board._parse_datetime` |
| Flat ROI | `app.dashboard_analytics.flat_stake_profit` |
| Report patterns | `market.policy_evaluation`: `sha256_file`, slice/bucket helpers, markdown report style |
| MARKET-006 CLI | `scripts/market_policy_study.py` |

## Artifact shapes

**daily.jsonl** (PIPE-001): `game_pk`, `run_date`, `model_probability`, `market_probability`, `edge`, `home_american`, `away_american`, `odds_snapshot_timestamp`, `prediction_timestamp`, `game_start_timestamp`, `source` (e.g. `the_odds_api:draftkings`)

**odds_books.jsonl** (PIPE-004): upserted one row per `(run_date, game_pk, bookmaker)` — **latest snapshot only**, not history. Fields: `bookmaker`, `home_american`, `away_american`, `snapshot_timestamp`, `source`.

**journal.jsonl**: join on `(game_pk, prediction_timestamp)`; `actual_home_win`, `correct`, scores.

## Closing-line source (in-repo only)

Use `odds_books.jsonl` matched to prediction `source` bookmaker (`draftkings` default).

Do **not** use historical archive closing lines for live prospective journal CLV without explicit timestamp semantics — archive is opening-benchmark methodology (ADR-004).

## Preflight counts (2026-09-09, orchestrator)

On current `state/predictions/`:

- 730 daily rows → 309 latest-per-game
- 2813 odds_books rows (9 bookmakers, 1 row per game/book/run_date)
- Strict CLV-eligible (pred < close <= start): **8 games**
- Close after first pitch: 129 (reject)
- Close ≤ prediction time: 171 (no movement)
- Missing DraftKings row: 1

**Expected verdict: CLV DATA INSUFFICIENT → create MARKET-009.**

Still implement full pipeline so CLV works when close snapshots exist.

## Integrity rules

- Exact `game_pk` joins only
- Prediction before first pitch
- Strict close: after prediction, at/before first pitch
- Reject close before prediction (strict population)
- Reject close after first pitch
- One row per game_pk in default summary
- CLV metrics: all games with valid close
- Outcome/ROI: resolved games only; pending excluded from outcome denominators

## Required slices

Edge buckets (coarse): 0-2, 2-4, 4-6, 6-8, 8+ pp; optional fine MARKET-006 buckets.

Crossover vs no-crossover; model-market agree/disagree; HOME/AWAY selected;
market favorite/underdog/near_even (`MARKET_NEUTRAL_BAND=0.02` from play_policy);
lead time <2h, 2-6h, 6-24h, 24h+; by bookmaker if multiple.

## Verdict cases

1. Positive CLV + positive ROI → continue shadow validation (do not promote)
2. Positive CLV + negative ROI → edge may contain info, policy not validated
3. Negative/flat CLV + negative ROI → edge not validated
4. Insufficient CLV data → CLV CANNOT BE EVALUATED; file MARKET-009

## Outputs

- `reports/market/clv-study-<run_id>.json`
- `reports/market/clv-study-<run_id>.md`
- `tasks/MARKET-009-prospective-closing-odds-capture.md` if Case 4

## Environment note

Some paths under `src/market/` and `scripts/` may be root-owned in this checkout.
Implementer should verify write access; escalate to operator if blocked.

## Tests to add

`tests/unit/market/test_clv.py` — side conversion, timestamps, dedup, bucket boundaries (2/4/6/8 pp), missing close, unresolved with CLV, ROI separation, no network.

## Commands (acceptance)

```bash
python scripts/market_clv_study.py \
  --predictions state/predictions/daily.jsonl \
  --journal state/predictions/journal.jsonl \
  --odds-books state/predictions/odds_books.jsonl \
  --run-id market-008

PYTHONPATH=src pytest tests/unit/market/test_clv.py -q
git diff --check
```
