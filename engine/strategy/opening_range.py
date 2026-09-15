"""
opening_range.py — Track A. The documented opening-range breakout (15m).

Idea (well-studied, e.g. Zarattini/Concretum): the first part of the session sets
a range; a clean break of that range tends to run on trend days. We define the
opening range as the first `or_bars` bars of the session, then take the first
later bar that CLOSES beyond it (with volatility, volume and trend filters).

Session = UTC date (the US regular session sits within one UTC date). One trade
per session is enforced by the simulator's one-trade-per-day rule.

Self-contained and causal.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from engine.strategy.base import Trade
from engine.indicators import calc_atr
from engine.filters.regime import trend_ok
from engine import config


class OpeningRangeStrategy:
    name = "opening_range"

    def __init__(self, params: dict = None):
        p = dict(config.OPENING_RANGE)
        if params:
            p.update(params)
        self.p = p
        self.interval = p["interval"]

    def signals(self, bars: pd.DataFrame, symbol: str) -> Optional[Trade]:
        p = self.p
        if bars is None or len(bars) < p["lookback_bars"]:
            return None
        window = bars.tail(p["lookback_bars"])

        atr = calc_atr(window, p["atr_period"])
        if atr <= 0:
            return None

        idx = window.index
        date_arr = idx.tz_convert("UTC").date if getattr(idx, "tz", None) is not None else idx.date
        cur = date_arr[-1]
        session = window[date_arr == cur]
        or_bars = p["or_bars"]
        if len(session) <= or_bars:
            return None  # opening range still forming, or current bar is inside it

        or_high = float(session["High"].iloc[:or_bars].max())
        or_low = float(session["Low"].iloc[:or_bars].min())

        bar = session.iloc[-1]
        close = float(bar["Close"])
        vol = float(bar["Volume"])
        vol_med = float(session["Volume"].median())
        if vol_med <= 0 or vol < vol_med:
            return None

        min_clear = p["min_break_atr"] * atr
        st = window.index[-1]

        if close > or_high + min_clear and trend_ok(window, "LONG", p["trend_ma"]):
            stop = max(or_low, close - p["stop_atr_mult"] * atr)
            risk = close - stop
            if risk <= 0:
                return None
            target = close + p["target_r"] * risk
            conv = min(1.0, (close - or_high) / atr)
            return Trade(symbol, st, "LONG", close, stop, target, conv, f"OR>{or_high:.2f}")

        if close < or_low - min_clear and trend_ok(window, "SHORT", p["trend_ma"]):
            stop = min(or_high, close + p["stop_atr_mult"] * atr)
            risk = stop - close
            if risk <= 0:
                return None
            target = close - p["target_r"] * risk
            conv = min(1.0, (or_low - close) / atr)
            return Trade(symbol, st, "SHORT", close, stop, target, conv, f"OR<{or_low:.2f}")

        return None
