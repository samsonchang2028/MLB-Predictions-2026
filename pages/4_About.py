"""Streamlit multipage wrapper for the user-facing About/Methodology page."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.about import MODEL_SUMMARY, evidence_labels, methodology_sections, primary_metric_names


st.set_page_config(page_title="About the MLB Model", layout="wide")
st.title("About this model")
st.caption("Plain-English methodology notes for the MLB moneyline dashboard.")

st.subheader("Model identity")
st.write(MODEL_SUMMARY)

for section in methodology_sections():
    st.subheader(section.title)
    st.write(section.body)

st.subheader("How model quality is judged")
st.write(
    "The project treats probability quality as the primary evidence, not raw "
    "accuracy or simulated betting return."
)
st.markdown("\n".join(f"- {name}" for name in primary_metric_names()))

st.subheader("Evidence labels")
for evidence in evidence_labels():
    with st.expander(evidence.label):
        st.write(evidence.description)

st.info(
    "Daily win/loss summaries describe the displayed journal only. They do not "
    "update the locked V1 model and should not be read as proof of profitability."
)
