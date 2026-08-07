# ADR-004: Historical Data Sources and Certification Gate

## Status

Accepted.

## Context

V1 needs a reproducible 2021-2025 historical baseball dataset before starter,
bullpen, feature-matrix, and model work can be trusted. Schedule-only MLB
ingestion is not enough for pitcher appearances or workload features.

Historical market evaluation also needs a stable odds source. The V1 source is
the `mlb_odds_dataset.json` asset from:

`https://github.com/ArnavSaraogi/mlb-odds-scraper/releases/tag/dataset`

Published SHA-256:

`3f952fd0bfae9f4f2d17e66692cb936ce6e1a5f6b415318012090c85933b882b`

## Decision

Use MLB Stats API data through the existing MLB StatsAPI integration/wrapper
pattern for historical baseball data. MLB `game_pk` remains canonical through
Bronze, Silver, Gold, features, predictions, and evaluation.

Target MLB coverage for V1 development is 2021, 2022, 2023, 2024, and 2025.
The smallest endpoint set that supports V1 should be used. Do not add Statcast
or pitch-level data unless a current V1 feature task requires it.

Bronze MLB responses remain immutable and must preserve source, endpoint,
request parameters, retrieval timestamp, `game_pk` where applicable, payload
hash, and ingestion run/build identity. Existing valid Bronze responses must
not be silently overwritten.

For historical market benchmarking, use opening moneyline odds from the
historical archive as the canonical V1 market benchmark. This supports claims
about model edge versus opening market only. It does not support claims about
the exact odds available at an arbitrary historical prediction timestamp.

Closing/current-style odds from the archive may be preserved and used for
separate post-hoc benchmarks, but they must not be represented as pregame
timestamp-valid odds unless the source data actually proves that timestamp.
Historical ROI from this archive must be labeled as simulated ROI at opening
prices and remain secondary to log loss, Brier score, and calibration.

Live/future odds remain timestamped snapshots. A live prediction must preserve
the exact odds snapshot used, and both the prediction timestamp and market
snapshot timestamp must be before the game start timestamp.

Add a formal historical data certification gate after MLB historical ingestion,
normalization, validation, and temporal/leakage checks. Certification is a
versioned artifact, not console output. A certification result is explicitly
`PASS` or `FAIL`. P0/P1 data findings and leakage failures are merge-blocking.

## Consequences

Feature and model tasks that depend on 2021-2025 historical baseball data are
not ready until the certified dataset build passes.

Historical odds archive ingestion and mapping/audit can proceed independently
from core baseball feature work where dependencies allow. It feeds market
evaluation later, but incomplete historical odds coverage must not block core
baseball model development.

No heavyweight metadata platform is introduced for V1. Certification artifacts
should use simple repository-versioned reports such as `state/data-certifications/`
or the closest existing convention.
