"""
Live countdown ticker — Path A Phase 6.
Shows a per-second countdown (e.g. "Next scan in 14:32" or "Exits in 09:05").
Uses st.fragment(run_every=1) so only this block reruns every second.
"""

import streamlit as st
from datetime import datetime, timezone


def _format_remaining(seconds: float) -> str:
    if seconds <= 0:
        return "0:00"
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@st.fragment(run_every=1)
def _ticker(session_key: str, label: str):
    ts = st.session_state.get(f"{session_key}_ts")
    lbl = st.session_state.get(f"{session_key}_label", label)
    
    if ts is None:
        return

    now_ = datetime.now(timezone.utc).timestamp()
    remaining = max(0, ts - now_)
    text = _format_remaining(remaining)
    
    color = "#ef4444" if remaining < 60 else "#6366f1"
    
    st.markdown(
        f"<div style='line-height:1.2;'>"
        f"<span style='color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:0.05em;'>{lbl}</span><br>"
        f"<span style='color:{color};font-size:20px;font-weight:700;font-family:monospace;'>{text}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

def render_countdown(
    label: str,
    target_utc: datetime,
    session_key: str = "countdown_ticker",
) -> None:
    now = datetime.now(timezone.utc)
    if target_utc.tzinfo is None:
        target_utc = target_utc.replace(tzinfo=timezone.utc)
    target_ts = target_utc.timestamp()
    st.session_state[f"{session_key}_ts"] = target_ts
    
    st.session_state[f"{session_key}_label"] = label

    _ticker(session_key, label)
