"""
donchian_filtered.py — Track A: the SAME Donchian entry, plus pre-registered
confirmation filters to skip the weakest breakouts (the ones most likely to become
losers). Baseline `donchian.py` is untouched; this wraps it.

Filters (each a toggle, research-backed for cutting false breakouts):
  - volume      : breakout-bar volume >= vol_mult * 20-bar average
  - momentum    : RSI(14) at the breakout >= rsi_thr (real directional conviction)
  - two_close   : require the last TWO closes both beyond the channel (no 1-bar fakeout)

Discipline reminder: we adopt a filter combo only if it raises OOS EXPECTANCY (a
higher win rate is a bonus), never if it lowers expectancy or fattens the tail.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from engine.strategy.base import Trade
from engine.strategy.donchian import DonchianStrategy
from engine.indicators import calc_rsi_float
from engine import config


class DonchianFiltered:
    name = "donchian_filtered"

    def __init__(self, use_volume=False, use_momentum=False, use_two_close=False,
                 params: dict = None):
        self.base = DonchianStrategy(params)
        self.p = dict(config.DONCHIAN)
        if params:
            self.p.update(params)
        self.f = dict(config.ENTRY_FILTERS)
        self.use_volume = use_volume
        self.use_momentum = use_momentum
        self.use_two_close = use_two_close
        self.interval = self.p["interval"]

    def signals(self, bars: pd.DataFrame, symbol: str) -> Optional[Trade]:
        base = self.base.signals(bars, symbol)
        if base is None:
            return None
        window = bars.tail(self.p["lookback_bars"])

        if self.use_volume:
            vol = float(window["Volume"].iloc[-1])
            avg = float(window["Volume"].tail(20).mean())
            if avg <= 0 or vol < self.f["vol_mult"] * avg:
                return None

        if self.use_momentum and base.direction == "LONG":
            if calc_rsi_float(window, 14) < self.f["rsi_thr"]:
                return None
        if self.use_momentum and base.direction == "SHORT":
            if calc_rsi_float(window, 14) > (100.0 - self.f["rsi_thr"]):
                return None

        if self.use_two_close:
            chan = self.p["channel"]
            prior = window.iloc[-(chan + 1):-1]
            if base.direction == "LONG":
                level = float(prior["High"].max())
                if not (float(window["Close"].iloc[-1]) > level
                        and float(window["Close"].iloc[-2]) > level):
                    return None
            else:
                level = float(prior["Low"].min())
                if not (float(window["Close"].iloc[-1]) < level
                        and float(window["Close"].iloc[-2]) < level):
                    return None

        return base
