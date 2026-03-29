"""
Brain Monitor — Neural network status, council activity, AEP proposals
Surfaces: Sessions 1 (memory + health), 2 (cortex debates), 3 (calibrator), 4 (trainer), 5 (AEP)
"""
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone

symbol = st.session_state.get('selected_symbol', 'ITC.NS')
regret_engine = st.session_state.get('regret_engine')

# ══════════════════════════════════════
# TAB STYLING
# ══════════════════════════════════════
st.markdown("""
<style>
    /* Fix cropped top banner */
    .block-container { padding-top: 2rem !important; }

    .stTabs [data-baseweb="tab-list"] { gap: 12px; }
    .stTabs [data-baseweb="tab"] {
        background: rgba(30, 41, 59, 0.6);
        border: 1px solid rgba(148,163,184,0.15);
        border-radius: 8px;
        padding: 10px 22px;
        font-weight: 700;
        font-size: 14px;
    }
    .stTabs [aria-selected="true"] {
        background: rgba(99,102,241,0.2) !important;
        border-color: #6366f1 !important;
        color: #818cf8 !important;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="alert-banner">
    <span class="status-live"></span> BRAIN MONITOR • Neural Intelligence Cluster
</div>
""", unsafe_allow_html=True)

# ══════════════════════════════════════
# SYSTEM HEARTBEAT / TIMER
# ══════════════════════════════════════
try:
    from market_agent.data.storage.postgres import PostgresStorage
    from sqlalchemy import text

    # Scout interval (autonomous_scout default); used for "next scan" estimate
    SCAN_INTERVAL_MINUTES = 10
    # Last *signal stored* (proxy for last scanner activity)
    db = PostgresStorage()
    with db.engine.connect() as conn:
        result = conn.execute(text("SELECT MAX(created_at) FROM signal_predictions")).scalar()

    last_scan = result if result else datetime.now()
    if hasattr(last_scan, 'tzinfo') and last_scan.tzinfo:
        last_scan = last_scan.replace(tzinfo=None)
    diff = (datetime.now() - last_scan).total_seconds() / 60
    next_scan_in = max(0, SCAN_INTERVAL_MINUTES - (diff % SCAN_INTERVAL_MINUTES))
    # Target time for live countdown (Path A Phase 6)
    now = datetime.now(timezone.utc)
    target_countdown = (now + timedelta(minutes=next_scan_in)).replace(tzinfo=timezone.utc)

    color = "#10b981" if diff < (SCAN_INTERVAL_MINUTES + 2) else "#ef4444"
    status_text = "ONLINE" if diff < (SCAN_INTERVAL_MINUTES + 2) else "DELAYED"

    st.markdown(f"""
    <div style="display: flex; gap: 20px; margin-bottom: 20px; font-family: monospace; background: #0f172a; padding: 10px 20px; border-radius: 8px; border: 1px solid #1e293b;">
        <div>
            <span style="color: #94a3b8; font-size: 12px;">SYSTEM STATUS</span><br>
            <span style="color: {color}; font-weight: bold; font-size: 16px;">● {status_text}</span>
        </div>
        <div>
            <span style="color: #94a3b8; font-size: 12px;">LAST SIGNAL</span><br>
            <span style="color: #e2e8f0; font-size: 16px;">{diff:.1f}m ago</span>
        </div>
        <div>
            <span style="color: #94a3b8; font-size: 12px;">NEXT SCAN (est.)</span><br>
            <span style="color: #6366f1; font-size: 16px;">~{next_scan_in:.0f}m</span>
        </div>
        <div style="flex-grow: 1; text-align: right;">
            <span style="color: #94a3b8; font-size: 12px;">DATA LATENCY</span><br>
            <span style="color: #e2e8f0; font-size: 16px;">~{int(diff*60)}s</span>
        </div>
    </div>
    """, unsafe_allow_html=True)
    # Live countdown ticker (Path A Phase 6): updates every second
    try:
        from market_agent.dashboard.components.countdown_ticker import render_countdown
        render_countdown("Next scan in", target_countdown, session_key="brain_monitor_next_scan")
    except Exception:
        pass
    st.caption("Last signal = latest prediction stored. Next scan assumes scout runs every 10m.")
except Exception as e:
    st.caption(f"Timer unavailable: {e}")

# ══════════════════════════════════════
# TABS
# ══════════════════════════════════════
tab_health, tab_brains, tab_council, tab_training, tab_interact = st.tabs([
    "📊 Health", "🧠 Brain Cluster", "⚡ Live Council", "🏋️ Training", "💬 Talk to Brains"
])

# ══════════════════════════════════════
# GET HEALTH MONITOR (singleton)
# ══════════════════════════════════════
def _get_monitor():
    try:
        from market_agent.brain.health_monitor import get_health_monitor
        return get_health_monitor()
    except Exception:
        return None

monitor = _get_monitor()

