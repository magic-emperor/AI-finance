"""
math_utils.py — Shared mathematical utilities for AI Agent Finance.

Centralises probability normalisation and other math helpers so every
signal generator, brain, and council uses identical logic.
"""
import numpy as np


def safe_direction_probs(probs: list) -> np.ndarray:
    """
    Normalize direction probability array to sum to 1.0.
    Input : [P_down, P_flat, P_up]  (raw, un-normalized)
    Output: valid probability distribution that sums to 1.0

    Guarantees:
      - No negative values (clipped to 0)
      - Always 3 elements [down, flat, up]
      - Falls back to uniform distribution on degenerate input
    """
    arr = np.array(probs, dtype=float)
    arr = np.clip(arr, 0.0, None)       # no negative probabilities
    total = arr.sum()
    if total <= 0:
        return np.array([0.33, 0.34, 0.33])   # uniform fallback
    return arr / total


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def expected_value(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Calculate Expected Value per trade.
    win_rate  : float 0-1
    avg_win   : positive float (profit per winning trade)
    avg_loss  : negative float (loss per losing trade — pass as negative number)
    """
    lose_rate = 1.0 - win_rate
    return (win_rate * avg_win) + (lose_rate * avg_loss)


def calculate_weekly_ev(trades: list) -> dict:
    """
    Section 10.1: The Correct Success Metric — Expected Value per Trade.
    Stop measuring raw accuracy %. Start measuring EV after each week.

    trades: list of dicts with keys:
      {'symbol', 'direction', 'outcome': 'TARGET'|'SL'|'EXPIRED',
       'entry', 't1', 'sl', 'actual_exit', 'pnl'}

    Returns a dict with win_rate, avg_win, avg_loss, EV, and profitability flag.
    If EV > 0 consistently over 50+ trades, the system has real edge.
    """
    if not trades:
        return {}

    wins    = [t for t in trades if t.get("outcome") == "TARGET"]
    losses  = [t for t in trades if t.get("outcome") == "SL"]
    expired = [t for t in trades if t.get("outcome") == "EXPIRED"]

    total = len(trades)
    win_rate  = len(wins)  / total
    avg_win   = sum(t.get("pnl", 0) for t in wins)   / len(wins)   if wins   else 0.0
    avg_loss  = sum(t.get("pnl", 0) for t in losses) / len(losses) if losses else 0.0

    ev = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

    return {
        "total_trades":             total,
        "win_rate":                 round(win_rate * 100, 1),
        "avg_win":                  round(avg_win, 2),
        "avg_loss":                 round(avg_loss, 2),
        "expected_value_per_trade": round(ev, 2),
        "is_profitable":            ev > 0,
        "expired_rate":             round(len(expired) / total * 100, 1),
        "sample_adequate":          total >= 50,  # EV is reliable only after 50+ trades
    }
