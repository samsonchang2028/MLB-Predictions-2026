# Research: Weather as a Feature Family

Status: research only, no code changes. Written against the repo as of the
V1 model lock (ADR-006) and the current optional task graph (`tasks/index.md`).

## 0. Bottom line

Weather is a plausible, cheap, low-risk feature family to add, but its
expected marginal value for a **moneyline** (win-probability) model is
small — the mechanisms below act mostly on *scoring volume*, not on which
side wins. It is a better bet for a future totals/runs market than for
ADR-006's locked classifier. The implementation shape fits this repo well:
one small Bronze source, one static 30-row lookup table (same idiom as
`TEAM_ABBREVIATIONS` in `src/app/board.py`), and one feature builder that is
*simpler* than `starter.py`/`bullpen.py` because it has no rolling windows.
The main design wrinkle is that weather doesn't naturally fit the
home_/away_/diff_ column pattern `src/features/build.py` uses for
team-indexed components — see §4.

## 1. Data sources

This repo needs two different things from a weather provider, not one:

- **2021-2025 historical actuals** to backfill training data (one-time,
  bulk, ~30 parks x ~5 seasons x ~81 home games = ~12,000 game-datetime
  points).
- **Live/forecast data** at daily-operator run time for today's slate
  (small, incremental, ongoing — this is what `PIPE-002`/`PIPE-003` already
  do daily for odds).

| Option | Historical coverage | Cost | Rate limits | Notes |
|---|---|---|---|---|
| **Open-Meteo** (`archive-api.open-meteo.com` + `api.open-meteo.com/v1/forecast`) | ERA5 reanalysis back to 1940, hourly, global grid — covers 2021-2025 fully | Free, no API key, no signup for non-commercial use | ~10,000 req/day soft guidance, no hard-enforced limit for reasonable use; paid tier only needed for commercial/high-volume | Hourly variables include temperature, wind speed/direction/gusts, precipitation, humidity, pressure — exactly what's needed. Same provider does both historical archive and live forecast, so one client/module covers both DATA ingestion modes with one HTTP boundary. |
| **Visual Crossing** | Full historical + forecast in one unified "timeline" endpoint | Free tier: 1,000 records/day; pay-as-you-go $0.0001/record beyond that; subscription tiers from $35/mo | 1,000/day free ceiling | A backfill of ~12,000 historical game-datetime points would blow past the free daily cap in a single day (would need to spread the backfill over ~12 days, or pay a few dollars once via pay-as-you-go — trivially cheap either way). Nicer unified API, worse free-tier ergonomics for a one-time bulk backfill than Open-Meteo. |
| **NWS/NOAA API** (`api.weather.gov`) | Forecasts and recent observations only — **not** a historical archive; NOAA's separate NCEI/CDO service is the actual historical archive and has its own access pattern | Free | N/A | Ruled out as a single source: would need NCEI/CDO for backfill and a separate NWS call for live forecast — two integrations for what Open-Meteo does in one. Also US-only if MLB ever needed a non-US park (not currently a concern — all 30 parks are US/Canada). |
| **OpenWeatherMap** | Historical requires a paid "One Call by Timestamp" or bulk history add-on | Free tier is current/forecast only; historical is paid | — | No free path to 2021-2025 backfill. Not worth it when Open-Meteo gives the same data for free. |

**Recommendation: Open-Meteo for both historical and live/forecast.** It is
the only option that is free for both the bulk historical backfill and the
daily forecast fetch, needs no API key (nothing to add to secrets/config,
which matters given `OPS-001`'s "needs secret + data artifact strategy"
note), and exposes wind/temp/precip/humidity directly as hourly fields keyed
by lat/lon/time — a clean match for "park coordinates + first-pitch
datetime -> weather at that hour."

### Park coordinates as a static lookup table

There are exactly 30 MLB parks. This repo already has the identical
pattern for team abbreviations:

```python
# src/app/board.py
# ponytail: MLB's 30 team_ids are a fixed, unchanging set with no existing
# id->name lookup anywhere in the repo ... A static map is the whole solution
TEAM_ABBREVIATIONS: dict[int, str] = {108: "LAA", 109: "ARI", ...}
```

