"""
AEP Proposals — Dashboard Page

Displays pending AI Enhancement Proposals for human review.
Each proposal shows: brain, problem, suggested parameter change,
council vote, Mistral review, and Approve / Reject buttons.

The system NEVER auto-applies anything.
Human clicks Approve → parameter logged to brain_parameter_overrides.
Human clicks Reject → AEP marked rejected, brain continues unchanged.
"""

import streamlit as st
import json
from datetime import datetime

# ══════════════════════════════════════════════════════════════
# PAGE — AEP Proposals
# ══════════════════════════════════════════════════════════════

st.markdown("## ⚡ AI Enhancement Proposals")
st.caption(
    "Brain failure patterns detected and reviewed before reaching you. "
    "The system suggests parameter changes only — **you decide**."
)

# ── CSS additions for AEP cards ───────────────────────────────
st.markdown("""
<style>
.aep-card {
    background: linear-gradient(145deg, #0f1a2e 0%, #0d1520 100%);
    border: 1px solid rgba(239, 68, 68, 0.25);
    border-radius: 14px;
    padding: 20px 24px;
    margin-bottom: 18px;
}
.aep-card-approved {
    background: linear-gradient(145deg, #0f1a2e 0%, #0d1520 100%);
    border: 1px solid rgba(16, 185, 129, 0.3);
    border-radius: 14px;
    padding: 20px 24px;
    margin-bottom: 18px;
}
.aep-card-rejected {
    background: linear-gradient(145deg, #0f1a2e 0%, #0d1520 100%);
    border: 1px solid rgba(100,100,100,0.2);
    border-radius: 14px;
    padding: 20px 24px;
    margin-bottom: 18px;
    opacity: 0.6;
}
.aep-brain-tag {
    display: inline-block;
    background: rgba(99,102,241,0.15);
    border: 1px solid rgba(99,102,241,0.4);
    color: #a5b4fc;
    font-size: 12px; font-weight: 600;
    padding: 3px 10px; border-radius: 20px;
    margin-bottom: 10px;
}
.aep-trigger-tag {
    display: inline-block;
    background: rgba(239,68,68,0.12);
    border: 1px solid rgba(239,68,68,0.35);
    color: #fca5a5;
    font-size: 11px; font-weight: 600;
    padding: 2px 9px; border-radius: 20px;
    margin-left: 8px;
    margin-bottom: 10px;
}
.aep-vote-badge {
    display: inline-block;
    background: rgba(16,185,129,0.1);
    border: 1px solid rgba(16,185,129,0.3);
    color: #6ee7b7;
    font-size: 12px; font-weight: 600;
    padding: 3px 10px; border-radius: 20px;
}
.aep-change-box {
    background: rgba(245,158,11,0.08);
    border: 1px solid rgba(245,158,11,0.2);
    border-radius: 8px;
    padding: 12px 16px;
    margin: 12px 0;
}
.aep-review-box {
    background: rgba(99,102,241,0.06);
    border: 1px solid rgba(99,102,241,0.15);
    border-radius: 8px;
    padding: 12px 16px;
    margin: 12px 0;
    font-size: 13px;
    color: #cbd5e1;
    font-style: italic;
}
.aep-empty {
    text-align: center;
    padding: 60px 20px;
    color: #475569;
}
.aep-empty-icon { font-size: 48px; margin-bottom: 16px; }
</style>
""", unsafe_allow_html=True)


# ── Load AEP data ─────────────────────────────────────────────
@st.cache_data(ttl=30)
def _load_pending_aeps():
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from market_agent.brain.aep_storage import AEPStorage
        s = AEPStorage(PostgresStorage())
        return s.get_pending_aeps_for_dashboard()
    except Exception as e:
        return []


