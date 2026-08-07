# Streamlit App Plan

## Purpose
Provide a lightweight UI for daily predictions, model transparency, and experiment review.

The app is a presentation layer only. Core logic should remain in reusable Python modules.

## Page 1: Today's Board

Suggested columns:

- game
- scheduled time
- probable starters
- model home win probability
- model away win probability
- market no-vig home probability
- market no-vig away probability
- model edge
- recommendation/pass indicator
- prediction timestamp

## Page 2: Game Detail

Show:

- model probability
- market probability
- edge
- starting pitcher comparison
- bullpen comparison
- recent offense comparison
- rest/schedule comparison
- model version
- feature version
- prediction timestamp

## Page 3: Model Performance

Show:

- cumulative log loss
- Brier score
- ROC-AUC
- accuracy
- performance by season
- performance vs market baseline

## Page 4: Calibration

Show:

- reliability curve
- predicted probability buckets
- observed win percentage by bucket

## Page 5: Experiment Comparison

Table dimensions:

- model family
- training window strategy
- fold/test season
- log loss
- Brier score
- AUC
- optional simulated ROI

## Page 6: Prediction Journal

Search/filter:

- date
- team
- model version
- edge bucket
- recommendation
- result

## Optional Page: $1 Challenge

This is a fun side tracker and must remain separate from the core evaluation methodology.

Possible fields:

- participant
- date
- game
- model recommendation
- starting balance
- amount used
- result
- ending balance

The challenge should consume model outputs; it should never drive model changes.
