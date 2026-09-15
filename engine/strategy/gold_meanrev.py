"""
gold_meanrev.py — Long-horizon mean-reversion, sized for gold's documented cycle.

Literature grounding (registered before this file was written, per the
hypothesis-registry step-0 discipline): gold is NOT a simple trend instrument.
Published research describes 8-14 month price cycles around a slow-moving
mean, and safe-haven "flight to gold" behavior that is CONDITIONAL on the
cause of market stress (works for macro/geopolitical shocks, not commodity-
or election-driven selloffs) -- i.e. gold overshoots a long-run mean in both
directions and tends to revert, rather than trending persistently like
equities or the currency/commodity momentum Moskowitz/Ooi/Pedersen document.

This is a DIFFERENT hypothesis from `meanrev.py` (Connors RSI-2: short-horizon
oversold-dip-buying WITHIN a confirmed uptrend, days-scale) -- that strategy
requires price > 200-SMA and buys 2-5 day dips. Gold's documented cycle is
8-14 MONTHS, roughly 168-294 trading days, and the reversion is bidirectional
(no uptrend precondition) -- so this strategy measures deviation from a
~250-day (~1yr, inside the 8-14mo window) mean via a z-score, entering
LONG when price has fallen unusually far below it and SHORT when it has
risen unusually far above it. An ADX filter avoids fading genuine strong
trends (this session's own Donchian test found gold DOES trend positively
some of the time -- GC=F +0.337R OOS -- so this is a real, not theoretical,
risk of the two hypotheses colliding).

Reuses the baseline `simulate()` (fixed stop + target): target = the long-run
mean (revert-to-mean), stop = ATR-based (tail protection against the trend
continuing instead of reverting).
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from engine.strategy.base import Trade
from engine.indicators import calc_atr, calc_sma, calc_adx
from engine import config


class GoldMeanRevStrategy:
    name = "gold_meanrev"

    def __init__(self, params: dict = None):
        p = dict(config.GOLD_MEANREV)
        if params:
            p.update(params)
        self.p = p
        self.interval = p["interval"]

    def signals(self, bars: pd.DataFrame, symbol: str) -> Optional[Trade]:
        p = self.p
        if bars is None or len(bars) < p["sma_cycle"] + p["zscore_lookback"]:
            return None
        window = bars.tail(p["lookback_bars"])

        atr = calc_atr(window, p["atr_period"])
        if atr <= 0:
            return None
        sma_cycle = calc_sma(window, p["sma_cycle"])
        if sma_cycle <= 0:
            return None
        adx = calc_adx(window, p["adx_period"])
        if adx is None or adx > p["adx_max"]:
            return None  # avoid fading a genuine strong trend

        close = float(window["Close"].iloc[-1])
        deviation = window["Close"].tail(p["sma_cycle"]) - sma_cycle
        std = float(deviation.tail(p["zscore_lookback"]).std())
        if std <= 0:
            return None
        z = (close - sma_cycle) / std
        st = window.index[-1]

        if z <= -p["entry_z"]:
            # price unusually far BELOW the long-run mean -> bet on reversion up
            stop = close - p["stop_atr_mult"] * atr
            risk = close - stop
            target = sma_cycle
            if risk <= 0 or target <= close:
                return None
            conviction = min(1.0, abs(z) / (p["entry_z"] * 2))
            return Trade(symbol, st, "LONG", close, stop, target, conviction,
                        reason=f"z={z:+.2f} adx={adx:.0f} revert-to-sma{p['sma_cycle']}")

        if z >= p["entry_z"] and not p["long_only"]:
            # price unusually far ABOVE the long-run mean -> bet on reversion down
            stop = close + p["stop_atr_mult"] * atr
            risk = stop - close
            target = sma_cycle
            if risk <= 0 or target >= close:
                return None
            conviction = min(1.0, abs(z) / (p["entry_z"] * 2))
            return Trade(symbol, st, "SHORT", close, stop, target, conviction,
                        reason=f"z={z:+.2f} adx={adx:.0f} revert-to-sma{p['sma_cycle']}")

        return None
