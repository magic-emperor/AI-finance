"""
Daily Brain Performance & Training Report

Generates a markdown report summarizing:
- Per-brain trading accuracy (last N resolved trades)
- Training runs per brain
- Recent analyst thoughts for key symbols

Intended to be run once per day (e.g., after Indian market close)
via cron / Windows Task Scheduler:

    python -m market_agent.reporting.daily_brain_report
"""

import os
from datetime import datetime
from pathlib import Path

from market_agent.data.storage.postgres import PostgresStorage
from market_agent.learning.evaluator import RegretEngine
from market_agent.learning.signal_resolver import SignalResolver, SignalPrediction


def generate_daily_brain_report(
    symbols=None,
    last_n_trades: int = 50,
    output_dir: str = "reports",
) -> str:
    """
    Build a single markdown report file and return its path.
    """
    storage = PostgresStorage()
    regret = RegretEngine(storage)
    resolver = regret.signal_resolver or SignalResolver(storage)
    
    # Dynamic: Get all model IDs that have actually generated signals
    session = storage.Session()
    try:
        from sqlalchemy import func
        db_brains = session.query(SignalPrediction.model_id).distinct().all()
        brains = [b[0] for b in db_brains if b[0]]
        # Sort for consistent report ordering
        brains.sort()
    except Exception:
        # Fallback if query fails
        brains = ["Aegis-Global"]  # Minimal fallback
    finally:
        session.close()

    # Load accuracy threshold from config
    WIN_THRESHOLD = 80.0
    try:
        import json as _json
        _cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json')
        with open(_cfg_path) as f:
            _cfg = _json.load(f)
        WIN_THRESHOLD = _cfg.get('default', {}).get('accuracy_threshold', 80.0)
    except Exception:
        pass

    now = datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d")

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"brain_report_{date_str}.md"

    lines = []
    lines.append(f"# Daily Brain Report — {date_str}")
    lines.append("")
    lines.append("## 1. Per-Brain Trading Performance (Last N Resolved Trades)")
    lines.append("")

    for brain in brains:
        stats = resolver.get_accuracy_stats(model_id=brain, last_n=last_n_trades)
        lines.append(f"### {brain}")
        lines.append(
            f"- Accuracy: **{stats.get('accuracy', 0.0):.1f}%** over "
            f"**{stats.get('total', 0)}** trades (status: {stats.get('status', 'N/A')}, trend: {stats.get('trend', 'N/A')})"
        )
        lines.append(
            f"- Wins (≥{WIN_THRESHOLD:.0f} score): **{stats.get('wins', 0)}**, Shocks detected: **{stats.get('shocks', 0)}**"
        )
        if stats.get("recent_scores"):
            lines.append("- Recent examples:")
            for r in stats["recent_scores"]:
                lines.append(
                    f"  - {r['date']}: {r['symbol']} {r['direction']} — "
                    f"{r['accuracy']:.1f} ({r['type']}, shock={r['shock']})"
                )
        lines.append("")

    # Training summary
    lines.append("## 2. Brain Training Runs")
    lines.append("")
    for brain in brains:
        training_stats = storage.get_brain_training_stats(brain)
        last_run_at = training_stats["last_run_at"].strftime("%Y-%m-%d %H:%M") if training_stats["last_run_at"] else "Never"
        lines.append(
            f"- **{brain}** — total runs: {training_stats['total_runs']}, "
            f"last run: {last_run_at}, mode: {training_stats.get('last_mode') or 'N/A'}"
        )
    lines.append("")

    # Recent analyst thoughts (optional, per symbol)
    if symbols:
        # Per-symbol activity first
        lines.append("## 3. Per-Symbol Activity (Today)")
        lines.append("")
        session = storage.Session()
        try:
            today = now.replace(hour=0, minute=0, second=0, microsecond=0)
            for sym in symbols:
                q = session.query(SignalPrediction).filter(
                    SignalPrediction.symbol == sym,
                    SignalPrediction.created_at >= today,
                )
                total_today = q.count()
                resolved_today = q.filter(SignalPrediction.is_resolved == True).count()
                lines.append(
                    f"- **{sym}** — signals today: {total_today}, resolved: {resolved_today}"
                )
            lines.append("")
        finally:
            session.close()

        lines.append("## 4. Recent Analyst Thoughts (Neural Memory)")
        lines.append("")
        for sym in symbols:
            try:
                history = storage.get_brain_history(sym, limit=3)
            except Exception:
                history = []
            lines.append(f"### {sym}")
            if not history:
                lines.append("- No stored thoughts for this symbol yet.")
            else:
                for h in history:
                    ts = h["timestamp"].strftime("%Y-%m-%d %H:%M")
                    lines.append(f"- {ts}: sentiment {h['sentiment']:+.2f}, conclusion: _{h['conclusion']}_")
            lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return str(out_path)


if __name__ == "__main__":
    # Symbols to highlight in daily report (customize as needed)
    default_symbols = os.getenv("AEGIS_REPORT_SYMBOLS", "ITC.NS,BTC-USD").split(",")
    path = generate_daily_brain_report(symbols=[s.strip() for s in default_symbols if s.strip()])
    print(f"Daily brain report written to: {path}")

