"""
breakout.py — The first strategy. This is the user's own idea, made precise.

"Watch the high/low of the prior session; if price breaks it, go with the break
(bullish above, bearish below)." We only take the break when filters confirm it
is a clean move, not a fake-out:
  - Volatility sane: ATR available and the break clears the level by >= min_break_atr*ATR.
  - Volume backs it: the break bar's volume >= median of recent bars.
  - Trend agrees: longs only above the trend SMA, shorts only below (regime filter).

Risk is structural: stop = the tighter of {prior level, entry ∓ 1*ATR}; target = 2R.
Conviction = how far past the level we broke (in ATR units) — used only to RANK
which symbol to take when several fire; it never changes position size.

Self-contained and causal: reads only the tail of the bars passed in.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional

from engine.strategy.base import Trade
from engine.indicators import calc_atr, prior_session_levels
from engine.filters.regime import trend_ok
from engine import config


class BreakoutStrategy:
    name = "breakout"

    def __init__(self, params: dict = None):
        p = dict(config.BREAKOUT)
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
            return None  # no volatility estimate → no trade (no faked fallback)

        prior_high, prior_low = prior_session_levels(window)
        if pd.isna(prior_high) or pd.isna(prior_low):
            return None  # need a full prior session

        bar = window.iloc[-1]
        close = float(bar["Close"])
        vol = float(bar["Volume"])
        vol_median = float(window["Volume"].tail(p["vol_lookback"]).median())
        if vol_median <= 0 or vol < vol_median:
            return None  # break not backed by participation

        min_clear = p["min_break_atr"] * atr
        signal_time = window.index[-1]

        # ── Bullish break above prior-session high ──
        if close > prior_high + min_clear and trend_ok(window, "LONG", p["trend_ma"]):
            stop = max(prior_low, close - p["stop_atr_mult"] * atr)  # tighter of the two
            risk = close - stop
            if risk <= 0:
                return None
            target = close + p["target_r"] * risk
            conviction = min(1.0, (close - prior_high) / atr)
            return Trade(symbol, signal_time, "LONG", close, stop, target, conviction,
                         reason=f"break>{prior_high:.2f} vol>{vol_median:.0f} atr={atr:.2f}")

        # ── Bearish break below prior-session low ──
        if close < prior_low - min_clear and trend_ok(window, "SHORT", p["trend_ma"]):
            stop = min(prior_high, close + p["stop_atr_mult"] * atr)  # tighter of the two
            risk = stop - close
            if risk <= 0:
                return None
            target = close - p["target_r"] * risk
            conviction = min(1.0, (prior_low - close) / atr)
            return Trade(symbol, signal_time, "SHORT", close, stop, target, conviction,
                         reason=f"break<{prior_low:.2f} vol>{vol_median:.0f} atr={atr:.2f}")

        return None
