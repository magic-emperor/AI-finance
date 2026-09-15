"""
regime.py — Price-based trend/regime filter (Phase-1 news/macro layer).

The key insight from the plan: when a market is falling for weeks because money
is leaving (the kind of macro move the user described), it shows up in the PRICE
as a downtrend. So we get most of the "news awareness" for free from price — no
article needed — by only taking longs in a non-down regime and shorts in a
non-up regime.

Causal: uses only the bars passed in.
"""
from __future__ import annotations

import pandas as pd
from engine.indicators import calc_sma


def trend_ok(bars: pd.DataFrame, direction: str, ma_period: int) -> bool:
    """Allow LONG only when last close is above the SMA; SHORT only when below."""
    sma = calc_sma(bars, ma_period)
    if sma <= 0:
        return False  # not enough data → no trade
    close = float(bars["Close"].iloc[-1])
    if direction == "LONG":
        return close > sma
    if direction == "SHORT":
        return close < sma
    return False
