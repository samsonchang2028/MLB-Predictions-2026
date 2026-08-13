# APP-003 - daily board display clarity

## Status

Completed.

## Role

Implementer.

## Scope

Improve the Streamlit daily prediction board display without changing model, market, or prediction-record semantics:

- display game/prediction/odds timestamps in Pacific time (`America/Los_Angeles`),
- add slate-date filtering so yesterday and today are not mixed by default,
- show the model-preferred side (`Model Side`) and a clearer `Action Label` such as `PLAY LAD` or `PASS`,
- preserve the caveat that PLAY/PASS is a synthetic display threshold, not a staking policy.

## Tests

Focused APP unit tests cover Pacific timestamp rendering, run-date helpers, and model-side/action-label logic.