A `PARK_COORDINATES` (or `PARK_METADATA`, if roof type is bundled in) dict
keyed by `venue_id` or `home_team_id` — lat, lon, and `is_open_air: bool` —
is the same idiom, not overkill: fixed cardinality, no relationship to any
other table, changes only when a franchise physically relocates (rare, but
real: Tropicana Field's 2024 hurricane damage moved the Rays to Steinbrenner
Field, an open-air park, for 2025-2026). That relocation is exactly the
kind of thing a hardcoded table needs a human to notice and edit — same
maintenance burden `TEAM_ABBREVIATIONS` already accepts for team
relocations/expansion, not a new risk this feature introduces.

Roof-status nuance worth encoding explicitly rather than as a single
static bool: several parks (Rogers Centre, Minute Maid Park, Chase Field,
American Family Field, T-Mobile Park, loanDepot park, Globe Life Field) have
*retractable* roofs, so "domed" is not a fixed park property — it's a
per-game decision (open or closed) that in principle a Bronze weather/venue
fetch could capture from the MLB Stats API's game feed (`venue` /
weather-related fields already present in the game-detail payload
`src/ingestion/mlb/game_detail.py` fetches) rather than inferred from
outdoor conditions. If the per-game roof state isn't reliably available,
the static table's `is_open_air` should be read as "roof status unknown, no
correction for closed retractable roofs" and documented as a known
limitation, not silently treated as always-open.

## 2. Point-in-time correctness: forecast vs. actual

This is the sharpest correctness issue in this feature family, and it maps
directly onto a distinction this repo already enforces for market data.

`src/market/engine.py` labels every odds observation by vintage:

```python
class MarketLabel(enum.Enum):
    OPENING = "OPENING"    # historical archive opening line
    SNAPSHOT = "SNAPSHOT"  # live timestamped snapshot, valid pregame input
    CLOSING = "CLOSING"    # refused as pregame input unless genuinely at-close
```

...with the rule that a `SNAPSHOT` is only a valid pregame input when
`snapshot_timestamp < prediction_timestamp < game_start_timestamp`, and
`CLOSING` is refused as a live pregame input because it isn't knowable until
after the market moves.

Weather has the same two-vintage structure, but inverted in which vintage
is "safe":

- **Live/future predictions**: only a **forecast** issued strictly before
  the prediction timestamp is legitimate — exactly like `SNAPSHOT` odds.
  You cannot use the actual observed first-pitch weather for a game that
  hasn't happened yet; that's not a forecast, it's the future. A forecast
  also carries lead-time-dependent uncertainty (a forecast issued 5 days out
  is much less accurate than one issued 2 hours before first pitch) that
  actuals don't have.
- **Historical training data**: the model should learn from what
  *actually happened* at first pitch — the **observed/actual** conditions —
  not from what some forecast (right or wrong) predicted days earlier. This
  is the mirror image of the odds case: for odds, historical `OPENING`
  archive data is deliberately used for training/eval even though it isn't
  the tightest pregame snapshot, because it's what's available and
  consistently defined; for weather, "actual observed at game time" is both
  available (Open-Meteo's historical archive is reanalysis/observation-based,
  not a stored old forecast) and the more honest training signal, since it
  reflects the true physical conditions the game was played in.

The failure mode to avoid is exactly the one ADR-002/ADR-004 exist to
prevent for other feature families: silently training on ACTUAL weather
(a training-time-only privileged vintage) while serving FORECAST weather at
inference time, without ever stating the mismatch. That's not a leakage bug
in the strict sense (forecast weather is still knowable pre-game), but it is
a **train/serve skew** — the model at inference time sees a systematically
noisier version of the same signal it was trained on, degrading real
calibration relative to backtested calibration. The fix is the same
discipline this repo already applies to market data: tag every weather
observation with an explicit vintage (`ACTUAL` vs `FORECAST`) and a
timestamp, never silently mix them into one untagged column, and be honest
in docs/ADRs that backtested weather-feature performance uses a cleaner
input than production inference will get. If this ever matters enough,
folding some forecast noise into training (or using T-minus-N-hour
forecasts as of a fixed lead time, mirroring `OPENING`) would be the
principled fix, but that's future scope, not V1.

## 3. What weather plausibly affects, and how much to expect from it

Physically real, well-documented effects:

- **Wind direction/speed** — wind blowing out (especially at HR-friendly
  parks like Wrigley Field) measurably increases home run rate and total
  runs; wind blowing in suppresses it. This is a *scoring* effect.
