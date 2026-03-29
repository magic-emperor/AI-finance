"""
Holdings — Portfolio & position analysis
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

symbol = st.session_state.get('selected_symbol', 'ITC.NS')
regret_engine = st.session_state.get('regret_engine')
position_active = st.session_state.get('position_active', False)
currency = "$" if "-USD" in symbol else "₹"


def _status_label(pred) -> str:
    """
    FIX 3 (Dashboardcheck.md): 5-state outcome label.
    Unresolved ≠ active. Could be expired and just not processed yet.
    """
    outcome = getattr(pred, 'outcome', None)
    if outcome == 'TARGET':  return '✅ TARGET HIT'
    if outcome == 'SL':      return '🔴 STOPPED OUT'
    if outcome == 'EXPIRED': return '⏰ EXPIRED'
    # Not yet resolved — check age
    if pred.created_at:
        try:
            ct = pred.created_at.replace(tzinfo=timezone.utc) if pred.created_at.tzinfo is None else pred.created_at
            age_hours = (datetime.now(timezone.utc) - ct).total_seconds() / 3600
        except Exception:
            age_hours = 0
    else:
        age_hours = 0
    if age_hours > 6:  return '⚠️ STALE (unresolved >6h)'
    return '🟡 OPEN'



# Tab styling
st.markdown("""
<style>
    .block-container { padding-top: 2rem !important; }
    .stTabs [data-baseweb="tab-list"] { gap: 12px; }
    .stTabs [data-baseweb="tab"] {
        background: rgba(30, 41, 59, 0.6);
        border: 1px solid rgba(148,163,184,0.15);
        border-radius: 8px;
        padding: 10px 22px;
        font-weight: 700;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(99,102,241,0.2) !important;
        border-color: #6366f1 !important;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="alert-banner">
    <span class="status-live"></span> HOLDINGS • Portfolio Analysis
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════
# ACTIVE POSITION
# ══════════════════════════════════════
if position_active:
    st.markdown("### 🛡️ Active Position")
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        d = t.history(period="1d", interval="1m")
        live = float(d['Close'].iloc[-1]) if d is not None and not d.empty else 0
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Symbol", symbol)
        with col2:
            st.metric("Live Price", f"{currency}{live:,.2f}")
        with col3:
            st.metric("Strategy", st.session_state.get('strategy', 'N/A'))
    except Exception as e:
        st.caption(f"Position data: {e}")
    st.markdown("---")

# ══════════════════════════════════════
# STORED SIGNALS
# ══════════════════════════════════════
st.markdown("### 📊 Signal Position Log")
st.caption("Past signals stored in the database. These represent theoretical entries/exits based on AI predictions.")

try:
    from market_agent.learning.signal_resolver import SignalPrediction
    from market_agent.data.storage.postgres import PostgresStorage
    storage = PostgresStorage()
    session = storage.Session()
    try:
        preds = session.query(SignalPrediction).filter(
            SignalPrediction.direction.in_(['BUY', 'SELL'])
        ).order_by(SignalPrediction.created_at.desc()).limit(50).all()

        if preds:
            rows = []
            for p in preds:
                c = "$" if "-USD" in (p.symbol or "") else "₹"
                rows.append({
                    "Symbol": p.symbol,
                    "Direction": p.direction,
                    "Entry": f"{c}{p.entry_price:,.2f}" if p.entry_price else "—",
                    "Target": f"{c}{p.target_1:,.2f}" if p.target_1 else "—",
                    "Stop": f"{c}{p.stop_loss:,.2f}" if p.stop_loss else "—",
                    "Confidence": f"{p.confidence:.0f}%",
                    "Strategy": p.strategy or "—",
                    "Status": _status_label(p),    # FIX 3: outcome-based 5-state label
                    "Age (h)": f"{((datetime.now(timezone.utc) - p.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600):.1f}" if p.created_at else "—",
                    "Accuracy": f"{p.accuracy_score:.1f}%" if p.accuracy_score is not None else "—",
                    "Date": p.created_at.strftime("%m/%d %H:%M") if p.created_at else "—",
                })

            st.dataframe(pd.DataFrame(rows), hide_index=True)

            # Summary stats
            resolved = [p for p in preds if p.is_resolved and p.accuracy_score is not None]
            if resolved:
                avg_acc = sum(p.accuracy_score for p in resolved) / len(resolved)
                wins = sum(1 for p in resolved if getattr(p, 'outcome', None) == 'TARGET')
                st.markdown(f"""
                <div style="display: flex; gap: 20px; margin-top: 12px;">
                    <div class="metric-card" style="flex: 1;">
                        <div class="data-label">Avg Accuracy</div>
                        <div class="data-value">{avg_acc:.1f}%</div>
                    </div>
                    <div class="metric-card" style="flex: 1;">
                        <div class="data-label">Win Rate</div>
                        <div class="data-value">{wins}/{len(resolved)}</div>
                    </div>
                    <div class="metric-card" style="flex: 1;">
                        <div class="data-label">Total Signals</div>
                        <div class="data-value">{len(preds)}</div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info("No position signals stored yet. Signals are logged as the scanner generates BUY/SELL calls.")
    finally:
        session.close()
except Exception as e:
    st.warning(f"Holdings data: {e}")

# ══════════════════════════════════════
# MULTI-SYMBOL OVERVIEW
# ══════════════════════════════════════
st.markdown("---")
st.markdown("### 🗂️ Multi-Symbol Overview")
st.caption("Quick snapshot of prediction activity across your watchlist.")

try:
    from market_agent.learning.signal_resolver import SignalPrediction
    from market_agent.data.storage.postgres import PostgresStorage
    from sqlalchemy import func
    storage = PostgresStorage()
    session = storage.Session()
    try:
        sym_stats = session.query(
            SignalPrediction.symbol,
            func.count(SignalPrediction.id).label('count'),
            func.avg(SignalPrediction.accuracy_score).label('avg_acc'),
        ).filter(
            SignalPrediction.is_resolved == True
        ).group_by(SignalPrediction.symbol).all()

        if sym_stats:
            overview = []
            for row in sym_stats:
                overview.append({
                    "Symbol": row.symbol,
                    "Signals": row.count,
                    "Avg Accuracy": f"{row.avg_acc:.1f}%" if row.avg_acc else "N/A",
                })
            st.dataframe(pd.DataFrame(overview), hide_index=True)
        else:
            st.info("Multi-symbol stats will appear after signals resolve across multiple symbols.")
    finally:
        session.close()
except Exception as e:
    st.caption(f"Multi-symbol: {e}")
