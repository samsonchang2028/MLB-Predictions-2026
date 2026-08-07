# Data-quality reports

Generated, repository-versioned data-quality artifacts (ADR-004: certification
is a versioned artifact, not console output).

## `odds_archive_coverage.json` (DATA-009)

Written by `validation.odds_mapping.write_coverage_report`. Deterministic,
sorted JSON describing historical odds-archive → MLB `game_pk` mapping coverage.

Shape:

```json
{
  "totals": {
    "events": 0,
    "MATCHED": 0,
    "UNMATCHED": 0,
    "AMBIGUOUS": 0,
    "matched_with_opening_pair": 0,
    "sportsbook_lines": 0
  },
  "by_season": { "2021": { "...": 0 } },
  "by_date": { "2021-04-01": { "...": 0 } },
  "by_sportsbook": {
    "DraftKings": { "lines": 0, "opening_pair_present": 0, "current_pair_present": 0 }
  }
}
```

- `by_season` / `by_date` buckets carry event status counts plus
  `matched_with_opening_pair` so missing opening lines and mapping gaps are
  visible per season and per date.
- `by_sportsbook` exposes per-book line volume and opening/current price
  presence so book-level gaps are auditable.

Only `MATCHED` events carry a canonical `game_pk`; `UNMATCHED` / `AMBIGUOUS`
events never do and must stay out of canonical opening-market evaluation
(MARKET-001). Opening prices support *model edge versus opening market* claims
only, not exact historical price at an arbitrary prediction timestamp
(ADR-002 / ADR-004).