# ══════════════════════════════════════
# TAB 0: HEALTH DASHBOARD (Area 1 Improvement)
# ══════════════════════════════════════
with tab_health:
    st.markdown("### 📊 Learning Loop Health Dashboard")
    st.caption("Real-time view of the SCAN → PREDICT → RESOLVE → LEARN → DEBATE → IMPROVE cycle.")

    # ── LEARNING LOOP STATUS ──
    _loop_stages = []
    _scan_ok = False
    _predict_ok = False
    _resolve_ok = False
    _debate_ok = False
    _learn_ok = False
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from sqlalchemy import text
        _hdb = PostgresStorage()
        with _hdb.engine.connect() as conn:
            # Last signal prediction time
            _last_pred = conn.execute(text("SELECT MAX(created_at) FROM signal_predictions")).scalar()
            # Total predictions
            _pred_count = conn.execute(text("SELECT COUNT(*) FROM signal_predictions")).scalar() or 0
            # Resolved predictions
            _resolved = conn.execute(text("SELECT COUNT(*) FROM signal_predictions WHERE binary_win IS NOT NULL")).scalar() or 0
            # Last debate time
            _last_debate = conn.execute(text("SELECT MAX(created_at) FROM council_debates")).scalar()
            # Debate count (last 7 days)
            _debate_7d = conn.execute(text(
                "SELECT COUNT(*) FROM council_debates WHERE created_at > NOW() - INTERVAL '7 days'"
            )).scalar() or 0
            # Debate count total
            _debate_total = conn.execute(text("SELECT COUNT(*) FROM council_debates")).scalar() or 0

        _scan_ok = _last_pred is not None
        _predict_ok = _pred_count > 0
        _resolve_ok = _resolved > 0
        _debate_ok = _debate_total > 0
        _learn_ok = _debate_ok and _resolve_ok  # Learning requires both resolved signals and debates

        # Format timestamps
        _pred_ago = ""
        if _last_pred:
            _pred_dt = _last_pred.replace(tzinfo=None) if hasattr(_last_pred, 'tzinfo') and _last_pred.tzinfo else _last_pred
            _pred_ago = f"{(datetime.now() - _pred_dt).total_seconds() / 60:.0f}m ago"

        _debate_ago = "Never"
        if _last_debate:
            _deb_dt = _last_debate.replace(tzinfo=None) if hasattr(_last_debate, 'tzinfo') and _last_debate.tzinfo else _last_debate
            _debate_ago = f"{(datetime.now() - _deb_dt).total_seconds() / 3600:.1f}h ago"

    except Exception as e:
        st.warning(f"Could not query health data: {e}")

    # Flow diagram
    stages = [
        ("SCAN", _scan_ok, f"Last: {_pred_ago}" if _scan_ok else "No scans"),
        ("PREDICT", _predict_ok, f"{_pred_count} total" if _predict_ok else "0 predictions"),
        ("RESOLVE", _resolve_ok, f"{_resolved}/{_pred_count} resolved" if _resolve_ok else "0 resolved"),
        ("DEBATE", _debate_ok, f"{_debate_total} debates" if _debate_ok else "No debates"),
        ("LEARN", _learn_ok, "Active" if _learn_ok else "Waiting"),
    ]
    flow_html = '<div style="display: flex; gap: 6px; align-items: center; margin: 16px 0; flex-wrap: wrap;">'
    for i, (name, ok, detail) in enumerate(stages):
        bg = "rgba(16,185,129,0.15)" if ok else "rgba(239,68,68,0.15)"
        border = "#10b981" if ok else "#ef4444"
        icon = "✅" if ok else "⏳"
        flow_html += f'''
        <div style="flex:1; min-width:120px; background:{bg}; border:1px solid {border};
            border-radius:8px; padding:10px 12px; text-align:center;">
            <div style="font-size:18px;">{icon}</div>
            <div style="font-weight:800; color:#f1f5f9; font-size:13px;">{name}</div>
            <div style="font-size:10px; color:#94a3b8; margin-top:2px;">{detail}</div>
        </div>'''
        if i < len(stages) - 1:
            flow_html += '<span style="color:#475569; font-size:20px; font-weight:bold;">→</span>'
    flow_html += '</div>'
    st.markdown(flow_html, unsafe_allow_html=True)

    # ── DEBATE METRICS ROW ──
    col_d1, col_d2, col_d3, col_d4 = st.columns(4)
    with col_d1:
        st.metric("Total Debates", _debate_total if '_debate_total' in dir() else 0)
    with col_d2:
        st.metric("Last 7 Days", _debate_7d if '_debate_7d' in dir() else 0)
    with col_d3:
        st.metric("Last Debate", _debate_ago if '_debate_ago' in dir() else "Never")
    with col_d4:
        _queue_size = 0
        _cortex = st.session_state.get('cortex')
        if _cortex and hasattr(_cortex, 'debate_queue'):
            _queue_size = len(_cortex.debate_queue)
        st.metric("Queue Size", _queue_size)

    st.markdown("---")

    # ── PER-BRAIN ACCURACY WITH ALERTS ──
    st.markdown("#### 🧠 Per-Brain Accuracy & Alerts")

    _alert_brains = []
    _brain_names = ["Brain-v3 (Hybrid+Neural)", "AMV-LSTM", "Cross-Stock GNN", "RL Weighter",
                    "Multi-Timeframe", "Regime Ensemble", "Multi-Modal Fusion", "Causal Ensemble"]
    _brain_cards = []
    for _bname in _brain_names:
        _perf = regret_engine.get_real_accuracy(_bname) if regret_engine else {}
        _acc = _perf.get('accuracy', 0)
        _total = _perf.get('total', 0)
        _status = _perf.get('status', 'NO_DATA')
        _trend = _perf.get('trend', '-')
        if _total >= 5 and _acc < 45:
            _alert_brains.append((_bname, _acc, _total))
        _brain_cards.append((_bname, _acc, _total, _status, _trend))

    # Show alerts for underperforming brains
    if _alert_brains:
        for _ab_name, _ab_acc, _ab_total in _alert_brains:
            st.error(
                f"🚨 **{_ab_name}** accuracy has dropped to **{_ab_acc:.1f}%** "
                f"({_ab_total} signals). Consider force-triggering a debate or retraining."
            )

    # Brain accuracy cards
    cols = st.columns(4)
    for i, (_bname, _acc, _total, _status, _trend) in enumerate(_brain_cards):
        with cols[i % 4]:
            if _total == 0 or _status in ("NO_DATA", "WARMING_UP"):
                _s_color = "#475569"
                _s_label = "NO DATA" if _total == 0 else f"WARMING ({_total})"
                _acc_text = "—"
            else:
                _s_color = "#10b981" if _acc >= 60 else "#f59e0b" if _acc >= 45 else "#ef4444"
                _s_label = "STRONG" if _acc >= 60 else "OK" if _acc >= 45 else "WEAK"
                _acc_text = f"{_acc:.1f}%"
            _trend_icon = "📈" if _trend == "improving" else "📉" if _trend == "declining" else "➡️"
            _short_name = _bname.replace("Brain-v3 (Hybrid+Neural)", "Signal Engine").replace("Cross-Stock ", "")
            st.markdown(f"""
            <div style="background: rgba(15,23,42,0.7); border: 1px solid {_s_color}33;
                border-radius: 10px; padding: 12px; margin-bottom: 10px;">
                <div style="font-weight:700; color:#e2e8f0; font-size:12px;
                    white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{_short_name}</div>
                <div style="font-size:22px; font-weight:800; color:{_s_color}; margin:4px 0;">{_acc_text}</div>
                <div style="display:flex; justify-content:space-between; font-size:10px; color:#94a3b8;">
                    <span>{_s_label}</span>
                    <span>{_trend_icon} {_trend}</span>
                </div>
                <div style="font-size:10px; color:#475569; margin-top:4px;">{_total} signals</div>
            </div>
            """, unsafe_allow_html=True)

    # ── HEALTH MONITOR TRIGGER CONDITIONS ──
    st.markdown("---")
    st.markdown("#### 🎯 Active Debate Trigger Conditions")
    if monitor:
        try:
            _triggers = monitor.get_debate_triggers(symbol=symbol)
            if _triggers:
                for _t in _triggers:
                    _t_topic = _t.get('topic', 'Unknown')
                    _t_urgency = _t.get('urgency', 'MEDIUM')
                    _u_color = "#ef4444" if _t_urgency == "HIGH" else "#f59e0b" if _t_urgency == "MEDIUM" else "#10b981"
                    st.markdown(f"""
                    <div style="background: rgba(15,23,42,0.5); border-left: 3px solid {_u_color};
                        padding: 8px 12px; margin-bottom: 6px; border-radius: 4px;">
                        <span style="color:{_u_color}; font-weight:700; font-size:11px;">{_t_urgency}</span>
                        <span style="color:#e2e8f0; font-size:12px; margin-left:8px;">{_t_topic}</span>
                    </div>
                    """, unsafe_allow_html=True)
                st.caption(f"{len(_triggers)} active trigger(s) for {symbol}. Debates auto-queue when scanner runs.")
            else:
                st.success("✅ No active debate triggers — all brains performing within normal parameters.")
        except Exception as _te:
            st.caption(f"Trigger check: {_te}")
    else:
        st.caption("Health monitor not available.")

    # ── ADD 3: BRAIN ACCURACY MATRIX (Dashboardcheck.md) ──
    # Shows which brains are reliable in which regimes.
    # Built from brain_predictions. Shows "building history" when empty.
    st.markdown("---")
    st.markdown("#### 📊 Brain × Regime Accuracy Matrix")
    st.caption(
        "Win rate per brain per market regime. "
        "Green ≥60% | Yellow ≥50% | Red <50% | Gray = no data. "
        "Requires resolved trades in `brain_predictions`."
    )
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from sqlalchemy import text
        _storage_mat = PostgresStorage()
        with _storage_mat.engine.connect() as conn:
            mat_rows = conn.execute(text("""
                SELECT
                    brain_name,
                    COALESCE(regime, 'UNKNOWN')                             AS regime,
                    COUNT(*)                                                AS trades,
                    ROUND(
                        100.0 * SUM(CASE WHEN outcome = 'TARGET' THEN 1 ELSE 0 END)
                        / NULLIF(COUNT(*), 0), 1
                    )                                                       AS win_rate
                FROM brain_predictions
                WHERE outcome IN ('TARGET', 'SL', 'EXPIRED')
                GROUP BY brain_name, regime
                ORDER BY brain_name, regime
            """)).fetchall()

        if mat_rows:
            import pandas as pd
            df_mat = pd.DataFrame(mat_rows, columns=['Brain', 'Regime', 'Trades', 'Win Rate'])
            matrix = df_mat.pivot_table(
                index='Brain', columns='Regime',
                values='Win Rate', aggfunc='first'
            ).reset_index()

            def _color_cell(val):
                if pd.isna(val):  return 'color: #475569'
                if val >= 60:     return 'color: #10b981; font-weight: 700'
                if val >= 50:     return 'color: #f59e0b'
                return 'color: #ef4444; font-weight: 700'

            non_brain_cols = [c for c in matrix.columns if c != 'Brain']
            styled = matrix.style.applymap(_color_cell, subset=non_brain_cols)
            st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.info("🔵 Brain accuracy matrix will appear after the first resolved trades. Keep the scout running.")
    except Exception as _mat_e:
        st.caption(f"Accuracy matrix: {_mat_e}")


