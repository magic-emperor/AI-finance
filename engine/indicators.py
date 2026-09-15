"""
indicators.py — Pure, causal indicator math.

Copied and trimmed from the old market_agent/brain/brain_utils.py (the one part
of the old system that was actually sound). Every function here is CAUSAL: the
value at row i depends only on rows <= i. That property is what lets the
backtest precompute on a full series without introducing look-ahead.

No imports from market_agent. Self-contained.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from typing import Tuple


def calc_atr(hist: pd.DataFrame, period: int = 14) -> float:
    """ATR via Wilder's rolling mean on True Range. 0.0 on insufficient data.

    IMPORTANT: 0.0 means "not enough data / no volatility estimate" → the
    strategy must treat it as 'no trade'. We do NOT fake a fallback like
    price*0.008 (that bug was in the old backtester)."""
    if hist is None or len(hist) < period + 1:
        return 0.0
    high, low = hist["High"], hist["Low"]
    prev_close = hist["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    result = tr.rolling(period).mean().iloc[-1]
    return float(result) if not pd.isna(result) else 0.0


def calc_rsi_series(hist: pd.DataFrame, period: int = 14) -> pd.Series:
    """RSI as a Series using Wilder's EMA smoothing (the correct method)."""
    close = hist["Close"]
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def calc_rsi_float(hist: pd.DataFrame, period: int = 14) -> float:
    """RSI as a single float (last value). 50.0 on insufficient data."""
    if hist is None or len(hist) < period + 1:
        return 50.0
    s = calc_rsi_series(hist, period)
    val = s.iloc[-1]
    return float(val) if not pd.isna(val) else 50.0


def calc_sma(hist: pd.DataFrame, period: int) -> float:
    """Simple moving average of Close (last value). 0.0 on insufficient data."""
    if hist is None or len(hist) < period:
        return 0.0
    val = hist["Close"].rolling(period).mean().iloc[-1]
    return float(val) if not pd.isna(val) else 0.0


def calc_vwap(hist: pd.DataFrame, window: int = 20) -> float:
    """Rolling VWAP over the last `window` bars. Falls back to last close."""
    if hist is None or len(hist) == 0:
        return 0.0
    if len(hist) < window:
        return float(hist["Close"].iloc[-1])
    need = ["High", "Low", "Close", "Volume"]
    if not all(c in hist.columns for c in need):
        return float(hist["Close"].iloc[-1])
    df = hist.tail(window)
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol_sum = df["Volume"].sum()
    if vol_sum <= 0:
        return float(df["Close"].iloc[-1])
    return float((typical * df["Volume"]).sum() / vol_sum)


def calc_adx(hist: pd.DataFrame, period: int = 14) -> float:
    """ADX trend-strength (0-100). >25 strong trend, <20 ranging. 15.0 fallback."""
    if hist is None or len(hist) < period * 2 + 1:
        return 15.0
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    up = high.diff()
    down = low.diff()
    plus_dm = up.where((up > down.abs()) & (up > 0), 0.0)
    minus_dm = down.abs().where((down.abs() > up) & (down < 0), 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr.replace(0, float("nan"))
    minus_di = 100 * minus_dm.rolling(period).mean() / atr.replace(0, float("nan"))
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    val = dx.rolling(period).mean().iloc[-1]
    return float(val) if not pd.isna(val) else 15.0


def prior_session_levels(hist: pd.DataFrame) -> Tuple[float, float]:
    """High/Low of the PREVIOUS calendar day (UTC) relative to the last bar.

    For US equities the regular session (09:30-16:00 ET = 13:30-21:00 UTC) lives
    entirely within one UTC date, so grouping by UTC date cleanly separates
    trading days. For 24/7 crypto it's simply the prior UTC day.

    Returns (prior_high, prior_low), or (nan, nan) if there isn't a full prior day.
    """
    if hist is None or len(hist) < 2:
        return (float("nan"), float("nan"))
    idx = hist.index
    if getattr(idx, "tz", None) is None:
        dates = pd.to_datetime(idx).date
    else:
        dates = idx.tz_convert("UTC").date
    s = pd.Series(range(len(hist)), index=dates)
    unique_dates = list(dict.fromkeys(dates))  # preserves order, dedups
    if len(unique_dates) < 2:
        return (float("nan"), float("nan"))
    prior_date = unique_dates[-2]
    mask = np.array([d == prior_date for d in dates])
    prior = hist.iloc[mask]
    if prior.empty:
        return (float("nan"), float("nan"))
    return (float(prior["High"].max()), float(prior["Low"].min()))