- **Temperature** — warmer air is less dense, so batted balls carry
  further; higher temps modestly increase HR/scoring. Also a *scoring*
  effect, and generally smaller than wind.
- **Precipitation** — this repo already treats rain as a *data-quality/game-
  state* case, not a scoring input: `tasks/DATA-012-postponed-final-scores.md`
  and the certification/validation layer (`check_results_scores`,
  `detailed_state` handling in `src/validation/checks.py`) already exclude
  Postponed/Suspended/Cancelled games from score-validity checks. A weather
  feature builder should reuse that existing signal (a game that never
  happened at its scheduled time isn't a "windy game," it's a missing game)
  rather than trying to model rain's effect on gameplay.
- **Humidity** — smaller, more contested effect on ball carry; usually
  a minor covariate alongside temperature, not a strong standalone signal.

The important caveat for *this* model: all of the above are primarily
**runs/scoring** effects. A moneyline model predicts **who wins**, not how
many runs are scored. Weather shifting both teams' expected runs up or down
roughly symmetrically has a much weaker effect on *win probability* than on
*total runs* — extra home-run-friendly conditions help whichever team hits
more fly balls that day, which is not obviously correlated with which team
is favored to win. Public sabermetric work on weather effects consistently
finds them clearer and larger for totals/O-U markets than for the
moneyline. Do not expect weather features to move ADR-006's locked model's
log loss/Brier score much; the honest framing for a task write-up is "cheap
to add, plausible but likely small, worth an ablation before claiming
value."

Cross-reference: this repo has no totals/runs market yet (only the
moneyline is modeled/served, per `docs/decisions/ADR-006-v1-methodology-lock.md`
and `src/market/engine.py`'s home-win-probability framing). Weather is a
much stronger candidate feature family *if and when* a totals market is
built — worth flagging in that future task's research, not re-litigating
here.

## 4. Proposed architecture

### Bronze: `src/ingestion/weather/`

Mirrors the existing ingestion shape (`src/ingestion/mlb/game_detail.py`,
`src/ingestion/odds/historical.py` + `snapshots.py`): an injected
fetch-function boundary (`fetch_weather(params) -> bytes | None`, same shape
as `GameDetailFetcher`), raw JSON payloads written immutably via
`write_raw_payload` (same as every other Bronze source — no new storage
primitive needed), idempotent by `(venue_id_or_lat_lon, target_datetime,
vintage)`.

Two ingestion modes, split into two tasks the same way odds ingestion split
live snapshots (`DATA-003`) from the historical archive (`DATA-008`):

- **Historical backfill** — for each certified 2021-2025 `silver.games` row,
  look up the home park's lat/lon from the static table, call Open-Meteo's
  historical archive endpoint for the game's first-pitch hour, store the raw
  response tagged `vintage=ACTUAL`.
- **Live/forecast fetch** — for today's slate (source: the existing daily
  schedule/game-detail refresh path used by `PIPE-002`/`PIPE-005`), call
  Open-Meteo's forecast endpoint for each park with a game today, store the
  raw response tagged `vintage=FORECAST` with the fetch timestamp (this
  *is* the "snapshot timestamp" for point-in-time validity, exactly like an
  odds `SNAPSHOT`).

### Silver: normalized `silver.weather_observations`

