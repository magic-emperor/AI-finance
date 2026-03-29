"""
brain_decisions_panel.py — Per IMPL-PLAN-V3 Fix 7
Dashboard panel for visualising council decisions logged by brain_logger.py.

Provides:
  render_latest(n)       — Returns last N cycle records as a formatted string table
  render_live_summary()  — Returns high-level stats: win rate, avg confidence, echo chamber alert
"""
from __future__ import annotations

from typing import List, Dict, Any
from datetime import datetime


def _load_recent(n: int = 50) -> List[Dict[str, Any]]:
    """Load recent log records from brain_logger. Returns [] on any failure."""
    try:
        from market_agent.brain.brain_logger import get_recent_cycles
        return get_recent_cycles(n)
    except Exception:
        return []


def render_latest(n: int = 10) -> str:
    """
    Returns last N brain council cycle records as a plain-text table.
    Suitable for embedding in a Streamlit st.text() or logging output.
    """
    records = _load_recent(n)
    if not records:
        return "No brain decisions logged yet. Run a council cycle to see results here."

    lines = [
        f"{'─' * 80}",
        f"  BRAIN COUNCIL DECISIONS — last {n} cycles",
        f"{'─' * 80}",
        f"  {'TIME':>8}  {'SYMBOL':<12}  {'REGIME':<18}  {'VERDICT':<6}  {'CONF':>5}  {'AGREE':>5}",
        f"{'─' * 80}",
    ]

    for r in reversed(records):  # most recent first
        ts_str = r.get("ts", "")[:19].replace("T", " ")
        symbol = r.get("symbol", "?")[:12]
        regime = r.get("regime", "?")[:18]
        verdict = r.get("verdict", "?")[:6]
        conf    = f"{float(r.get('verdict_confidence', 0)):.1%}"
        agree   = f"{float(r.get('agreement_rate', 0)):.0%}"
        lines.append(f"  {ts_str:>19}  {symbol:<12}  {regime:<18}  {verdict:<6}  {conf:>5}  {agree:>5}")

        # Show individual brain votes indented
        for b in r.get("brains", []):
            badge = "✓" if b.get("direction") == verdict else "✗"
            lines.append(
                f"    {badge} {b.get('brain','?'):<22}  {b.get('direction','?'):<5}  {float(b.get('confidence', 0)):.1%}"
            )
        lines.append("")

    lines.append(f"{'─' * 80}")
    return "\n".join(lines)


def render_live_summary() -> str:
    """
    High-level executive summary of recent council performance.
    Flags echo-chamber risk if agreement_rate > 0.85 consistently.
    """
    records = _load_recent(50)
    if not records:
        return "No data yet — run a council cycle to see summary."

    total         = len(records)
    buy_count     = sum(1 for r in records if r.get("verdict") == "BUY")
    sell_count    = sum(1 for r in records if r.get("verdict") == "SELL")
    hold_count    = total - buy_count - sell_count
    avg_conf      = sum(float(r.get("verdict_confidence", 0)) for r in records) / total
    avg_agree     = sum(float(r.get("agreement_rate", 0)) for r in records) / total
    echo_risk     = avg_agree > 0.85
    echo_warning  = "  ⚠️  ECHO CHAMBER RISK — avg agree > 85%" if echo_risk else ""

    symbols = list({r.get("symbol", "?") for r in records})

    lines = [
        f"{'━' * 60}",
        f"  BRAIN COUNCIL — LIVE SUMMARY  (last {total} cycles)",
        f"{'━' * 60}",
        f"  Symbols tracked : {', '.join(symbols[:6])}{'...' if len(symbols) > 6 else ''}",
        f"  BUY / SELL / HOLD : {buy_count} / {sell_count} / {hold_count}",
        f"  Avg verdict conf  : {avg_conf:.1%}",
        f"  Avg brain agree   : {avg_agree:.1%}{echo_warning}",
        f"{'━' * 60}",
        f"  Tip: If agreement > 85% consistently, add uncorrelated brains.",
        f"{'━' * 60}",
    ]
    return "\n".join(lines)
