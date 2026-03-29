"""
Performance — Signal accuracy, history, attribution, system report
Surfaces: Sessions 3 (attribution), 4 (signal resolver), 7 (market report)
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timezone

symbol = st.session_state.get('selected_symbol', 'ITC.NS')
regret_engine = st.session_state.get('regret_engine')

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
    <span class="status-live"></span> PERFORMANCE • Signal Accuracy &amp; System Health
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════
# ADD 2 — EV TRACKER (Dashboardcheck.md) — HEADLINE METRIC
# This is the most important number. It sits above all tabs.
# ══════════════════════════════════════
def _render_ev_summary():
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from sqlalchemy import text
        storage = PostgresStorage()
        with storage.engine.connect() as conn:
            row = conn.execute(text("""
                SELECT
                    COUNT(*)                                                                   AS total_trades,
                    SUM(CASE WHEN outcome = 'TARGET' THEN 1 ELSE 0 END)                       AS wins,
                    SUM(CASE WHEN outcome = 'SL'     THEN 1 ELSE 0 END)                       AS losses,
                    ROUND(AVG(CASE WHEN outcome='TARGET' THEN pnl_pct::numeric END), 3)        AS avg_win,
                    ROUND(AVG(CASE WHEN outcome='SL'     THEN pnl_pct::numeric END), 3)        AS avg_loss,
                    ROUND(((
                        SUM(CASE WHEN outcome='TARGET' THEN 1 ELSE 0 END)::numeric /
                        NULLIF(SUM(CASE WHEN outcome IN ('TARGET','SL') THEN 1 ELSE 0 END), 0)
                        * AVG(CASE WHEN outcome='TARGET' THEN pnl_pct::numeric END)
                    ) + (
                        SUM(CASE WHEN outcome='SL' THEN 1 ELSE 0 END)::numeric /
                        NULLIF(SUM(CASE WHEN outcome IN ('TARGET','SL') THEN 1 ELSE 0 END), 0)
                        * AVG(CASE WHEN outcome='SL' THEN pnl_pct::numeric END)
                    )), 4)                                                                      AS ev_per_trade
                FROM council_verdicts
                WHERE outcome IN ('TARGET', 'SL')
            """)).fetchone()

        total = row[0] or 0
        wins = row[1] or 0
        losses = row[2] or 0
        avg_win = float(row[3] or 0)
        avg_loss = float(row[4] or 0)
        ev = float(row[5] or 0) if row[5] is not None else None

        # ── Resolver health ─────────────────────────────────────────────
        with storage.engine.connect() as conn:
            res_row = conn.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE outcome IS NULL)  AS unresolved,
                    MIN(created_at) FILTER (WHERE outcome IS NULL) AS oldest_pending,
                    MAX(exit_timestamp)                      AS last_resolved
                FROM council_verdicts
            """)).fetchone()
        unresolved = res_row[0] or 0
        oldest_pending = res_row[1]
        last_resolved = res_row[2]

        if oldest_pending:
            age_h = (datetime.now(timezone.utc) - oldest_pending.replace(tzinfo=timezone.utc)).total_seconds() / 3600
            oldest_str = f"{age_h:.1f}h ago"
        else:
            oldest_str = "N/A"

        if last_resolved:
            lr_min = (datetime.now(timezone.utc) - last_resolved.replace(tzinfo=timezone.utc)).total_seconds() / 60
            last_res_str = f"{lr_min:.0f} min ago"
        else:
            last_res_str = "Never"

        st.caption(
            f"⏳ Resolver health — **Unresolved predictions:** {unresolved} │ "
            f"**Oldest:** {oldest_str} │ **Last resolved:** {last_res_str}"
        )

        if total == 0:
            st.info("🔵 No resolved council trades yet — Expected Value will appear after the first closed position.")
            return

        wr = wins / (wins + losses) if (wins + losses) > 0 else 0
        evidence = f"({wins + losses} closed trades)"
        if (wins + losses) < 50:
            evidence = f"⚠️ Small sample ({wins + losses} trades — need 50+ for statistical confidence)"

        ev_color = '#10b981' if (ev or 0) > 0 else '#ef4444'
        ev_label = '▲ POSITIVE EDGE' if (ev or 0) > 0 else '▼ NEGATIVE EDGE'

        mc1, mc2, mc3, mc4 = st.columns(4)
        with mc1:
            st.metric("Expected Value / Trade",
                      f"{ev:+.3f}%" if ev is not None else "N/A",
                      delta=ev_label)
        with mc2:
            st.metric("Win Rate", f"{wr:.1%}", delta=f"{wins}W / {losses}L")
        with mc3:
            st.metric("Avg Win", f"+{avg_win:.3f}%")
        with mc4:
            st.metric("Avg Loss", f"{avg_loss:.3f}%")
        st.caption(evidence)

        # Rolling cumulative EV chart
        with storage.engine.connect() as conn:
            rolling = conn.execute(text("""
                SELECT
                    DATE(exit_timestamp)            AS trade_date,
                    ROUND(AVG(pnl_pct)::numeric, 4) AS daily_avg_pnl,
                    COUNT(*)                        AS trades
                FROM council_verdicts
                WHERE outcome IN ('TARGET', 'SL')
                  AND exit_timestamp IS NOT NULL
                GROUP BY DATE(exit_timestamp)
                ORDER BY trade_date ASC
            """)).fetchall()

        if rolling:
            import plotly.graph_objects as go
            rdf = pd.DataFrame(rolling, columns=['Date', 'Daily Avg PnL', 'Trades'])
            rdf['Cumulative EV'] = rdf['Daily Avg PnL'].cumsum()
            line_color = '#10b981' if rdf['Cumulative EV'].iloc[-1] > 0 else '#ef4444'
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=rdf['Date'], y=rdf['Cumulative EV'],
                fill='tozeroy', line=dict(color=line_color, width=2),
                name='Cumulative EV'
            ))
            fig.update_layout(
                paper_bgcolor='#0f172a', plot_bgcolor='#0f172a',
                font=dict(color='#94a3b8'),
                title='Cumulative Expected Value Over Time',
                height=220, margin=dict(l=0, r=0, t=30, b=0),
                xaxis=dict(gridcolor='#1e293b'),
                yaxis=dict(gridcolor='#1e293b', zeroline=True, zerolinecolor='#475569'),
            )
            st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        st.caption(f"EV tracker unavailable: {e}")

