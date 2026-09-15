"""
metrics.py — Honest scorecard math, computed from real R-multiples.

The headline is EXPECTANCY (mean R per trade): the average amount, in units of
risk, that each trade makes or loses. Positive expectancy after costs = a real
edge. Everything is computed from the actual closed trades — no partial credit,
no inflation.
"""
from __future__ import annotations

from typing import List, Dict
import numpy as np

from engine.backtest.simulator import CompletedTrade


def compute_metrics(trades: List[CompletedTrade]) -> Dict:
    n = len(trades)
    base = {
        "n_trades": n, "win_rate": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0,
        "avg_win_r": 0.0, "avg_loss_r": 0.0, "reward_risk": 0.0,
        "max_drawdown_r": 0.0, "total_r": 0.0, "equity_curve": [],
    }
    if n == 0:
        return base

    rs = np.array([t.R for t in trades], dtype=float)
    wins = rs[rs > 0]
    losses = rs[rs <= 0]

    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0

    equity = np.cumsum(rs)
    peak = np.maximum.accumulate(equity)
    max_dd = float((equity - peak).min()) if n else 0.0

    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0  # <= 0

    base.update({
        "win_rate": float(wins.size) / n,
        "expectancy_r": float(rs.mean()),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
        "reward_risk": (avg_win / abs(avg_loss)) if avg_loss < 0 else float("inf"),
        "max_drawdown_r": max_dd,
        "total_r": float(rs.sum()),
        "equity_curve": equity.tolist(),
    })
    return base
