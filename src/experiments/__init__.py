"""Development validation experiments (2021-2025; 2026 never inspected).

Each experiment drives the ML-004 walk-forward runner across all three model
families for a training-window strategy and emits a common result schema
(fold-metric rows + a game_pk-keyed prediction table) for ML-007 to compare.
"""