# ══════════════════════════════════════
# TAB 1: BRAIN CLUSTER
# ══════════════════════════════════════
with tab_brains:
    st.markdown("### Neural Network Cluster Status")

    # Get real brain weights (from DB first, fallback to monitor)
    brain_weights = {}
    try:
        from market_agent.learning.training_persistence import training_db
        _db_weights = training_db.get_brain_weights()
        brain_weights = {k: v.get('weight', 1.0) for k, v in _db_weights.items()}
    except Exception:
        if monitor:
            try:
                brain_weights = monitor.get_brain_weights()
            except Exception:
                pass

    # ── TRAINED BRAINS (from walk-forward training) ──
    trained_brains = {}
    symbol_accuracy = {}
    last_training = "Never"
    try:
        import json, os
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                'config', 'strategy_params.json')
        with open(cfg_path) as f:
            cfg = json.load(f)
        trained_brains = cfg.get('brain_confidence', {})
        symbol_accuracy = cfg.get('symbol_accuracy', {})
        last_training = cfg.get('last_training', 'Never')
        if isinstance(last_training, str) and 'T' in last_training:
            last_training = last_training.split('T')[0] + " " + last_training.split('T')[1][:5]
    except Exception:
        pass

    # ── NEW: REAL BRAIN VISUALIZATION (Phase 3+4) ──
    st.markdown("#### 🚀 Active Learning Brains (Causal + RL)")
    
    col_c, col_r = st.columns(2)
    
    # 1. Causal Graph Visualization
    with col_c:
        st.markdown("**🌐 Causal Network (Leader-Follower)**")
        st.caption("Shows which symbols lead or follow others (e.g. BTC → ETH). Data comes from `models/checkpoints/causal_graph.json` after causal model training.")
        try:
            import json, os
            # Ensure Graphviz binaries are findable even in subprocess environments
            _gv_bin = r"C:\Program Files\Graphviz\bin"
            if os.path.isdir(_gv_bin) and _gv_bin not in os.environ.get("PATH", ""):
                os.environ["PATH"] += os.pathsep + _gv_bin
            import graphviz  # pip install graphviz; also need Graphviz BINARIES (dot.exe) on system PATH

            models_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                    'models', 'checkpoints')
            causal_path = os.path.join(models_dir, 'causal_graph.json')

            if os.path.exists(causal_path):
                with open(causal_path) as f:
                    c_graph = json.load(f)

                graph = graphviz.Digraph()
                graph.attr(rankdir='LR', bgcolor='transparent')
                graph.attr('node', shape='box', style='filled', fillcolor='#1e293b',
                          color='#6366f1', fontcolor='#f8fafc', fontname='sans-serif')
                graph.attr('edge', color='#94a3b8', fontcolor='#94a3b8', fontsize='10')

                edge_count = 0
                for source, targets in c_graph.items():
                    for target, meta in targets.items():
                        if meta.get('strength', 0) > 0.1:
                            graph.edge(source, target, label=f"{meta.get('lag', 0)}h")
                            edge_count += 1

                if edge_count > 0:
                    st.graphviz_chart(graph)
                    st.caption(f"Showing {edge_count} significant lead-lag relationships.")
                else:
                    st.info("No significant causal links in graph yet (strength &lt; 0.1).")
            else:
                st.info("Causal graph not trained yet. Run causal model training to generate `causal_graph.json` in `models/checkpoints/`.")
        except ImportError:
            st.warning(
                "**Graphviz Python package missing.** Run: `pip install graphviz`. "
                "You also need the **Graphviz binaries** (e.g. from [graphviz.org](https://graphviz.org/download/) or `winget install Graphviz.Graphviz`). "
                "Add the folder containing `dot.exe` to your system PATH, then **restart your terminal and IDE** so the new PATH is picked up."
            )
        except Exception as e:
            err = str(e)
            if "ExecutableNotFound" in err or "graphviz" in err.lower() or "dot" in err.lower():
                st.warning(
                    "**Graphviz binaries not found.** Install the app from [Graphviz](https://graphviz.org/download/) and add its `bin` folder to system PATH. "
                    "**Restart your terminal/IDE** after changing PATH so the change is visible."
                )
            else:
                st.error(f"Graph error: {e}")

    # 2. RL Agent Weights Visualization
    with col_r:
        st.markdown("**⚖️ Dynamic RL Weights**")
        try:
            import torch
            import os
            import pandas as pd
            models_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 
                                    'models', 'checkpoints')
            rl_path = os.path.join(models_dir, 'rl_agent_v1.pth')
            
            if os.path.exists(rl_path):
                # We can't easily query the model without context, but we can load the last saved state 
                # or just show a placeholder explanation of what it does.
                # Ideally, we log the *current* weights from the scanner into DB.
                # For now, we will explain the policy.
                st.info("""
                **Policy Gradient Agent Active**
                
                This agent dynamically adjusts weights between:
                - **Technical Analysis (TA)**: trusted in chopped/ranging markets.
                - **Neural Network (NN)**: trusted in strong trends.
                - **Neutral Baseline**: used when uncertainty is high.
                
                *Real-time weights are logged in scanner output.*
                """)
                
                # Mockup of recent weights (replace with DB fetch if available)
                # data = pd.DataFrame({
                #    'Model': ['TA', 'Neural', 'Neutral'],
                #    'Weight': [0.45, 0.52, 0.03]
                # })
                # st.bar_chart(data.set_index('Model'))
            else:
                st.info("RL Agent not trained yet. Run `retrain_loop.py`.")
        except Exception as e:
            st.error(f"RL view error: {e}")

    st.markdown("---")

    if trained_brains:
        st.markdown("#### 🏆 Walk-Forward Trained Brains")
        st.caption(f"Last training: {last_training} • {cfg.get('training_candles', 0):,} candles processed")

        brain_info = {
            "SMA-Crossover": "Short MA(5) vs Long MA(20) — trend following",
            "RSI-Momentum": "RSI(14) oversold/overbought — mean reversion",
            "MACD-Signal": "MACD line crossing signal — momentum",
            "Bollinger-Bounce": "Bollinger Bands position — volatility",
            "Volume-Breakout": "Volume spike + direction — breakout",
            "Boss-Brain": "Majority vote of all 5 brains — ensemble",
        }

        cols = st.columns(3)
        for i, (brain_name, weight) in enumerate(trained_brains.items()):
            acc_pct = weight * 100
            s_color = "#10b981" if acc_pct >= 65 else "#f59e0b" if acc_pct >= 55 else "#ef4444"
            status = "STRONG" if acc_pct >= 65 else "DECENT" if acc_pct >= 55 else "WEAK"
            desc = brain_info.get(brain_name, "Analysis brain")

            with cols[i % 3]:
                st.markdown(f"""
                <div class="brain-card">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 800; color: #f1f5f9; font-size: 14px;">{brain_name}</span>
                        <span style="color: {s_color}; font-size: 10px; font-weight: 700;
                            background: rgba({'16,185,129' if s_color == '#10b981' else '245,158,11' if s_color == '#f59e0b' else '239,68,68'},0.15);
                            padding: 2px 8px; border-radius: 10px;">{status}</span>
                    </div>
                    <div style="margin-top: 12px; font-size: 11px; color: #94a3b8;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                            <span>Trained Accuracy</span>
                            <span style="color: {s_color}; font-weight: 700;">{acc_pct:.1f}%</span>
                        </div>
                        <div style="display: flex; justify-content: space-between; margin-bottom: 4px;">
                            <span>Confidence Weight</span>
                            <span style="color: #818cf8; font-weight: 600;">{weight:.3f}</span>
                        </div>
                    </div>
                    <div style="margin-top: 10px; height: 3px; background: #1e293b; border-radius: 2px;">
                        <div style="height: 3px; background: {s_color}; width: {max(5, acc_pct)}%;
                            border-radius: 2px;"></div>
                    </div>
                    <div style="margin-top: 6px; font-size: 10px; color: #475569;">{desc}</div>
                </div>
                """, unsafe_allow_html=True)

        # Per-symbol accuracy table
        if symbol_accuracy:
            st.markdown("#### 📊 Per-Symbol Accuracy (Walk-Forward)")
            sym_data = [{"Symbol": s, "Accuracy": f"{a:.1f}%",
                         "Rating": "🟢 Strong" if a >= 70 else "🟡 Decent" if a >= 60 else "🔴 Weak"}
                        for s, a in sorted(symbol_accuracy.items(), key=lambda x: x[1], reverse=True)]
            st.dataframe(pd.DataFrame(sym_data), hide_index=True)

        st.markdown("---")

    # Boss Brain Summary (global + per-model; signals are stored as Aegis-Signal-Engine so global = main accuracy)
    st.markdown("---")
    st.markdown("### 🎓 Boss Brain Unified Report")
    st.caption("Signals are produced by the Signal Engine (TA + analyst). Council brains (AMV-LSTM, etc.) contribute to debates; their accuracy here reflects any predictions tagged with that model.")
    st.caption("**WARMING_UP** = fewer than 5 resolved signals; accuracy is not statistically meaningful until then.")

    def _acc_display(total: int, accuracy: float, status: str) -> str:
        if total == 0:
            return "— (0)"
        if total < 5 or status == "WARMING_UP":
            return f"— ({total})"
        return f"{accuracy:.1f}% ({total})"

    report_data = []
    # Global accuracy (all predictions for selected symbol — the main metric)
    if regret_engine and regret_engine.signal_resolver:
        try:
            from market_agent.config import ACCURACY_STATS_LAST_N
            global_stats = regret_engine.signal_resolver.get_accuracy_stats(symbol=symbol, last_n=ACCURACY_STATS_LAST_N)
            total_g = global_stats.get('total', 0)
            report_data.append({
                "Brain": "Signal Engine (global)",
                "Accuracy": _acc_display(total_g, global_stats.get('accuracy', 0), global_stats.get('status', 'NO_DATA')),
                "Signals": total_g,
                "Status": global_stats.get('status', 'NO_DATA'),
                "Trend": global_stats.get('trend', '-'),
                "Weight": "1.000"
            })
        except Exception:
            pass
    for name in ["AMV-LSTM", "Cross-Stock GNN", "RL Weighter", "Multi-Timeframe", "Regime Ensemble", "Multi-Modal Fusion", "Causal Ensemble"]:
        perf = regret_engine.get_real_accuracy(name) if regret_engine else {"accuracy": 0, "total": 0, "status": "NO_DATA"}
        total_p = perf.get('total', 0)
        report_data.append({
            "Brain": name,
            "Accuracy": _acc_display(total_p, perf.get('accuracy', 0), perf.get('status', 'NO_DATA')),
            "Signals": total_p,
            "Status": perf.get('status', 'NO_DATA'),
            "Trend": perf.get('trend', '-'),
            "Weight": f"{brain_weights.get(name, 1.0):.3f}"
        })

    if report_data and any(d.get('Signals', 0) > 0 for d in report_data):
        st.dataframe(pd.DataFrame(report_data), hide_index=True)
    else:
        st.info("📊 **Boss Brain Report** builds after signals resolve. Use Command Center (resolution runs on load) or run the autonomous scanner to generate and resolve predictions.")

    # Calibration (Session 3)
    with st.expander("🎯 Calibration Formula — How Confidence is Calculated"):
        st.markdown("""
        <div class="signal-card" style="font-size: 12px; color: #94a3b8;">
            <div style="margin-bottom: 8px;"><b style="color: #818cf8;">Formula:</b>
                <code style="color: #f59e0b;">calibrated = max_prob × regime_reliability × brain_weight</code></div>
            <div>• <b>regime_reliability</b> = accuracy from SignalResolver per regime (5-min cache)</div>
            <div>• <b>brain_weight</b> = dynamic RL weight from Health Monitor (starts at 1.0)</div>
            <div>• <b>max_prob</b> = highest confidence among all 6 brains</div>
            <div style="margin-top: 8px; color: #64748b;">
                This formula ensures brains with poor track records in a specific regime
                get their confidence downgraded automatically. Weights adjust via reinforcement
                learning: +0.05 for correct, -0.075 for wrong.
            </div>
        </div>
        """, unsafe_allow_html=True)


