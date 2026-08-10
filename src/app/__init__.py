"""Presentation layer for the daily board.

Business/model logic stays out of this package's Streamlit modules: view models
are pure functions over stored PIPE-001 prediction records and must not
re-derive feature or market calculations.
"""
