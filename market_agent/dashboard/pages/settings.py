"""
Settings — Brain toggles, AI infrastructure, weight decay
"""
import streamlit as st
import pandas as pd
from datetime import datetime

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
    <span class="status-live"></span> SETTINGS • Configuration & Infrastructure
</div>
""", unsafe_allow_html=True)

tab_toggles, tab_infra, tab_decay, tab_jobs = st.tabs([
    "🎛️ Brain Toggles", "🤖 AI Infrastructure", "📉 Weight Decay", "🔧 Job Health"
])

# ══════════════════════════════════════
# TAB 1: ABLATION TOGGLES
# ══════════════════════════════════════
with tab_toggles:
    st.markdown("### 🎛️ Brain Ablation Toggles")
    st.caption("Enable/disable individual brains for A/B testing. Disabled brains are excluded from consensus.")

    brains = ["AMV-LSTM", "Regime Ensemble", "Multi-Modal Fusion", "Multi-Timeframe", "Cross-Stock GNN", "RL Weighter"]

    if 'toggles' not in st.session_state:
        st.session_state['toggles'] = {b: True for b in brains}

    cols = st.columns(3)
    for i, brain in enumerate(brains):
        with cols[i % 3]:
            enabled = st.toggle(brain, value=st.session_state['toggles'].get(brain, True), key=f"toggle_{brain}")
            st.session_state['toggles'][brain] = enabled
            status = "🟢 Active" if enabled else "🔴 Disabled"
            st.caption(status)

    active_count = sum(st.session_state['toggles'].values())
    if active_count < 3:
        st.warning("⚠️ Less than 3 brains active. Consensus quality may degrade.")
    else:
        st.success(f"✅ {active_count}/6 brains active")

    # Current Config
    st.markdown("---")
    st.markdown("#### Current Configuration")
    config_display = {
        "symbol": symbol,
        "strategy": st.session_state.get('strategy', 'N/A'),
        "asset_class": st.session_state.get('asset_class', 'N/A'),
        "brains_active": active_count,
        "position_active": st.session_state.get('position_active', False),
    }
    st.json(config_display)

# ══════════════════════════════════════
# TAB 2: AI INFRASTRUCTURE
# ══════════════════════════════════════
with tab_infra:
    st.markdown("### 🤖 AI Model Infrastructure")

    # AI client status (global singleton — used by cortex, analyst, daily report)
    try:
        from market_agent.brain.gemini_client import gemini_client
        stats = gemini_client.get_status() if hasattr(gemini_client, 'get_status') else {}
    except Exception:
        gemini_client = None
        stats = {}

    if gemini_client and stats:
        st.markdown("#### API Status & Rate Limits")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            avail = stats.get('available')
            st.metric("Status", "🟢 Online" if avail else "🔴 Offline")
        with col2:
            st.metric("Calls (this session)", stats.get('total_calls', 0))
        with col3:
            used = stats.get('calls_in_window', 0)
            max_rpm = stats.get('max_rpm', 1)
            st.metric("Rate limit (per min)", f"{used} / {max_rpm}")
        with col4:
            wait = stats.get('wait_seconds', 0) or 0
            if wait > 0:
                st.metric("Next restock in", f"{int(wait)}s")
            else:
                st.metric("Next restock in", "Ready")

        if not avail:
            st.caption("**Offline** = no API key configured or all providers unavailable. Set at least one of: GOOGLE_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY, COhere API key (env or .env).")
        elif stats.get('wait_seconds', 0) > 0:
            st.caption("⏱️ One or more providers are at rate limit. Next window resets in the time shown.")
        else:
            st.caption("**Ready** = not rate limited; you can call now. Countdown appears when a provider hits its RPM limit.")

        # Per-provider breakdown
        providers = stats.get('providers', {})
        if providers:
            with st.expander("Per-provider details", expanded=False):
                for name, p in providers.items():
                    wait_sec = p.get('wait_sec', 0)
                    if name == "gemini":
                        per_key = p.get('per_key', [])
                        used = sum(k.get('rpm_used', 0) for k in per_key)
                        max_rpm = sum(k.get('max_rpm', 14) for k in per_key) if per_key else 14 * max(1, p.get('keys', 1))
                        st.caption(f"**{name}** ({p.get('keys', 0)} keys): {used}/{max_rpm} RPM" + (f" • restock in {int(wait_sec)}s" if wait_sec > 0 else " • ready"))
                    else:
                        st.caption(f"**{name}**: {p.get('rpm_used', 0)}/{p.get('max_rpm', 0)} RPM" + (f" • restock in {int(wait_sec)}s" if wait_sec > 0 else " • ready"))
    else:
        st.info("**AI client** powers debates, trade ideas, and reports. It initializes on first use (e.g. Command Center, Talk to Brains, or autonomous scout).")

    # Database
    st.markdown("---")
    st.markdown("#### Database")
    try:
        from sqlalchemy import text as sql_text
        from market_agent.data.storage.postgres import PostgresStorage
        storage = PostgresStorage()
        with storage.engine.connect() as conn:
            result = conn.execute(sql_text("SELECT version()"))
            version = result.scalar()

        st.markdown(f"""
        <div class="metric-card" style="text-align: left; padding: 12px;">
            <div style="font-size: 12px; color: #10b981; font-weight: 600;">🟢 Connected</div>
            <div style="font-size: 10px; color: #94a3b8; margin-top: 4px;">{version[:60] if version else 'N/A'}</div>
        </div>
        """, unsafe_allow_html=True)

        # Table row counts
        with storage.engine.connect() as conn:
            tables = ["signal_predictions", "council_debates", "ai_code_proposals", "brain_training_events"]
            table_data = []
            for table in tables:
                try:
                    count = conn.execute(sql_text(f"SELECT COUNT(*) FROM {table}")).scalar()
                    table_data.append({"Table": table, "Rows": count})
                except Exception:
                    table_data.append({"Table": table, "Rows": "Table missing"})
            st.dataframe(pd.DataFrame(table_data), hide_index=True)
    except Exception as e:
        st.error(f"🔴 Database Offline: {e}")

    # FAISS Memory
    st.markdown("---")
    st.markdown("#### FAISS Vector Memory")
    try:
        from market_agent.brain.council_memory import CouncilMemory
        mem = CouncilMemory()
        total = mem.faiss.index.ntotal if mem.faiss and hasattr(mem.faiss, 'index') and mem.faiss.index else 0
        st.markdown(f"""
        <div class="metric-card" style="text-align: left; padding: 12px;">
            <div style="font-size: 12px; color: {'#10b981' if total > 0 else '#64748b'}; font-weight: 600;">
                📦 {total} vectors stored
            </div>
            <div style="font-size: 10px; color: #94a3b8; margin-top: 4px;">
                {'Active — search via Brain Monitor' if total > 0 else 'Vectors populate as council debates happen.'}
            </div>
        </div>
        """, unsafe_allow_html=True)
    except Exception as e:
        st.caption(f"FAISS: {e}")

# ══════════════════════════════════════
# TAB 3: WEIGHT DECAY
# ══════════════════════════════════════
with tab_decay:
    st.markdown("### 📉 Weight Decay Status")
    st.caption("Daily weight decay pulls extreme brain weights back toward 1.0, preventing runaway dominance.")

    try:
        from market_agent.brain.health_monitor import get_health_monitor
        health = get_health_monitor()
        weights = health.get_brain_weights() if hasattr(health, 'get_brain_weights') else {}

        if weights:
            weight_data = []
            for brain, w in weights.items():
                deviation = abs(w - 1.0)
                status = "✅ Normal" if deviation < 0.1 else "⚠️ Adjusted" if deviation < 0.3 else "🔴 Extreme"
                weight_data.append({
                    "Brain": brain,
                    "Weight": f"{w:.4f}",
                    "Δ from 1.0": f"{'+' if w >= 1 else ''}{w - 1:.4f}",
                    "Status": status,
                })
            st.dataframe(pd.DataFrame(weight_data), hide_index=True)
        else:
            st.info("All brain weights start at **1.0** and adjust through RL training (+0.05 correct / -0.075 wrong). Decay runs daily.")

        st.markdown("""
        <div class="brain-card" style="font-size: 11px; color: #94a3b8;">
            <b style="color: #818cf8;">Decay Formula:</b> weight = weight × (1 - decay_rate) + decay_rate × 1.0<br>
            <b style="color: #818cf8;">Frequency:</b> Once per day, after learning cycle<br>
            <b style="color: #818cf8;">Purpose:</b> Prevents extreme weights from persisting after regime changes
        </div>
        """, unsafe_allow_html=True)
    except Exception as e:
        st.caption(f"Weight decay: {e}")

# ══════════════════════════════════════
# TAB 4: JOB HEALTH (ADD 4 — Dashboardcheck.md)
# ══════════════════════════════════════
with tab_jobs:
    st.markdown("### 🔧 Background Job Health")
    st.caption("Live status of the 3 critical background systems. If any go offline, data quality degrades silently.")

    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from sqlalchemy import text
        storage = PostgresStorage()

        # ── 1. Signal Resolver ────────────────────────────────────────────
        with storage.engine.connect() as conn:
            res = conn.execute(text("""
                SELECT
                    MAX(exit_timestamp) AS last_resolve,
                    COUNT(*) FILTER (WHERE outcome IS NULL
                              AND created_at < NOW() - INTERVAL '30 minutes') AS stale_count
                FROM council_verdicts
            """)).fetchone()

        if res and res[0]:
            age_min = (datetime.utcnow() - res[0]).total_seconds() / 60
            status  = '✅' if age_min < 15 else '⚠️'
            st.markdown(
                f"{status} **Signal Resolver** — "
                f"Last resolved: **{age_min:.0f} min ago** │ "
                f"Stale predictions (>30m): **{res[1] or 0}**"
            )
        else:
            st.markdown("⚪ **Signal Resolver** — No resolved signals yet")

        # ── 2. AEP Daily Check ────────────────────────────────────────────
        try:
            with storage.engine.connect() as conn:
                aep_row = conn.execute(text("""
                    SELECT run_at, brains_checked, triggers_fired
                    FROM aep_run_logs
                    ORDER BY run_at DESC LIMIT 1
                """)).fetchone()

            if aep_row:
                aep_age_h = (datetime.utcnow() - aep_row[0]).total_seconds() / 3600
                aep_status = '✅' if aep_age_h < 25 else '⚠️'
                st.markdown(
                    f"{aep_status} **AEP Daily Check** — "
                    f"Last run: **{aep_age_h:.1f}h ago** │ "
                    f"Brains checked: **{aep_row[1] or '?'}** │ "
                    f"Triggers fired: **{aep_row[2] or 0}**"
                )
            else:
                st.markdown("⚪ **AEP Daily Check** — Not yet run (scheduled daily at 09:30 UTC)")
        except Exception:
            st.markdown("⚪ **AEP Daily Check** — `aep_run_logs` table not found yet")

        # ── 3. News Watcher ──────────────────────────────────────────────
        watcher = st.session_state.get('news_watcher')
        if watcher:
            thread = getattr(watcher, '_thread', None)
            alive  = thread.is_alive() if thread else False
            cache  = st.session_state.get('news_cache')
            scored = 0
            if cache and hasattr(cache, '_data'):
                scored = len([k for k in cache._data if k != '_rss_meta' and cache._data[k].get('scored_at')])
            icon_alive = "\u2705" if alive else "\U0001f534"
            st.markdown(
                f"{icon_alive} **News Watcher** — "
                f"Thread: **{'alive' if alive else 'DEAD'}** │ "
                f"Symbols scored: **{scored}**"
            )
        else:
            st.markdown("⚪ **News Watcher** — Not initialized in session state")

        st.markdown("---")
        st.caption("✅ = healthy | ⚠️ = stale / needs attention | 🔴 = dead | ⚪ = not started")

    except Exception as e:
        st.warning(f"Job health panel: {e}")


# ═══════════════════════════════════════════════════════════════════════
# SECTION 6 (Settings) — Paper Trading Capital
# Feeds base_capital_inr used by Session Scorecard ₹ PnL display.
# ═══════════════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("💰 Paper Trading Capital")
st.caption(
    "Sets the base capital used to display PnL in ₹ across the dashboard. "
    "No connection to any broker — display purposes only."
)
current_capital = st.session_state.get('base_capital_inr', 500000)
new_capital = st.number_input(
    "Base Capital (₹)",
    min_value=10000,
    max_value=10000000,
    value=int(current_capital),
    step=10000,
    format="%d",
    help="Example: enter 500000 for ₹5,00,000",
    key="capital_input_settings",
)
if new_capital != current_capital:
    st.session_state['base_capital_inr'] = new_capital
    st.success(f"Capital updated to ₹{new_capital:,.0f}")