# ══════════════════════════════════════
# TAB 2: LIVE COUNCIL (Session 2)
# ══════════════════════════════════════
with tab_council:
    st.markdown("### ⚡ Neural Council Debates")
    st.caption("When brain accuracy drops or brains disagree, the cortex triggers an AI debate. Results stored in DB.")

    try:
        from market_agent.data.storage.postgres import PostgresStorage
        storage = PostgresStorage()
        debates = storage.get_council_debates(symbol=None, limit=10)

        if debates:
            st.success(f"📡 {len(debates)} debates found — each generated live by Gemini AI")
            for d in debates:
                topic = d.get('topic', 'Market Analysis')
                verdict = d.get('verdict', 'N/A')
                created = d.get('created_at', '')
                sym = d.get('symbol', '')
                duration = d.get('duration', 0)
                if isinstance(created, str) and len(created) > 19:
                    created = created[:19].replace('T', ' ')
                elif hasattr(created, 'strftime'):
                    created = created.strftime('%Y-%m-%d %H:%M:%S')

                v_color = "#10b981" if "BUY" in str(verdict).upper() else \
                          "#ef4444" if "SELL" in str(verdict).upper() else "#6366f1"

                st.markdown(f"""
                <div class="brain-card">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-weight: 700; color: #e2e8f0; font-size: 13px;">📡 {topic}</span>
                        <span style="color: {v_color}; font-weight: 800; font-size: 14px;">{verdict}</span>
                    </div>
                    <div style="margin-top: 6px; display: flex; justify-content: space-between; font-size: 11px; color: #94a3b8;">
                        <span>{created} {f'• {sym}' if sym else ''}</span>
                        <span style="color: #818cf8; font-size: 9px; background: rgba(99,102,241,0.1);
                            padding: 2px 8px; border-radius: 10px;">🤖 AI GENERATED{f' • {duration:.0f}s' if duration else ''}</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                # Show full brain positions & reasoning (no truncation)
                parts = d.get('participants') or []
                if parts:
                    with st.expander(f"🧠 {len(parts)} Brain positions — click to expand", expanded=False):
                        for p in parts:
                            pos = p.get('position', '—')
                            brain = p.get('brain', '—')
                            reasoning = p.get('reasoning') or 'No reasoning recorded'
                            pos_color = "#10b981" if "BUY" in str(pos).upper() else \
                                       "#ef4444" if "SELL" in str(pos).upper() else "#818cf8"
                            st.markdown(f"""
                            <div style="background: rgba(15,23,42,0.5); border-left: 3px solid {pos_color};
                                padding: 8px 12px; margin-bottom: 8px; border-radius: 4px;">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <span style="font-weight: 700; color: #e2e8f0; font-size: 12px;">🧠 {brain}</span>
                                    <span style="color: {pos_color}; font-weight: 800; font-size: 12px;">{pos}</span>
                                </div>
                                <div style="margin-top: 6px; font-size: 11px; color: #cbd5e1; line-height: 1.5;">
                                    {reasoning}
                                </div>
                            </div>
                            """, unsafe_allow_html=True)
        else:
            st.info("""
            **No debates recorded yet.**
            
            Debates trigger when:
            1. You click **"Start Council Debate"** in the **Talk to Brains** tab (manual)
            2. Autonomous scanner runs and cortex queues a debate (accuracy drop, disagreement, news impact)
            
            Run `python -m market_agent.runner.autonomous_scout` or use **Talk to Brains → Start Council Debate** to create debates.
            """)
    except Exception as e:
        st.warning(f"Council debates: {e}")

    # Council Memory (Session 1 - FAISS)
    st.markdown("---")
    st.markdown("### 📚 Council Memory (FAISS)")
    try:
        from market_agent.brain.council_memory import CouncilMemory
        memory = CouncilMemory()

        query = st.text_input("🔍 Search debate memory", placeholder=f"e.g. {symbol} volatility reversal", key="memory_search")
        if query:
            results = memory.recall_similar(query, k=5)
            if results:
                for r in results:
                    mem_type = r.get('type', 'INSIGHT')
                    content = r.get('content', r.get('text', 'N/A'))
                    score = r.get('score', 0)
                    st.markdown(f"""
                    <div class="brain-card">
                        <div style="display: flex; justify-content: space-between;">
                            <span style="color: #818cf8; font-weight: 600; font-size: 11px;">{mem_type}</span>
                            <span style="color: #475569; font-size: 10px;">Relevance: {score:.2f}</span>
                        </div>
                        <div style="font-size: 12px; color: #cbd5e1; margin-top: 4px;">{content[:300]}</div>
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.caption("No matching memories. FAISS vectors populate as debates happen.")
        else:
            try:
                total = 0
                if memory.faiss and hasattr(memory.faiss, 'index') and memory.faiss.index is not None:
                    total = memory.faiss.index.ntotal
                st.caption(f"📦 {total} vectors stored. Search to find past debate insights. Vectors grow when debates are stored.")
            except Exception:
                st.caption("Enter a query to search debate memory.")
    except Exception as e:
        st.caption(f"Memory system: {e}")



# ══════════════════════════════════════
# TAB 4: TRAINING (Session 4)
# ══════════════════════════════════════
with tab_training:
    st.markdown("### 🏋️ Brain Training & RL Updates")

    st.markdown("#### Training Recommendations")

    # Use DB-persistent training approval instead of session_state
    try:
        from market_agent.learning.training_persistence import training_db
        _training_db_avail = True
    except ImportError:
        _training_db_avail = False
        if 'permanent_trained' not in st.session_state:
            st.session_state['permanent_trained'] = set()

    # Load accuracy threshold from config
    _accuracy_threshold = 55.0
    try:
        import json as _json, os as _os
        _cfg_path = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), 'config', 'strategy_params.json')
        with open(_cfg_path) as f:
            _cfg = _json.load(f)
        _accuracy_threshold = _cfg.get('default', {}).get('accuracy_threshold', 55.0)
    except Exception:
        pass

    if regret_engine and regret_engine.signal_resolver:
        try:
            proposals = regret_engine.signal_resolver.get_models_needing_training(accuracy_threshold=_accuracy_threshold)
            # Filter out already-approved (check DB first, then session_state fallback)
            if _training_db_avail:
                pending = [p for p in proposals if not training_db.is_training_approved(p['model'])] if proposals else []
            else:
                already_trained = st.session_state.get('permanent_trained', set())
                pending = [p for p in proposals if p['model'] not in already_trained] if proposals else []

            if pending:
                for p in pending:
                    st.markdown(f"""
                    <div class="signal-card" style="border-left: 3px solid #ef4444;">
                        <div style="font-weight: 700; color: #ef4444; margin-bottom: 4px;">⚠️ Training Needed: {p['model']}</div>
                        <div style="font-size: 11px; color: #94a3b8;">
                            Accuracy: {p['accuracy']}% | Threshold: {_accuracy_threshold}% | Reason: {p['reason']}
                        </div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"✅ Approve Training: {p['model']}", key=f"train_{p['model']}"):
                        if _training_db_avail:
                            training_db.approve_training(p['model'], reason=p.get('reason', ''),
                                                        old_accuracy=p.get('accuracy'))
                        else:
                            st.session_state['permanent_trained'].add(p['model'])
                        st.success(f"✅ Training approved for {p['model']}! Saved to database — will not repeat.")
                        st.rerun()
            elif proposals:
                st.success("✅ All training proposals have been approved.")
            else:
                st.success(f"✅ All brains operating within acceptable thresholds (>{_accuracy_threshold}% accuracy).")
        except Exception as e:
            st.caption(f"Training check: {e}")
    else:
        st.info("Training recommendations appear after the signal resolver has enough data to evaluate brain performance.")

    # Historical Training button
    st.markdown("---")
    st.markdown("#### 📊 Historical Training (Backtest)")
    st.caption("Run backtester on 6 months of data to optimize target/SL parameters.")

    _bt_symbols = st.text_input("Symbols (comma separated)", value=symbol, key="bt_symbols")
    _bt_strategy = st.selectbox("Strategy", ["Intraday (Scalp)", "Swing (Hold)"], key="bt_strategy")

    if st.button("🚀 Run Historical Training", key="run_backtest", type="primary"):
        with st.spinner("Running backtest optimization... (this may take a few minutes)"):
            try:
                from market_agent.training.backtester import backtester
                syms = [s.strip() for s in _bt_symbols.split(',')]
                result = backtester.train_and_save(syms, _bt_strategy)
                
                st.success(f"✅ Training complete! Best accuracy: {result.get('best_accuracy', 0):.1f}%")
                st.json(result.get('best_params', {}))

                if result.get('results'):
                    for sym, r in result['results'].items():
                        if isinstance(r, dict) and not r.get('error'):
                            st.markdown(f"**{sym}**: Acc={r.get('best_accuracy', 0):.1f}%, "
                                       f"Params={r.get('best_params')}")
            except Exception as e:
                st.error(f"Backtest failed: {e}")

    # RL Weight History
    st.markdown("---")
    st.markdown("#### RL Weight Update Log")
    st.caption("Weights adjust when council debates resolve: +0.05 for correct, -0.075 for wrong. If all show 1.0, run scanner or trigger a debate in Talk to Brains.")

    if brain_weights := (monitor.get_brain_weights() if monitor else {}):
        try:
            from market_agent.learning.training_persistence import training_db
            _db_w = training_db.get_brain_weights()
        except Exception:
            _db_w = {}

        for brain_name, w in brain_weights.items():
            db_info = _db_w.get(brain_name, {})
            db_weight = db_info.get('weight', w)
            db_acc = db_info.get('accuracy', 0)
            delta = db_weight - 1.0
            d_color = "#10b981" if delta >= 0 else "#ef4444"
            st.markdown(f"""
            <div style="display: flex; justify-content: space-between; padding: 6px 0;
                border-bottom: 1px solid rgba(148,163,184,0.08); font-size: 12px;">
                <span style="color: #e2e8f0; font-weight: 600;">{brain_name}</span>
                <span style="color: #818cf8;">Weight: {db_weight:.4f}</span>
                <span style="color: #94a3b8;">Acc: {db_acc:.1f}%</span>
                <span style="color: {d_color}; font-weight: 700;">
                    Δ {'+' if delta >= 0 else ''}{delta:.4f}
                </span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("Weights start at 1.0 and update when council debates resolve. Use **Talk to Brains → Start Council Debate** or run the autonomous scout to generate debates.")


# ══════════════════════════════════════
# TAB 5: TALK TO BRAINS (new!)
# ══════════════════════════════════════
with tab_interact:
    # Path A: Ensure Boss and manual debates have context — fetch current signal if last_analysis missing or wrong symbol
    _la = st.session_state.get('last_analysis') or {}
    if not _la or _la.get('symbol') != symbol:
        try:
            from market_agent.dashboard.pages.command_center import get_real_signal
            _strategy = st.session_state.get('strategy', 'Intraday (Scalp)')
            _sig = get_real_signal(symbol, _strategy)
            if _sig:
                _ta = _sig.get('tech_analysis') or {}
                _cons = _ta.get('consensus') if isinstance(_ta, dict) else {}
                if not _cons and _sig.get('direction') not in ('WAIT', None):
                    _cons = {'direction': _sig.get('direction', 'HOLD'), 'confidence': _sig.get('confidence', 0)}
                st.session_state['last_analysis'] = {
                    'symbol': symbol,
                    'price': _sig.get('current_price') or _sig.get('entry_price'),
                    'current_price': _sig.get('current_price') or _sig.get('entry_price'),
                    'confidence': _sig.get('confidence', 0),
                    'tech_analysis': dict(_ta) if isinstance(_ta, dict) else {},
                    'consensus': _cons,
                }
                if isinstance(st.session_state['last_analysis']['tech_analysis'], dict) and not st.session_state['last_analysis']['tech_analysis'].get('consensus') and _cons:
                    st.session_state['last_analysis']['tech_analysis'] = dict(st.session_state['last_analysis']['tech_analysis'])
                    st.session_state['last_analysis']['tech_analysis']['consensus'] = _cons
        except Exception:
            pass

    st.markdown("### 💬 Brain Interaction Panel")
    st.caption("Query individual brains or trigger a full council debate on any topic.")

    # Select brain
    brain_choice = st.selectbox("Select Brain", [
        "All (Full Council)", "AMV-LSTM", "Regime Ensemble", "Multi-Modal Fusion",
        "Multi-Timeframe", "Cross-Stock GNN", "RL Weighter"
    ], key="brain_select")

    question = st.text_area("Your question / analysis request",
        placeholder=f"e.g. What is the short-term outlook for {symbol}? Should we increase position size?",
        key="brain_question", height=80)

    if question and st.button("🚀 Ask Brain", key="ask_brain", type="primary"):
        with st.spinner(f"Querying {brain_choice}..."):
            try:
                from market_agent.brain.cortex import CortexGatekeeper
                cortex = CortexGatekeeper()
                
                if brain_choice == "All (Full Council)":
                    # Use cortex handle_user_query (works with or without Gemini)
                    result = cortex.handle_user_query(
                        query=question,
                        symbol=symbol,
                        analysis_context=st.session_state.get('last_analysis', {})
                    )
                    if result:
                        verdict = result if isinstance(result, str) else result.get('response', str(result))
                        st.markdown(f"""
                        <div class="signal-card" style="border-left: 3px solid #6366f1;">
                            <div style="font-weight: 700; color: #818cf8; margin-bottom: 8px;">🧠 Council Response</div>
                            <div style="color: #e2e8f0; font-size: 13px;">{verdict}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    else:
                        st.info("Council returned no response — try a more specific question.")
                else:
                    # Query specific brain via cortex
                    result = cortex.handle_user_query(
                        query=f"[{brain_choice}] {question}",
                        symbol=symbol,
                        analysis_context=st.session_state.get('last_analysis', {})
                    )
                    if result:
                        answer = result if isinstance(result, str) else result.get('response', str(result))
                        st.markdown(f"""
                        <div class="brain-card">
                            <div style="font-weight: 700; color: #818cf8; margin-bottom: 8px;">🤖 {brain_choice} Response</div>
                            <div style="color: #cbd5e1; font-size: 12px;">{answer}</div>
                        </div>
                        """, unsafe_allow_html=True)
                    # Also show health data if available
                    if monitor:
                        health = monitor._check_brain(brain_choice, symbol)
                        with st.expander(f"📊 {brain_choice} Health Data", expanded=False):
                            st.json(health)
            except Exception as e:
                st.error(f"Query failed: {e}")

    # Quick actions
    st.markdown("---")
    st.markdown("#### ⚡ Quick Actions")

    # Manual council debate trigger
    debate_topic = st.text_input("🎯 Trigger Manual Debate",
        placeholder=f"e.g. Should we change strategy for {symbol}?",
        key="manual_debate_topic")
    if debate_topic and st.button("⚡ Start Council Debate", key="manual_debate_btn", type="primary"):
        try:
            from market_agent.brain.cortex import CortexGatekeeper
            cortex = CortexGatekeeper()
            added = cortex.queue_debate(
                topic=debate_topic,
                symbol=symbol,
                trigger_type="user_manual",
                market_metrics=st.session_state.get('last_analysis', {}),
                urgency="HIGH"
            )
            if added:
                verdict = cortex.process_debate_queue(regret_engine=regret_engine)
                if verdict:
                    # Store for collapsible expander with max height
                    _parts_html = []
                    try:
                        from market_agent.data.storage.postgres import PostgresStorage
                        _stor = PostgresStorage()
                        _debates = _stor.get_council_debates(symbol=symbol, limit=1)
                        if _debates:
                            _d = _debates[0]
                            _parts = _d.get("participants") or []
                            for _p in _parts:
                                _pos = _p.get("position", "—")
                                _color = "#10b981" if _pos == "BUY" else "#ef4444" if _pos == "SELL" else "#6366f1"
                                _r = str(_p.get('reasoning', ''))[:120].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                                _parts_html.append(f"<div style='margin-bottom:6px;'><b>{_p.get('brain', '—')}</b>: <span style='color:{_color}; font-weight:700;'>{_pos}</span> — {_r}...</div>")
                    except Exception:
                        pass
                    st.session_state["last_debate_verdict"] = verdict
                    st.session_state["last_debate_parts_html"] = _parts_html
                    st.success(f"Council verdict: {verdict}")
                else:
                    st.info("Debate queued but no verdict yet — brains are deliberating.")
            else:
                st.warning("Debate not queued — topic may be duplicate or queue is full.")
        except Exception as e:
            st.error(f"Debate failed: {e}")

    # Collapsible debate result (max height, scrollable)
    if st.session_state.get("last_debate_verdict"):
        with st.expander("Council verdict & brain positions", expanded=True):
            verdict = st.session_state["last_debate_verdict"]
            st.markdown(f"**Verdict:** {verdict}")
            parts_html = st.session_state.get("last_debate_parts_html") or []
            if parts_html:
                inner = "".join(parts_html)
                st.markdown(f"<div style='max-height: 220px; overflow-y: auto; padding: 8px 0;'>{inner}</div>", unsafe_allow_html=True)
            st.caption("Council decided: " + str(verdict))

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🔍 Run Health Check", key="health_check"):
            if monitor:
                with st.spinner("Checking all brains..."):
                    report = monitor.full_health_check(symbol)
                    st.session_state["last_health_check"] = report
            else:
                st.warning("Health monitor not available.")
    with col2:
        if st.button("📊 Debate Triggers", key="debate_triggers"):
            if monitor:
                triggers = monitor.generate_debate_triggers(symbol)
                st.session_state["last_debate_triggers"] = triggers if triggers else ["No triggers — all brains aligned."]
            else:
                st.warning("Health monitor not available.")
    with col3:
        if st.button("📝 Brain Prompt Summary", key="prompt_summary"):
            if monitor:
                summary = monitor.get_brain_summary_for_prompt(symbol)
                st.session_state["last_prompt_summary"] = summary or ""
            else:
                st.warning("Health monitor not available.")

    # Collapsible outputs for the three buttons (max height, scrollable)
    def _esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    if st.session_state.get("last_health_check") is not None:
        with st.expander("Last Health Check", expanded=False):
            import json
            _j = json.dumps(st.session_state["last_health_check"], indent=2, default=str)
            st.markdown(f"<div style='max-height: 280px; overflow-y: auto;'><pre style='font-size: 11px; margin: 0;'>{_esc(_j)}</pre></div>", unsafe_allow_html=True)
    if st.session_state.get("last_debate_triggers") is not None:
        with st.expander("Last Debate Triggers", expanded=False):
            _t = st.session_state["last_debate_triggers"]
            _lines = "\n".join(str(x) for x in _t) if isinstance(_t, list) else str(_t)
            st.markdown(f"<div style='max-height: 200px; overflow-y: auto;'><pre style='font-size: 12px; margin: 0;'>{_esc(_lines)}</pre></div>", unsafe_allow_html=True)
    if st.session_state.get("last_prompt_summary"):
        with st.expander("Last Brain Prompt Summary", expanded=False):
            st.markdown(f"<div style='max-height: 260px; overflow-y: auto;'><pre style='font-size: 11px; white-space: pre-wrap; margin: 0;'>{_esc(st.session_state['last_prompt_summary'])}</pre></div>", unsafe_allow_html=True)