@st.cache_data(ttl=60)
def _load_recent_aeps(limit: int = 10):
    """Load recently resolved (approved/rejected) AEPs for history view."""
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from sqlalchemy import text
        storage = PostgresStorage()
        session = storage.Session()
        rows = session.execute(text("""
            SELECT id, pr_id, proposing_brain, trigger_type,
                   problem_summary, human_decision,
                   suggested_change, created_at, applied_at
            FROM   ai_code_proposals
            WHERE  human_decision IN ('APPROVED', 'REJECTED')
            ORDER  BY created_at DESC
            LIMIT  :limit
        """), {"limit": limit}).fetchall()
        session.close()
        return [
            {
                "id":           r[0],
                "pr_id":        r[1],
                "brain":        r[2],
                "trigger_type": r[3],
                "problem":      r[4],
                "decision":     r[5],
                "has_change":   bool(r[6] and r[6] != "null"),
                "created_at":   r[7].strftime("%Y-%m-%d %H:%M") if r[7] else "",
                "applied_at":   r[8].strftime("%Y-%m-%d %H:%M") if r[8] else "",
            }
            for r in rows
        ]
    except Exception:
        return []


def _approve_aep(aep: dict) -> bool:
    """Write approved parameter change to brain_parameter_overrides."""
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from market_agent.brain.aep_storage import AEPStorage
        s = AEPStorage(PostgresStorage())
        change = aep.get("suggested_change") or {}
        return s.save_parameter_override(
            aep_id            = aep["id"],
            brain_name        = aep["brain"],
            parameter_name    = change.get("parameter_name", "unknown"),
            old_value         = str(change.get("current_value", "")),
            new_value         = str(change.get("suggested_value", "")),
            applies_to_regime = change.get("applies_to_regime"),
            applies_to_symbol = change.get("applies_to_symbol"),
        )
    except Exception as e:
        st.error(f"Approval failed: {e}")
        return False


def _reject_aep(aep_id: int) -> bool:
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from market_agent.brain.aep_storage import AEPStorage
        s = AEPStorage(PostgresStorage())
        return s.reject_aep(aep_id)
    except Exception as e:
        st.error(f"Rejection failed: {e}")
        return False


def _extract_verdict_label(review_text: str) -> tuple:
    """Returns (label, color) from Mistral review verdict."""
    review_upper = (review_text or "").upper()
    if "VERDICT: APPROVE_FOR_REVIEW" in review_upper:
        return "Mistral: APPROVE", "#10b981"
    elif "VERDICT: NEEDS_MORE_DATA" in review_upper:
        return "Mistral: MORE DATA NEEDED", "#f59e0b"
    elif "VERDICT: REJECT" in review_upper:
        return "Mistral: REJECT", "#ef4444"
    return "Mistral: UNKNOWN", "#64748b"


# ─────────────────────────────────────────────────────────────
# MAIN CONTENT — Tabs
# ─────────────────────────────────────────────────────────────
tab_pending, tab_history, tab_about = st.tabs([
    "⚠️  Pending Review",
    "📋 History",
    "ℹ️  How AEP Works",
])


