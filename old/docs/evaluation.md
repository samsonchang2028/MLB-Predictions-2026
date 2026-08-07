# Evaluation and Experiment Plan

## Why Random Splits Are Not Primary
MLB prediction is chronological. Random K-fold splits can leak later baseball environments into earlier training periods and produce unrealistic estimates.

Use walk-forward methods as the primary validation framework.

## Experiment Dimension 1: Model Family

- Logistic Regression
- Random Forest
- XGBoost

## Experiment Dimension 2: Training Window

### Expanding Window

```text
Train 2021       -> Test 2022
Train 2021-2022  -> Test 2023
Train 2021-2023  -> Test 2024
Train 2021-2024  -> Test 2025
```

Purpose: test whether accumulating all prior history improves generalization.

### Rolling 2-Season Window

```text
Train 2021-2022 -> Test 2023
Train 2022-2023 -> Test 2024
Train 2023-2024 -> Test 2025
```

Purpose: test whether recent data is more valuable than older MLB history.

### Rolling 3-Season Window

```text
Train 2021-2023 -> Test 2024
Train 2022-2024 -> Test 2025
```

Purpose: test a compromise between sample size and recency.

## Experiment Matrix

```text
                Logistic    Random Forest    XGBoost
Expanding          X             X              X
Rolling-2          X             X              X
Rolling-3          X             X              X
```

## Final Holdout

2026 YTD is the final untouched holdout.

Do not use 2026 to:

- choose features
- select model family
- select training window
- tune hyperparameters
- choose calibration method
- tune edge thresholds

After methodology selection, retrain according to the selected strategy through 2025 and evaluate once on 2026.

## Primary Metrics

### Log Loss
Primary probability-quality metric. Strongly penalizes confident wrong predictions.

### Brier Score
Measures squared error of predicted probabilities and is useful for calibration-oriented evaluation.

### Calibration
Use:

- reliability diagram
- probability buckets
- calibration slope/intercept if useful

### ROC-AUC
Secondary ranking/discrimination metric.

## Secondary Metrics

- accuracy
- precision by high-confidence buckets
- simulated ROI
- units
- max drawdown
- number of qualifying plays

ROI should never be the sole model-selection metric because it can be noisy over relatively small samples.

## Market Baseline

When historical/current odds data is available, compare every model against no-vig market probability.

Questions:

- Does model log loss beat market log loss?
- Does model Brier score beat market Brier score?
- Does model identify useful deviations from market price?

## Edge Analysis

Evaluate predictions by model-market edge buckets, e.g.:

```text
0-1%
1-2%
2-3%
3-4%
4-5%
5%+
```

Track sample size and uncertainty. Do not overinterpret small buckets.

## Experiment Output

Each experiment run should save a summary row containing:

- experiment_id
- model_name
- model_version
- feature_version
- training_window_strategy
- train period
- test period
- hyperparameters
- calibration method
- log_loss
- brier_score
- roc_auc
- accuracy
- optional ROI metrics
