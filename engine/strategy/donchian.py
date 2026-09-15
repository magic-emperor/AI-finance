"""
donchian.py — Track B. Daily Donchian trend breakout (swing, Turtle-style).

The higher-probability path to a real edge: on daily bars, moves are several
percent so transaction costs barely matter, and trend-following has decades of
documented (if cyclical) edge. Naturally "few high-conviction trades" — it only
fires on genuine multi-week breakouts, and winners are allowed to run.

Entry: close breaks above the prior `channel`-day high AND is above the long-term
trend (200-day SMA). Stop a couple of ATRs back; let the target be a generous R
(a trailing exit is a pre-registered variant if this is close).

Self-contained and causal. Runs through the SAME simulator with no changes.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from engine.strategy.base import Trade
from engine.indicators import calc_atr, calc_sma
from engine import config


class DonchianStrategy:
    name = "donchian"

    def __init__(self, params: dict = None):
        p = dict(config.DONCHIAN)
        if params:
            p.update(params)
        self.p = p
        self.interval = p["interval"]

    def signals(self, bars: pd.DataFrame, symbol: str) -> Optional[Trade]:
        p = self.p
        need = max(p["channel"] + 1, p["sma_trend"])
        if bars is None or len(bars) < need:
            return None
        window = bars.tail(p["lookback_bars"])

        atr = calc_atr(window, p["atr_period"])
        if atr <= 0:
            return None
        sma = calc_sma(window, p["sma_trend"])
        if sma <= 0:
            return None

        prior = window.iloc[-(p["channel"] + 1):-1]   # prior N bars, excluding current
        chan_high = float(prior["High"].max())
        chan_low = float(prior["Low"].min())
        close = float(window["Close"].iloc[-1])
        st = window.index[-1]

        if close > chan_high and close > sma:
            stop = close - p["stop_atr_mult"] * atr
            risk = close - stop
            if risk <= 0:
                return None
            target = close + p["target_r"] * risk
            conv = min(1.0, (close - chan_high) / atr)
            return Trade(symbol, st, "LONG", close, stop, target, conv, f"donch>{chan_high:.2f}")

        if not p["long_only"] and close < chan_low and close < sma:
            stop = close + p["stop_atr_mult"] * atr
            risk = stop - close
            if risk <= 0:
                return None
            target = close - p["target_r"] * risk
            conv = min(1.0, (chan_low - close) / atr)
            return Trade(symbol, st, "SHORT", close, stop, target, conv, f"donch<{chan_low:.2f}")

        return None
