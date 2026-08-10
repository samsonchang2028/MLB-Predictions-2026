"""Chronological (walk-forward) evaluation shared by all model families.

``splits`` defines deterministic expanding/rolling folds with no train/test
overlap and 2026 excluded from development; ``runner`` vectorizes the FEAT-004
matrix, fits a ``models.build_model`` estimator per fold (preprocessing inside
the fold), and scores probability quality (log loss, Brier, calibration).
"""