# ════════════════════════════════════════
# TAB 1: PENDING PROPOSALS
# ════════════════════════════════════════
with tab_pending:
    pending = _load_pending_aeps()
    
    # Needs a localized storage instance to fetch the run log and data check
    try:
        from market_agent.data.storage.postgres import PostgresStorage
        from market_agent.brain.aep_storage import AEPStorage
        aep_storage = AEPStorage(PostgresStorage())
        last_run = aep_storage.get_last_aep_run()
        data_check = aep_storage.get_data_sufficiency()
    except Exception:
        last_run = None
        data_check = {'sufficient': False, 'resolved_trades': 0}

    # ── STATE 3: PENDING REVIEW ─────────────────────────────
    if pending:
        st.warning(f"⚠️ {len(pending)} AEP proposal(s) pending your review")
        st.markdown("Each passed a 4/7 council vote and Mistral sanity-check. Review and decide.")
        st.markdown("---")

        for aep in pending:
            change        = aep.get("suggested_change") or {}
            mistral_txt   = aep.get("mistral_review", "")
            verdict_label, verdict_color = _extract_verdict_label(mistral_txt)

            # ── AEP Card ─────────────────────────────────────
            st.markdown(f"""
            <div class="aep-card">
                <span class="aep-brain-tag">🧠 {aep['brain']}</span>
                <span class="aep-trigger-tag">{aep['trigger_type'].replace('_', ' ').upper()}</span>
                <span class="aep-vote-badge">🗳️ {aep['vote_summary']}</span>
            </div>
            """, unsafe_allow_html=True)

            col_info, col_actions = st.columns([3, 1])

            with col_info:
                st.markdown(f"**Problem:** {aep['problem_summary']}")
                st.markdown(f"**Root cause:** {aep.get('root_cause', 'See details')}")

                # Suggested change box
                if change:
                    regime_txt  = f" · Regime: `{change.get('applies_to_regime', 'ALL')}`"
                    symbol_txt  = f" · Symbol: `{change.get('applies_to_symbol', 'ALL')}`"
                    retrain_txt = " · **Retrain required**" if aep.get("requires_retrain") else ""
                    st.markdown(f"""
<div class="aep-change-box">
<b>💡 Suggested parameter change</b><br>
Set <code>{change.get('parameter_name', '?')}</code>
from <code>{change.get('current_value', '?')}</code>
to <code>{change.get('suggested_value', '?')}</code>
{regime_txt}{symbol_txt}{retrain_txt}<br>
<span style="color:#94a3b8;font-size:12px">{aep.get('change_rationale', '')}</span>
</div>
                    """, unsafe_allow_html=True)
                else:
                    st.warning("No specific parameter change suggested — human investigation required.")

                # Mistral review
                if mistral_txt:
                    clean_review = mistral_txt.replace("VERDICT: APPROVE_FOR_REVIEW", "").replace(
                        "VERDICT: NEEDS_MORE_DATA", "").replace("VERDICT: REJECT", "").strip()
                    st.markdown(f"""
<div class="aep-review-box">
<b style="color:#a5b4fc;font-style:normal">Mistral Review:</b><br>{clean_review}
<br><span style="color:{verdict_color};font-weight:600;font-style:normal">→ {verdict_label}</span>
</div>
                    """, unsafe_allow_html=True)

                st.caption(f"**{aep['pr_id']}** · Generated {aep['created_at']}")

            with col_actions:
                st.markdown("<br>", unsafe_allow_html=True)

                # APPROVE button (only if there's a parameter change to apply)
                if change:
                    if st.button(
                        "✅ APPROVE",
                        key=f"approve_{aep['id']}",
                        type="primary",
                        use_container_width=True,
                    ):
                        if _approve_aep(aep):
                            st.success(
                                f"✅ Approved! Parameter `{change.get('parameter_name')}` "
                                f"logged to overrides table."
                            )
                            if aep.get("requires_retrain"):
                                st.info("🔄 Retrain required for this change to activate.")
                            _load_pending_aeps.clear()
                            _load_recent_aeps.clear()
                            st.rerun()
                        else:
                            st.error("Failed to save. Check logs.")
                else:
                    st.button(
                        "✅ APPROVE",
                        key=f"approve_{aep['id']}",
                        disabled=True,
                        help="No parameter change to apply — investigate manually.",
                        use_container_width=True,
                    )

                if st.button(
                    "❌ REJECT",
                    key=f"reject_{aep['id']}",
                    use_container_width=True,
                ):
                    if _reject_aep(aep["id"]):
                        st.info("Proposal rejected. Brain continues with current parameters.")
                        _load_pending_aeps.clear()
                        _load_recent_aeps.clear()
                        st.rerun()
                    else:
                        st.error("Rejection failed. Check logs.")

                # Show details expander
                with st.expander("🔍 Full Details"):
                    st.json({
                        "pr_id":            aep["pr_id"],
                        "trigger_type":     aep["trigger_type"],
                        "suggested_change":  change,
                        "requires_retrain": aep.get("requires_retrain"),
                        "vote_summary":      aep["vote_summary"],
                    })

            st.markdown("---")

    # ── STATE 4: RUNNER NOT EXECUTED ────────────────────────
    elif not last_run:
        st.info("⚪ AEP check has not run yet — scheduled for 09:30 UTC daily.")

    # ── STATE 4: RUNNER STALE (>25h ago) — not just >24h ────
    elif (datetime.utcnow().timestamp() - last_run['run_at'].timestamp()) > 90000:
        st.info(
            f"⚪ **Last AEP check:** {last_run['run_at'].strftime('%Y-%m-%d %H:%M UTC')} "
            f"— running again at 09:30 UTC"
        )

    # ── STATE 1: INSUFFICIENT DATA ──────────────────────────
    elif not data_check.get('sufficient'):
        st.info(
            f"🔵 **Building trade history** — "
            f"{data_check.get('resolved_trades', 0)} resolved trades collected total. "
            f"AEP pattern detection activates after 15 trades per brain per regime. "
            f"Keep the system running."
        )

    # ── STATE 2: CHECKED AND GENUINELY HEALTHY ───────────────
    # Only shows this if: last_run exists, was < 25h ago, AND data was sufficient.
    # This prevents showing 'healthy' when the table is simply empty.
    else:
        checked_time = last_run['run_at'].strftime('%H:%M UTC')
        st.success(
            f"✅ All brains healthy — checked today at {checked_time}. "
            f"No failure patterns detected."
        )


