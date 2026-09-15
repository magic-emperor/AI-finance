"""
simulator.py — The honest fill engine. This is where most of the old system's
lies are fixed:

  - NO look-ahead: the signal is computed on bars[:i] and the fill happens at the
    NEXT bar's open. We never peek into the future to decide.
  - Symmetric, realistic costs: slippage AND fees on BOTH entry and exit.
  - Pessimistic intrabar tie: if a bar's range touches BOTH stop and target, we
    assume the STOP was hit first.
  - Honest P&L in R: a stop hit is a FULL loss (~ -1R, slightly worse after
    costs). There is NO partial credit — the exact bug from signal_resolver.py.
  - Time stop: if neither level hits within max_hold_bars, exit at that bar's
    close (honest mark-to-close), so trades don't drift overnight forever.

One position at a time per symbol (the cursor jumps past each exit), which also
keeps the system to "few high-conviction trades."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List
import pandas as pd

from engine.strategy.base import Strategy


@dataclass
class CompletedTrade:
    symbol: str
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    entry_fill: float
    exit_price: float
    stop: float
    target: float
    risk_per_unit: float
    R: float              # net R, after costs (the truth)
    gross_R: float        # cost-free R (diagnostic: is the signal positive before costs?)
    mfe_r: float          # max favorable excursion during hold, in R (left-on-table check)
    mae_r: float          # max adverse excursion during hold, in R (heat taken)
    outcome: str          # "TARGET" | "STOP" | "TIME"
    conviction: float
    reason: str
    bars_held: int


def simulate(df: pd.DataFrame, strategy: Strategy, symbol: str,
             costs: dict, max_hold_bars: int,
             one_trade_per_day: bool = True) -> List[CompletedTrade]:
    fee = (costs.get("fee_bps", 0.0) + costs.get("extra_bps", 0.0)) / 10000.0
    slip = costs.get("slippage_bps", 0.0) / 10000.0

    n = len(df)
    if n < 3:
        return []
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    index = df.index

    trades: List[CompletedTrade] = []
    i = 0
    while i < n - 1:
        sig = strategy.signals(df.iloc[: i + 1], symbol)
        if sig is None:
            i += 1
            continue

        entry_idx = i + 1
        raw_entry = float(opens[entry_idx])
        long = sig.direction == "LONG"
        entry_fill = raw_entry * (1 + slip) if long else raw_entry * (1 - slip)
        stop, target = sig.stop, sig.target
        risk_per_unit = (entry_fill - stop) if long else (stop - entry_fill)
        if risk_per_unit <= 0:
            i += 1
            continue

        exit_price = None
        outcome = None
        exit_idx = None
        hi_max = float("-inf")   # for MFE/MAE over the holding window
        lo_min = float("inf")
        last_j = min(entry_idx + max_hold_bars, n - 1)
        for j in range(entry_idx, last_j + 1):
            hi, lo = float(highs[j]), float(lows[j])
            hi_max = max(hi_max, hi)
            lo_min = min(lo_min, lo)
            if long:
                if lo <= stop:                       # stop first on tie (pessimistic)
                    exit_price, outcome, exit_idx = stop, "STOP", j; break
                if hi >= target:
                    exit_price, outcome, exit_idx = target, "TARGET", j; break
            else:
                if hi >= stop:
                    exit_price, outcome, exit_idx = stop, "STOP", j; break
                if lo <= target:
                    exit_price, outcome, exit_idx = target, "TARGET", j; break
        if exit_price is None:                       # time stop → mark to close
            exit_idx = last_j
            exit_price = float(closes[last_j])
            outcome = "TIME"

        exit_fill = exit_price * (1 - slip) if long else exit_price * (1 + slip)
        gross = (exit_fill - entry_fill) if long else (entry_fill - exit_fill)
        fees = fee * (entry_fill + exit_fill)
        net = gross - fees
        R = net / risk_per_unit

        # gross (cost-free) R: raw open entry, exit at the level price, no fees/slippage
        risk_gross = (raw_entry - stop) if long else (stop - raw_entry)
        if risk_gross > 0:
            gross_move = (exit_price - raw_entry) if long else (raw_entry - exit_price)
            gross_R = gross_move / risk_gross
            mfe_r = ((hi_max - raw_entry) if long else (raw_entry - lo_min)) / risk_gross
            mae_r = ((raw_entry - lo_min) if long else (hi_max - raw_entry)) / risk_gross
        else:
            gross_R = mfe_r = mae_r = 0.0

        trades.append(CompletedTrade(
            symbol=symbol, signal_time=index[i], entry_time=index[entry_idx],
            exit_time=index[exit_idx], direction=sig.direction,
            entry_fill=entry_fill, exit_price=exit_price, stop=stop, target=target,
            risk_per_unit=risk_per_unit, R=R, gross_R=gross_R, mfe_r=mfe_r, mae_r=mae_r,
            outcome=outcome, conviction=sig.conviction, reason=sig.reason,
            bars_held=exit_idx - entry_idx,
        ))
        i = exit_idx + 1  # no overlapping positions
        if one_trade_per_day:
            # Selectivity: at most one trade per symbol per calendar day (UTC).
            # Take only the FIRST qualifying break of the day, then stand down.
            entry_date = index[entry_idx].date()
            while i < n and index[i].date() <= entry_date:
                i += 1

    return trades
