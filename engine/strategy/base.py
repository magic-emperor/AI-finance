"""
base.py — The ONE contract every strategy implements.

This is the keystone that prevents backtest/live divergence: the backtest and
the paper-trading loop both call `strategy.signals(bars, symbol)` with the SAME
code. Only the source of `bars` differs (a historical slice vs the latest closed
bar). There is no second implementation to drift.

CONTRACT — `bars` contains ONLY bars up to and including the decision bar. A
strategy must never read beyond the last row (no look-ahead). The actual fill
happens at the NEXT bar's open, simulated downstream — `entry` here is only a
reference price (the decision-bar close).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol
import pandas as pd


@dataclass(frozen=True)
class Trade:
    symbol: str
    signal_time: pd.Timestamp   # the decision bar (signal computed on bars up to here)
    direction: str              # "LONG" | "SHORT"
    entry: float                # reference price (decision-bar close); real fill = next open
    stop: float                 # price level
    target: float               # price level (2R by construction)
    conviction: float           # 0..1 — ranks/selects only; NEVER changes risk size
    reason: str


class Strategy(Protocol):
    name: str

    def signals(self, bars: pd.DataFrame, symbol: str) -> Optional[Trade]:
        """Return a Trade if the decision bar (last row of `bars`) triggers a
        setup, else None. Must read only `bars` (no look-ahead)."""
        ...
