"""
meanrev.py — Track B: a naturally HIGH-WIN-RATE mean-reversion strategy
(Connors RSI-2 style), to compare honestly against the trend system.

Idea: in a long-term uptrend (price > 200-SMA), buy short-term OVERSOLD dips
(RSI(2) very low, price below the 5-day mean) and exit when price reverts UP to
the mean. You win often (the dip usually bounces) — but the tail risk is real:
when a dip keeps falling, the ATR stop takes a full loss. That tradeoff (high win
rate, fatter tail) is exactly what we want the user to SEE side-by-side.

Reuses the baseline `simulate()` (fixed stop + target): target = the 5-day mean
(revert-to-mean), stop = ATR-based (the tail cap). Long-only (buy dips in uptrends).
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from engine.strategy.base import Trade
from engine.indicators import calc_atr, calc_sma, calc_rsi_float
from engine import config


class MeanRevStrategy:
    name = "meanrev"

    def __init__(self, params: dict = None):
        p = dict(config.MEANREV)
        if params:
            p.update(params)
        self.p = p
        self.interval = p["interval"]

    def signals(self, bars: pd.DataFrame, symbol: str) -> Optional[Trade]:
        p = self.p
        if bars is None or len(bars) < p["sma_trend"] + 5:
            return None
        window = bars.tail(p["lookback_bars"])

        atr = calc_atr(window, 14)
        if atr <= 0:
            return None
        sma_trend = calc_sma(window, p["sma_trend"])
        sma_exit = calc_sma(window, p["exit_sma"])
        if sma_trend <= 0 or sma_exit <= 0:
            return None
        rsi = calc_rsi_float(window, p["rsi_period"])
        close = float(window["Close"].iloc[-1])
        st = window.index[-1]

        # Buy an oversold dip inside a long-term uptrend; target = revert to the mean.
        if close > sma_trend and rsi < p["rsi_entry"] and close < sma_exit:
            stop = close - p["stop_atr_mult"] * atr
            risk = close - stop
            target = sma_exit                     # the mean (above current close)
            if risk <= 0 or target <= close:
                return None
            conviction = min(1.0, (sma_exit - close) / atr)
            return Trade(symbol, st, "LONG", close, stop, target, conviction,
                         reason=f"rsi2={rsi:.0f} dip<sma{p['exit_sma']}")
        return None
