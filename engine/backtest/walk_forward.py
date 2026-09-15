"""
walk_forward.py — Train / held-out out-of-sample (OOS) splitting.

The discipline: the oldest 60% of history is TRAIN (where parameter tuning WOULD
happen). The most recent 40% is OOS — never touched during tuning. We report OOS
as the truth. We also tag trades into thirds so we can see whether the edge holds
across time (a rolling robustness check), not just in one lucky window.

Because the v1 params are pre-registered in config.py (no auto-tuning yet), the
split's job here is honest reporting: TRAIN for reference, OOS for the verdict.
"""
from __future__ import annotations

from typing import List, Tuple
import pandas as pd

from engine.backtest.simulator import CompletedTrade


def oos_boundary_time(df: pd.DataFrame, oos_fraction: float) -> pd.Timestamp:
    """Timestamp at which OOS begins (the most recent `oos_fraction` of bars)."""
    n = len(df)
    pos = int(n * (1.0 - oos_fraction))
    pos = max(0, min(pos, n - 1))
    return df.index[pos]


def split_trades(trades: List[CompletedTrade],
                 boundary: pd.Timestamp) -> Tuple[List[CompletedTrade], List[CompletedTrade]]:
    """Split by signal_time: (train, oos). OOS = signalled at/after the boundary."""
    train = [t for t in trades if t.signal_time < boundary]
    oos = [t for t in trades if t.signal_time >= boundary]
    return train, oos


def thirds(trades: List[CompletedTrade]) -> List[List[CompletedTrade]]:
    """Chronological thirds of the trade list (robustness-across-time check)."""
    if not trades:
        return [[], [], []]
    ts = sorted(trades, key=lambda t: t.signal_time)
    k = len(ts)
    a, b = k // 3, 2 * k // 3
    return [ts[:a], ts[a:b], ts[b:]]