_render_ev_summary()
st.markdown("---")

# ══════════════════════════════════════
# GLOBAL ACCURACY HERO METRIC
# ══════════════════════════════════════
perf = regret_engine.get_real_accuracy(symbol=symbol) if regret_engine else {}
accuracy = perf.get('accuracy', 0)
total = perf.get('total', 0)
wins = perf.get('wins', 0)
win_rate = perf.get('win_rate', 0) if total and total > 0 else 0
status = perf.get('status', 'NO_DATA')
trend = perf.get('trend') or '—'

currency = "$" if "-USD" in symbol else "₹"

col_acc, col_wr, col_sigs, col_trend = st.columns(4)
with col_acc:
    acc_color = "#10b981" if accuracy > 60 else "#f59e0b" if accuracy > 30 else "#64748b"
    st.markdown(f"""
    <div class="metric-card">
        <div class="data-label">Gradient Accuracy</div>
        <div class="data-value" style="color: {acc_color};">{accuracy:.1f}%</div>
        <div style="font-size: 10px; color: #64748b;">{status}</div>
    </div>
    """, unsafe_allow_html=True)
with col_wr:
    wr_color = "#10b981" if win_rate > 50 else "#ef4444" if total > 0 else "#64748b"
    wr_display = f"{win_rate:.1f}%" if total > 0 else "—"
    st.markdown(f"""
    <div class="metric-card">
        <div class="data-label">Win Rate</div>
        <div class="data-value" style="color: {wr_color};">{wr_display}</div>
        <div style="font-size: 10px; color: #64748b;">{wins}/{total} wins</div>
    </div>
    """, unsafe_allow_html=True)