One row per `(game_pk, vintage)` (`ACTUAL` or `FORECAST`, per §2), carrying
temperature, wind speed, wind direction, precipitation, humidity, the
static park's `is_open_air` flag, and the observation/forecast timestamp.
Join key is `game_pk` (this repo's canonical identity, per `AGENTS.md`) via
`silver.games.home_team_id` -> the static park table, not team/date (same
"team/date alone is not a safe unique key" rule `AGENTS.md` states for
doubleheaders/reschedules — a doubleheader's two games share a park but not
a first-pitch hour, so they get different weather rows keyed by `game_pk`).

### Gold: `src/features/weather.py`

Structurally **simpler** than `starter.py`/`bullpen.py`/`team.py`: those
build rolling/history-based features and must shift-before-roll because
each row's feature depends on *prior games'* outcomes. Weather has no
history dependency — it is a single per-game snapshot (what were the
conditions at this game's first pitch), so there is no window, no
chronological accumulation, and no "cold start" case to handle. The only
point-in-time rule that still applies is §2's forecast/actual vintage
discipline, enforced by which Silver row the caller passes in (historical
training pipelines pass `ACTUAL` rows; `PIPE-001`/`PIPE-002` inference
passes `FORECAST` rows) — the builder itself doesn't need to know which
vintage it received, it just needs the vintage to be labeled in the output
so it's traceable (mirroring `MarketLabel` on the market side).

**Design wrinkle**: `build_feature_matrix` (`src/features/build.py`)
composes `team`/`starter`/`bullpen` as `_COMPONENTS`, each indexed by
`(game_pk, team_id)`, producing `home_{component}_{key}` /
`away_{component}_{key}` / `diff_{component}_{key}` columns (see
`_feature_schema`/`_assemble_features`). Weather doesn't have a home/away
split — both teams play in the same park under the same conditions, so
forcing weather through that machinery would produce a duplicated
`away_weather_*` column identical to `home_weather_*` and a meaningless
`diff_weather_*` column that is always zero. That's not "reusing the
pattern," it's cargo-culting a shape that doesn't fit the data. The lazier,
correct option: keep `build_weather_features` keyed by `game_pk` only (one
row per game, not per team), and add a small game-level merge step in
`build_feature_matrix` that writes `game_weather_{key}` columns directly
onto each row's `features` dict once, alongside the existing
`home_/away_/diff_` columns — no new per-team split, no wasted zero-columns.
This is a real (if small) change to `build.py`, not something `weather.py`
can absorb alone; scope it as its own task (see §5) rather than quietly
expanding `FEAT-004`'s already-locked contract.

## 5. Proposed task breakdown

Follows `tasks/index.md`'s table style and this repo's `DATA-`/`FEAT-`
prefix convention. Next free numbers given the current graph: `DATA-021` is
already backlog (targeted retry task), so historical weather ingestion
starts at `DATA-022`; `FEAT-006` is the last used `FEAT-` id, so weather
features start at `FEAT-007`.

| Task | Status | Depends on | Notes |
|---|---|---|---|
| DATA-022 | backlog | DATA-007 | Bronze historical weather backfill: static 30-park lat/lon(+roof) table, Open-Meteo archive-API fetch keyed to each certified `silver.games` first-pitch hour, immutable raw JSON, `vintage=ACTUAL` |
| DATA-023 | backlog | PIPE-002, PIPE-003 | Bronze live/forecast weather ingestion: Open-Meteo forecast-API fetch for today's slate during the daily operator run, immutable raw JSON, `vintage=FORECAST` with fetch timestamp as snapshot time |
| DATA-024 | backlog | DATA-022, DATA-023 | Silver normalization: `silver.weather_observations` keyed by `(game_pk, vintage)`, park static table integration, explicit ACTUAL/FORECAST vintage + observation/forecast timestamp columns |
| FEAT-007 | backlog | DATA-024 | `src/features/weather.py`: point-in-time weather feature builder (temperature, wind speed/direction, precipitation, humidity, open-air/dome flag), one row per `game_pk`, vintage-labeled output |
| FEAT-008 | backlog | FEAT-007, FEAT-004 | Integrate weather into `build_feature_matrix` as game-level `game_weather_*` columns (no home/away/diff split — see §4); update Gold feature-coverage/completeness gate for the new component |

Not scoped here (explicitly out of scope per the task brief): a totals/runs
market to actually spend this feature family's strongest expected value on
(§3's cross-reference), and any ADR — if this graph is picked up, `FEAT-008`
touching `build.py`'s composition contract is the one change substantial
enough that an ADR update (or at least a note in
`docs/decisions/ADR-002-point-in-time.md`'s scope) is worth considering
during implementation planning, not decided here.

## Sources

- [Open-Meteo Historical Weather API docs](https://open-meteo.com/en/docs/historical-weather-api)
- [Open-Meteo Historical Forecast API docs](https://open-meteo.com/en/docs/historical-forecast-api)
- [Open-Meteo Features](https://open-meteo.com/en/features)
- [Visual Crossing Weather API](https://www.visualcrossing.com/weather-api/)
- [Visual Crossing pricing](https://www.visualcrossing.com/weather-data-pricing/)
- [Visual Crossing queryCost docs](https://www.visualcrossing.com/resources/documentation/weather-api/what-is-the-querycost-parameter/)
- [NWS historical-data limitations discussion](https://github.com/weather-gov/api/discussions/270)
- [NOAA NCEI Climate Data Online](https://www.ncei.noaa.gov/cdo-web/)
