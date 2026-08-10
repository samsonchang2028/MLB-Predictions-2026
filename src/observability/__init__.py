"""Prediction journal and observability over immutable PIPE-001 records.

Original prediction fields are never mutated: result/outcome enrichment is stored
separately and joined by ``(game_pk, prediction_timestamp)``, preserving the
model version that produced each prediction.
"""
