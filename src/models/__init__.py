"""V1 model families (probabilistic P(home_win) classifiers).

Each model module exposes a ``build_model(...)`` factory returning a
scikit-learn-compatible estimator (``fit(X, y)`` / ``predict_proba(X)``) plus
metadata, so the ML-004 walk-forward evaluator can drive every family uniformly.
"""
