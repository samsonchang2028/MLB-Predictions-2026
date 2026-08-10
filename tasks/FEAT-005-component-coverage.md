# FEAT-005 — Component Coverage Policy for the Game Feature Matrix

## Status

ready

## Dependencies

- FEAT-004

## Execution

Primary role: implementer
Review required: yes
Tester required: yes
Worktree required: yes

## Rationale

Discovered during the first real 2021-2025 experiment run. `build_feature_matrix`
raises when a regular-season game lacks a component row for either team:

```
ValueError: game_pk=632457 missing home bullpen features for team_id=144
```

In the certified build, 4 regular-season games have ZERO parsed pitcher
appearances, so neither team gets bullpen features (8 missing team-game keys; 4
also missing starter features):

- 632457 (2021-09-16)
- 746577 (2024-09-29)
- 746596 (2024-08-26)
- 746597 (2024-08-23)

This is consistent with `bronze.mlb_game_detail_payloads` = 14,518 vs
`silver.games` = 14,520. Today this hard-fails the whole matrix build, so no
feature matrix can be produced from the real certified data without an
out-of-band workaround.

## Goal

Decide and implement an explicit, auditable policy for games with incomplete
component coverage.

## Required classification (do this first)

For EACH of the four games, determine and document which it is:

- a **legitimate exclusion** (e.g. the game genuinely has no pitching line and
  never will),
- a **lifecycle/data-source edge case** (e.g. resumed/suspended lineage, a game
  whose detail lives under a different game_pk),
- or an **ingestion defect** (the payload exists upstream but we failed to fetch
  or parse it - in which case it belongs in DATA-016's scope, not here).

Record the finding per game_pk with evidence. Note that 2 of these games also lack
a `bronze.mlb_game_detail_payloads` row (14,518 payloads vs 14,520 games), so the
four are unlikely to share a single cause.

## Requirements

- The throwaway experiment-driver workaround (filtering these games out in an
  operational script) must NOT be preserved. Encode the correct behavior in
  production code.
- Do NOT silently drop games (AGENTS.md data rules).
- Choose ONE explicit policy per classification and document it in the module
  docstring:
  (a) emit the game with `None` component features (downstream vectorization
  already fills `NaN`, and model pipelines impute), or
  (b) exclude the game but RETURN the exclusions with `game_pk` + reason so the
  caller can report/count them.
- Either way the matrix result must expose the affected games (e.g. an
  `excluded` / `incomplete_components` list) so coverage is visible and testable.
- Preserve the existing strictness for genuinely inconsistent data (a component
  row present for one team but not the other of a covered game should remain an
  error if that indicates corruption rather than absent source detail).
- Keep the target isolated and all FEAT-004 guarantees intact.
- Add regression tests for the encoded behavior.

## Acceptance criteria

- Building the matrix over the real certified 2021-2025 data succeeds without
  out-of-band filtering.
- The 4 known games above are handled by the documented policy and are visible
  in the result.
- Tests cover: a game missing both teams' bullpen components, a game missing one
  team's component, and that covered games are unaffected.
