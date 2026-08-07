# Modeling Plan

## Prediction Target

Binary classification:

```text
home_win = 1 if home team wins
home_win = 0 if away team wins
```

The primary output is a probability, not a hard class label.

## V1 Model Families

### Logistic Regression
Purpose:

- simple baseline
- interpretable coefficient direction
- sanity check for feature signal

### Random Forest Classifier
Purpose:

- nonlinear comparison model
- interaction discovery
- relatively robust baseline tree ensemble

### XGBoost Classifier
Purpose:

- primary advanced tabular model candidate
- nonlinear interactions
- flexible regularization

## Training Philosophy

All model families must consume the same fold definitions for fair comparison.

Do not choose a model because it has the highest training accuracy.

## Probability Calibration

Calibration should be evaluated for each competitive model.

Candidate methods:

- Platt/sigmoid scaling
- isotonic regression

Calibration must be fit only on appropriate historical validation data, never on the final holdout.

## Ensemble Policy

No ensemble is required in V1.

After standalone models are evaluated, test either:

- soft voting
- stacking with a simple meta-model

Only keep an ensemble if it improves out-of-sample log loss, Brier score, or calibration consistently across folds.

## Market-Aware vs Baseball-Only Modeling

Prefer keeping two conceptual experiments:

### Baseball-only model
Uses baseball/context features without sportsbook market probability.

Question answered:

> How predictive is our baseball information by itself?

### Market-aware model
Adds no-vig market probability and possibly line movement later.

Question answered:

> Can our baseball information add predictive value beyond the market?

## Model Artifacts

Store:

- model binary
- model name/version
- feature version
- training dates/seasons
- training-window strategy
- hyperparameters
- calibration method
- evaluation summary
