"""
latency_stress.py — robustness-gate check #3: does the edge survive a delayed fill?

A parallel variant of simulate() (never edits the frozen baseline), identical in
every respect except WHEN the entry and exit fills happen: `extra_delay_bars` extra
bars pass between the decision and the fill, representing real execution lag
(order routing, a slow broker API, a stale price feed) rather than a fixed cost.
This is a genuinely different failure mode than the 2x-cost stress test: a delay
exposes the strategy to whatever the market actually did during the lag (which
could be favorable OR adverse), not a fixed haircut.

Stop/target are still set from the ORIGINAL signal bar's levels (a real system
decides risk parameters at signal time; only the fill lags) -- only the entry/exit
TIMING shifts.
"""
from __future__ import annotations

from typing import List

from engine.strategy.base import Strategy
from engine.backtest.simulator import CompletedTrade


def simulate_with_latency(df, strategy: Strategy, symbol: str, costs: dict,
                          max_hold_bars: int, extra_delay_bars: int = 1,
                          one_trade_per_day: bool = True) -> List[CompletedTrade]:
    fee = (costs.get("fee_bps", 0.0) + costs.get("extra_bps", 0.0)) / 10000.0
    slip = costs.get("slippage_bps", 0.0) / 10000.0

    n = len(df)
    if n < 3 + extra_delay_bars:
        return []
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    closes = df["Close"].values
    index = df.index

    trades: List[CompletedTrade] = []
    i = 0
    while i < n - 1 - extra_delay_bars:
        sig = strategy.signals(df.iloc[: i + 1], symbol)
        if sig is None:
            i += 1
            continue

        entry_idx = i + 1 + extra_delay_bars   # the only structural change vs simulate()
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
        hi_max = float("-inf")
        lo_min = float("inf")
        last_j = min(entry_idx + max_hold_bars, n - 1)
        for j in range(entry_idx, last_j + 1):
            hi, lo = float(highs[j]), float(lows[j])
            hi_max = max(hi_max, hi)
            lo_min = min(lo_min, lo)
            if long:
                if lo <= stop:
                    exit_price, outcome, exit_idx = stop, "STOP", j; break
                if hi >= target:
                    exit_price, outcome, exit_idx = target, "TARGET", j; break
            else:
                if hi >= stop:
                    exit_price, outcome, exit_idx = stop, "STOP", j; break
                if lo <= target:
                    exit_price, outcome, exit_idx = target, "TARGET", j; break
        if exit_price is None:
            exit_idx = last_j
            exit_price = float(closes[last_j])
            outcome = "TIME"

        exit_fill = exit_price * (1 - slip) if long else exit_price * (1 + slip)
        gross = (exit_fill - entry_fill) if long else (entry_fill - exit_fill)
        fees = fee * (entry_fill + exit_fill)
        net = gross - fees
        R = net / risk_per_unit

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
        i = exit_idx + 1
        if one_trade_per_day:
            entry_date = index[entry_idx].date()
            while i < n and index[i].date() <= entry_date:
                i += 1

    return trades