with col_sigs:
    st.markdown(f"""
    <div class="metric-card">
        <div class="data-label">Total Signals</div>
        <div class="data-value">{total}</div>
        <div style="font-size: 10px; color: #64748b;">resolved</div>
    </div>
    """, unsafe_allow_html=True)
with col_trend:
    t_color = "#10b981" if trend == "IMPROVING" else "#ef4444" if trend == "DECLINING" else "#6366f1"
    t_icon = "📈" if trend == "IMPROVING" else "📉" if trend == "DECLINING" else "➡️"
    trend_display = trend if trend and trend != "—" else ("STABLE" if total > 0 else "—")
    st.markdown(f"""
    <div class="metric-card">
        <div class="data-label">Trend</div>
        <div class="data-value" style="color: {t_color};">{t_icon}</div>
        <div style="font-size: 10px; color: #64748b;">{trend_display}</div>
    </div>
    """, unsafe_allow_html=True)

if total == 0:
    st.info("📊 **Building Signal History**: Accuracy metrics populate after the scanner stores predictions and resolves them against actual price moves. Run `scan.py` to start.")

st.markdown("---")

# ══════════════════════════════════════
# TABS
# ══════════════════════════════════════
tab_history, tab_regime, tab_brains, tab_attribution, tab_report = st.tabs([
    "📜 Signal History", "🎯 Regime Breakdown", "🧠 Per-Brain", "🔍 Attribution", "📊 System Report"
])

