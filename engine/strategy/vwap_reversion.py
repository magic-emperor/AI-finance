"""
vwap_reversion.py — Documented fallback strategy (only used if breakout fails
its Go/No-Go gate). A complementary regime: breakout is momentum; this fades
extremes in QUIET (ranging) markets.

Rules: only when ADX < adx_max (no strong trend). Go LONG when price has pierced
below the rolling VWAP by a band and RSI is oversold AND the bar closes back up
(rejection); mirror for SHORT. Stop just beyond the trigger extreme; first target
= VWAP. Require >= min_reward_risk or skip.

Self-contained and causal.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from engine.strategy.base import Trade
from engine.indicators import calc_atr, calc_adx, calc_vwap, calc_rsi_float
from engine import config


class VwapReversionStrategy:
    name = "vwap_reversion"

    def __init__(self, params: dict = None):
        p = dict(config.VWAP_REVERSION)
        if params:
            p.update(params)
        self.p = p

    def signals(self, bars: pd.DataFrame, symbol: str) -> Optional[Trade]:
        p = self.p
        if bars is None or len(bars) < p["lookback_bars"]:
            return None
        window = bars.tail(p["lookback_bars"])

        atr = calc_atr(window, p["atr_period"])
        if atr <= 0:
            return None
        if calc_adx(window, p["atr_period"]) >= p["adx_max"]:
            return None  # only fade in ranging conditions

        vwap = calc_vwap(window, p["vwap_window"])
        rsi = calc_rsi_float(window, p["rsi_period"])
        bar = window.iloc[-1]
        close = float(bar["Close"])
        low = float(bar["Low"])
        high = float(bar["High"])
        signal_time = window.index[-1]

        # LONG: dipped below VWAP, oversold, closed back up (rejection of lows)
        if low < vwap and rsi < p["rsi_low"] and close > low:
            stop = low - p["stop_atr_mult"] * atr
            risk = close - stop
            target = vwap
            if risk > 0 and (target - close) / risk >= p["min_reward_risk"]:
                conviction = min(1.0, (vwap - low) / atr)
                return Trade(symbol, signal_time, "LONG", close, stop, target, conviction,
                             reason=f"vwap-fade rsi={rsi:.0f}")

        # SHORT: spiked above VWAP, overbought, closed back down
        if high > vwap and rsi > p["rsi_high"] and close < high:
            stop = high + p["stop_atr_mult"] * atr
            risk = stop - close
            target = vwap
            if risk > 0 and (close - target) / risk >= p["min_reward_risk"]:
                conviction = min(1.0, (high - vwap) / atr)
                return Trade(symbol, signal_time, "SHORT", close, stop, target, conviction,
                             reason=f"vwap-fade rsi={rsi:.0f}")

        return None
