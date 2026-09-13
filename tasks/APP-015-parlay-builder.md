# APP-015 — Parlay Builder Page

## Status

done

## Dependencies

- APP-007 (daily board row shape)
- APP-008 (best plays ranking pattern)
- MARKET-007 (shadow row enrichment)

## Goal

Add a Streamlit sidebar page that ranks parlay-eligible legs with a composite
score (not raw edge alone) and suggests 2/3/4-leg parlay combinations.
Display / entertainment only — not PLAY policy or model validation.

## Deliverables

- `src/app/parlay_builder.py` — leg scoring, policy filters, combo math
- `src/app/parlay_builder_page.py` — Streamlit UI
- `pages/8_Parlay_Builder.py` — multipage wrapper
- `tests/unit/app/test_parlay_builder.py`

## Usage

```bash
streamlit run streamlit_app.py
```

Open **Parlay Builder** from the sidebar. Defaults: Raw Model source, Balanced
rank, 2-leg parlays.

## Handoff

- 10 unit tests pass (`PYTHONPATH=src pytest tests/unit/app/test_parlay_builder.py -q`)
- No production PLAY threshold or pipeline changes