# ══════════════════════════════════════
# TAB 1: SIGNAL HISTORY (from DB)
# ══════════════════════════════════════
with tab_history:
    st.markdown("### Recent Signal Predictions")
    try:
        from market_agent.learning.signal_resolver import SignalPrediction
        from market_agent.data.storage.postgres import PostgresStorage
        storage = PostgresStorage()
        session = storage.Session()
        try:
            preds = session.query(SignalPrediction).filter(
                SignalPrediction.symbol == symbol
            ).order_by(SignalPrediction.created_at.desc()).limit(30).all()

            if preds:
                rows = []
                for p in preds:
                    # FIX 3 applied here too: outcome-based status labels
                    outcome = getattr(p, 'outcome', None)
                    if outcome == 'TARGET':      res_status = '✅ TARGET HIT'
                    elif outcome == 'SL':         res_status = '🔴 STOPPED OUT'
                    elif outcome == 'EXPIRED':    res_status = '⏰ EXPIRED'
                    elif p.is_resolved:           res_status = '✅ Resolved'
                    else:
                        age_h = (
                            (datetime.now(timezone.utc) - p.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                            if p.created_at else 0
                        )
                        res_status = '⚠️ STALE (>6h)' if age_h > 6 else '🟡 OPEN'
                    acc_val = p.accuracy_score
                    acc = f"{acc_val:.1f}%" if acc_val is not None else "—"
                    close_val = getattr(p, 'resolve_price', None)
                    close_str = f"{currency}{close_val:,.2f}" if close_val is not None and close_val > 0 else ("Pending" if not p.is_resolved else "—")
                    conf_val = p.confidence if p.confidence is not None else 0
                    conf_pct = (conf_val * 100) if conf_val <= 1 else conf_val
                    rows.append({
                        "Date": p.created_at.strftime("%m/%d %H:%M") if p.created_at else "—",
                        "Direction": p.direction,
                        "Entry": f"{currency}{p.entry_price:,.2f}" if p.entry_price else "—",
                        "Target": f"{currency}{p.target_1:,.2f}" if p.target_1 else "—",
                        "Stop": f"{currency}{p.stop_loss:,.2f}" if p.stop_loss else "—",
                        "Close Price": close_str,
                        "Confidence": f"{conf_pct:.0f}%",
                        "Accuracy": acc,
                        "Status": res_status,
                        "Regime": p.regime or "—",
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True)
            else:
                st.info(f"No signal predictions stored for **{symbol}** yet. Predictions are saved automatically when the scanner generates BUY/SELL signals.")
        finally:
            session.close()
    except Exception as e:
        st.warning(f"Signal history: {e}")

# ══════════════════════════════════════
# TAB 2: REGIME BREAKDOWN (FIX 2 — Dashboardcheck.md)
# Correct outcome-based win rate from council_verdicts.
# Replaces the old accuracy_score >= 50 logic.
# ══════════════════════════════════════
with tab_regime:
    st.markdown("### 🎯 Regime Breakdown (council_verdicts)")
    st.caption("Win = `outcome = 'TARGET'`. Loss = `outcome = 'SL'`. This is real trade outcomes, not confidence scores.")
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from sqlalchemy import text
        storage = PostgresStorage()
        with storage.engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT
                    COALESCE(bp.regime, 'UNKNOWN')                                      AS regime,
                    COUNT(DISTINCT cv.id)                                                AS total,
                    SUM(CASE WHEN cv.outcome = 'TARGET'  THEN 1 ELSE 0 END)             AS wins,
                    SUM(CASE WHEN cv.outcome = 'SL'      THEN 1 ELSE 0 END)             AS losses,
                    SUM(CASE WHEN cv.outcome = 'EXPIRED' THEN 1 ELSE 0 END)             AS expired,
                    ROUND(
                        100.0 * SUM(CASE WHEN cv.outcome = 'TARGET' THEN 1 ELSE 0 END)
                        / NULLIF(COUNT(DISTINCT cv.id), 0), 1
                    )                                                                    AS win_rate_pct,
                    ROUND(AVG(cv.pnl_pct::numeric), 3)                                  AS avg_pnl_pct,
                    ROUND(((
                        SUM(CASE WHEN cv.outcome = 'TARGET' THEN 1 ELSE 0 END)::numeric
                        / NULLIF(COUNT(DISTINCT cv.id), 0)
                        * AVG(CASE WHEN cv.outcome = 'TARGET' THEN cv.pnl_pct::numeric END)
                    ) + (
                        SUM(CASE WHEN cv.outcome = 'SL' THEN 1 ELSE 0 END)::numeric
                        / NULLIF(COUNT(DISTINCT cv.id), 0)
                        * AVG(CASE WHEN cv.outcome = 'SL' THEN cv.pnl_pct::numeric END)
                    )), 4)                                                                AS expected_value
                FROM council_verdicts cv
                LEFT JOIN (
                    SELECT DISTINCT council_session_id, regime
                    FROM brain_predictions
                    WHERE regime IS NOT NULL AND council_session_id IS NOT NULL
                ) bp ON cv.council_session_id = bp.council_session_id
                WHERE cv.outcome IS NOT NULL
                GROUP BY bp.regime
                ORDER BY total DESC
            """)).fetchall()

        if rows:
            df_regime = pd.DataFrame(rows, columns=[
                'Regime', 'Total', 'Wins', 'Losses', 'Expired',
                'Win Rate %', 'Avg PnL %', 'Expected Value'
            ])
            st.dataframe(df_regime, use_container_width=True, hide_index=True)
        else:
            st.info("🔵 No resolved council verdicts yet. Keep the scout running.")
    except Exception as e:
        st.warning(f"Regime breakdown: {e}")

# ══════════════════════════════════════
# TAB 2: PER-BRAIN BREAKDOWN
# ══════════════════════════════════════
with tab_brains:
    st.markdown("### Per-Brain Accuracy Breakdown")
    st.caption("Signals are stored under **Signal Engine** (TA + analyst). Council brains (AMV-LSTM, etc.) get rows when predictions are tagged with that model_id.")

    brain_data = []
    if regret_engine and regret_engine.signal_resolver:
        try:
            from market_agent.config import ACCURACY_STATS_LAST_N
            global_stats = regret_engine.signal_resolver.get_accuracy_stats(symbol=symbol, last_n=ACCURACY_STATS_LAST_N)
            if global_stats.get('total', 0) > 0:
                brain_data.append({
                    "Brain": "Signal Engine",
                    "Accuracy": f"{global_stats.get('accuracy', 0):.1f}%",
                    "Signals": global_stats.get('total', 0),
                    "Win Rate": f"{global_stats.get('win_rate', 0):.1f}%",
                    "Status": global_stats.get('status', 'NO_DATA'),
                    "Trend": global_stats.get('trend', 'STABLE'),
                })
        except Exception:
            pass
    brains = ["AMV-LSTM", "Regime Ensemble", "Multi-Modal Fusion", "Multi-Timeframe", "Cross-Stock GNN", "RL Weighter"]
    for brain in brains:
        perf_b = regret_engine.get_real_accuracy(model_name=brain) if regret_engine else {}
        total_b = perf_b.get('total', 0)
        brain_data.append({
            "Brain": brain,
            "Accuracy": f"{perf_b.get('accuracy', 0):.1f}%" if total_b > 0 else "Warming up",
            "Signals": total_b,
            "Win Rate": f"{perf_b.get('win_rate', 0):.1f}%" if total_b > 0 else "—",
            "Status": perf_b.get('status', 'NO_DATA'),
            "Trend": perf_b.get('trend') or "—",
        })
    st.dataframe(pd.DataFrame(brain_data), hide_index=True)

    if all(d.get('Signals', 0) == 0 for d in brain_data):
        st.info("Accuracy builds after the scanner stores and resolves predictions. Run Command Center (resolution on load) or the autonomous scout.")

    # Path A: Brain predictions table + points (who was right, +1 per correct)
    st.markdown("---")
    st.markdown("### Brain predictions & points (Path A)")
    st.caption("Recent resolved predictions per brain. **Points** = count of correct predictions (T1/T2 hit). Council row shows agreed decision when available.")
    if regret_engine and regret_engine.signal_resolver:
        try:
            points = regret_engine.signal_resolver.get_brain_points(symbol=symbol)
            streaks = regret_engine.signal_resolver.get_brain_streaks(symbol=symbol)
            recent = regret_engine.signal_resolver.get_recent_predictions_by_model(symbol=symbol, limit=40)
            if points:
                st.markdown("**Points (correct count):** " + " | ".join(f"{m}: **{c}**" for m, c in sorted(points.items(), key=lambda x: -x[1])))
            if streaks:
                st.markdown("**Streaks (correct in a row):** " + " | ".join(f"{m}: **{n}**" for m, n in sorted(streaks.items(), key=lambda x: -x[1])))
            if recent:
                rows = []
                for r in recent:
                    correct = "Yes (+1)" if r.get("binary_win") == 1 else "No"
                    exp = r.get("expires_at") or ""
                    if exp and len(exp) > 16:
                        exp = exp[:16].replace("T", " ")
                    tf = r.get("timeframe_min")
                    timeframe_str = f"{tf} min" if tf is not None else (f"Valid until {exp}" if exp else "—")
                    created = (r.get("created_at") or "")[:16].replace("T", " ") if r.get("created_at") else "—"
                    t1, sl = r.get("target_1"), r.get("stop_loss")
                    rows.append({
                        "Brain": r.get("model_id", "—"),
                        "Direction": r.get("direction", "—"),
                        "Entry": f"{r.get('entry_price', 0):,.2f}" if r.get("entry_price") is not None else "—",
                        "Target": f"{t1:,.2f}" if t1 is not None else "—",
                        "SL": f"{sl:,.2f}" if sl is not None else "—",
                        "Resolve": f"{r.get('resolve_price', 0):,.2f}" if r.get("resolve_price") is not None else "—",
                        "Timeframe": timeframe_str,
                        "Predicted": created,
                        "Correct?": correct,
                        "Accuracy": f"{r.get('accuracy_score', 0):.1f}%" if r.get("accuracy_score") is not None else "—",
                        "Resolved": (r.get("resolved_at") or "—")[:16],
                    })
                st.dataframe(pd.DataFrame(rows), hide_index=True)
            # Council decided row
            try:
                from market_agent.data.storage.postgres import PostgresStorage
                stor = PostgresStorage()
                verdict = stor.get_latest_council_verdict(symbol)
                if verdict:
                    st.markdown("**Council decided:** " + (
                        f"{verdict.get('direction', '—')} @ {verdict.get('entry_price', 0):,.2f} | "
                        f"T1: {verdict.get('target_1', 0):,.2f} | SL: {verdict.get('stop_loss', 0):,.2f}"
                        + (f" | Timeframe: {verdict.get('timeframe_min')} min" if verdict.get('timeframe_min') else "")
                    ))
                else:
                    st.caption("No council verdict stored for this symbol yet.")
            except Exception:
                pass
        except Exception as e:
            st.caption(f"Brain points / recent: {e}")
    else:
        st.caption("Regret engine not available.")

    # Wrong prediction log (Path A: recent resolved with accuracy < 50% or binary_win = 0)
    if regret_engine and regret_engine.signal_resolver:
        try:
            recent_all = regret_engine.signal_resolver.get_recent_predictions_by_model(symbol=symbol, limit=80)
            wrong = [r for r in recent_all if r.get("binary_win") == 0 or (r.get("accuracy_score") or 0) < 50]
            if wrong:
                with st.expander("❌ Wrong prediction log (accuracy < 50% or miss)", expanded=False):
                    st.caption("Resolved signals that missed target or scored below 50%. Used for RAG/attribution.")
                    rows = []
                    for r in wrong[:25]:
                        rows.append({
                            "Brain": r.get("model_id", "—"),
                            "Direction": r.get("direction", "—"),
                            "Entry": f"{r.get('entry_price', 0):,.2f}" if r.get("entry_price") else "—",
                            "Resolve": f"{r.get('resolve_price', 0):,.2f}" if r.get("resolve_price") else "—",
                            "Accuracy": f"{r.get('accuracy_score', 0):.1f}%" if r.get("accuracy_score") is not None else "—",
                            "Resolved": (r.get("resolved_at") or "—")[:16],
                        })
                    st.dataframe(pd.DataFrame(rows), hide_index=True)
        except Exception:
            pass

    # Boss Brain accuracy (compares council debate verdicts to actual outcomes)
    st.markdown("---")
    st.markdown("### 👑 Boss Brain Verdict Accuracy")
    st.caption("Compares past council debate verdicts (BUY/SELL) to actual price outcomes. Needs debates + resolved predictions.")
    try:
        from market_agent.brain.health_monitor import get_health_monitor
        health = get_health_monitor()
        boss_acc = health.get_boss_accuracy(last_n=30)
        total_boss = boss_acc.get('total', 0)
        if total_boss > 0:
            acc_boss = boss_acc.get('accuracy', 0)
            st.markdown(f"""
            <div class="metric-card" style="display: inline-block; margin-right: 16px;">
                <div class="data-label">Boss Verdict Accuracy</div>
                <div class="data-value">{acc_boss:.1f}%</div>
                <div style="font-size: 10px; color: #64748b;">{boss_acc.get('correct', 0)}/{total_boss} verdicts matched outcome</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("No verdicts to compare yet. Create debates in **Talk to Brains** and run the scout so predictions resolve.")
    except Exception as e:
        st.caption(f"Boss accuracy: {e}")

# ══════════════════════════════════════
# TAB 3: ATTRIBUTION (Session 3)
# ══════════════════════════════════════
with tab_attribution:
    st.markdown("### 🔍 Failure Attribution Analysis")
    st.caption("AI-powered root cause analysis when predictions fail. Uses FAISS to recall similar past failures.")

    try:
        from market_agent.learning.attribution import AttributionEngine
        analyzer = AttributionEngine()

        if hasattr(analyzer, 'get_recent_attributions'):
            attrs = analyzer.get_recent_attributions(symbol=symbol, limit=5)
            if attrs:
                for attr in attrs:
                    st.markdown(f"""
                    <div class="brain-card" style="border-left: 3px solid #ef4444;">
                        <div style="font-weight: 700; color: #ef4444; font-size: 13px; margin-bottom: 6px;">
                            ❌ {attr.get('failure_type', 'PREDICTION_MISS')}
                        </div>
                        <div style="font-size: 12px; color: #cbd5e1; margin-bottom: 4px;">
                            {attr.get('analysis', 'Analysis pending...')}
                        </div>
                        <div style="font-size: 10px; color: #64748b;">
                            Date: {attr.get('date', 'N/A')} | Brain: {attr.get('brain', 'N/A')}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.info("No failure attributions recorded yet. They generate automatically when predictions miss by >10%.")
        else:
            st.info("Attribution analysis runs automatically during signal resolution.")
    except Exception as e:
        st.caption(f"Attribution system: {e}")

    # Anomaly (Session 7)
    st.markdown("---")
    st.markdown("### ⚡ Anomaly Detection")
    try:
        from market_agent.utils.market_utils import check_anomaly
        import yfinance as yf
        df = yf.Ticker(symbol).history(period="5d", interval="1h")
        if df is not None and not df.empty:
            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            price_data = {
                "open": float(last.get("Open", 0)),
                "close": float(last.get("Close", 0)),
                "prev_close": float(prev.get("Close", 0)),
                "volume": float(last.get("Volume", 0)),
                "avg_volume": float(df["Volume"].mean()) if "Volume" in df.columns else 0,
            }
            anomaly = check_anomaly(price_data)
            if anomaly and anomaly.get('has_anomaly'):
                st.warning(f"⚠️ **{anomaly.get('anomaly_type', 'Unknown')}** ({anomaly.get('severity', 'N/A')}) — {anomaly.get('details', '')}")
            else:
                st.success("✅ No anomalies detected in recent price action.")
        else:
            st.caption("Price data unavailable for anomaly check.")
    except Exception as e:
        st.caption(f"Anomaly check: {e}")

# ══════════════════════════════════════
# TAB 4: SYSTEM REPORT (Session 7)
# ══════════════════════════════════════
with tab_report:
    st.markdown("### 📊 System Health Report")
    st.caption("Based on selected symbol and DB data. Regenerate refreshes from current state.")
    try:
        from market_agent.utils.market_utils import generate_market_report
        _storage = None
        if regret_engine and getattr(regret_engine, 'signal_resolver', None):
            _storage = getattr(regret_engine.signal_resolver, 'storage', None)
        report = generate_market_report(storage=_storage, symbol=symbol)

        if report:
            # Market Status
            markets = report.get('markets', {})
            mcols = st.columns(len(markets)) if markets else []
            for i, (mkt_name, mkt_data) in enumerate(markets.items()):
                with mcols[i]:
                    is_open = mkt_data.get('is_open', False)
                    st.markdown(f"""
                    <div class="metric-card">
                        <div class="data-label">{mkt_name}</div>
                        <div class="data-value" style="color: {'#10b981' if is_open else '#f59e0b'};">
                            {'🟢 OPEN' if is_open else '🔴 ' + mkt_data.get('status', 'CLOSED')}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)

            # Top performers
            top = report.get('top_performers', [])
            if top:
                st.markdown("#### 🏆 Top Brains")
                st.dataframe(pd.DataFrame(top), hide_index=True)

            # AI Narrative
            narrative = report.get('ai_narrative', '')
            if narrative:
                st.markdown("#### 🤖 AI Narrative")
                st.markdown(f"""
                <div class="brain-card" style="font-size: 12px; color: #cbd5e1; line-height: 1.6;">{narrative}</div>
                """, unsafe_allow_html=True)

            # Pending
            pending = report.get('pending_proposals', 0)
            if pending > 0:
                st.warning(f"📋 {pending} AEP proposals pending review — check Brain Monitor.")
        else:
            st.info("System report generates after the scanner completes at least one cycle.")
    except Exception as e:
        st.caption(f"Report: {e}")

    if st.button("🔄 Regenerate Report", key="regen_report"):
        st.rerun()