# ════════════════════════════════════════
# TAB 2: HISTORY
# ════════════════════════════════════════
with tab_history:
    history = _load_recent_aeps(20)

    if not history:
        st.info("No resolved proposals yet.")
    else:
        for h in history:
            icon    = "✅" if h["decision"] == "APPROVED" else "❌"
            color   = "#10b981" if h["decision"] == "APPROVED" else "#64748b"
            ts_info = f"Applied: {h['applied_at']}" if h["applied_at"] else f"Created: {h['created_at']}"
            st.markdown(
                f"{icon} **{h['pr_id']}** · {h['brain']} · _{h['trigger_type'].replace('_', ' ')}_ · "
                f"<span style='color:{color}'>{h['decision']}</span> · {ts_info}",
                unsafe_allow_html=True,
            )
            if h["problem"]:
                st.caption(f"  → {h['problem']}")
        st.caption("Showing last 20 resolved proposals.")


# ════════════════════════════════════════
# TAB 3: HOW AEP WORKS
# ════════════════════════════════════════
with tab_about:
    st.markdown("""
### How the AEP System Works

The AEP (AI Enhancement Proposal) system **never changes anything automatically**.
It detects, diagnoses, and surfaces brain failure patterns for **human review only**.

---

#### Trigger Conditions (Pure Python — zero LLM calls)

| Condition | Threshold |
|-----------|-----------|
| **Accuracy drift** | Win rate drops ≥10pp week-over-week, ≥15 trades per window |
| **Regime failure** | Win rate < 45% in a specific market regime, ≥15 trades |

---

#### The Pipeline (per triggered brain)

```
Step 1: Trigger check          → pure Python, 0 LLM calls
Step 2: Gemini diagnosis       → 1 Gemini call (parameter suggestion only)
Step 3: Council vote (4/7)     → pure Python, each brain checks own DB records
Step 4: Mistral review         → 1 Mistral call (sanity check)
Step 5: Appears here           → you approve or reject
```

**Cost:** 0 LLM calls on healthy days. 2 calls (1 Gemini + 1 Mistral) per triggered brain.

---

#### What Happens When You Approve

1. Parameter change written to `brain_parameter_overrides` table (full audit trail)
2. Brain reads override at next initialization — applies if regime/symbol match
3. If retrain required: run retrain manually to activate the change

#### What Happens When You Reject

1. AEP marked rejected in DB
2. Brain continues with current parameters
3. Data preserved — useful to see if same suggestion repeats

---

#### Governance Rule

> *This system generates observations and parameter suggestions only.
> It never generates code diffs. It never auto-applies any change.
> The blast radius of a wrong parameter change: 1 config override + 1 retrain.
> Acceptable. The blast radius of a wrong code change: unknown. Not acceptable.*
""")

    st.markdown("""
---
#### Brains watched by AEP
| Brain | Tracks |
|-------|--------|
| AMV-LSTM | Temporal pattern recognition |
| Cross-Stock GNN | Cross-asset correlations |
| RL Weighter | Position sizing & risk |
| Multi-Timeframe | 1m / 15m / 1h / 4h / 1d |
| Regime Ensemble | Market regime detection |
| Multi-Modal Fusion | News sentiment + TA |
| Causal Ensemble | RSI + Bollinger |
""")
