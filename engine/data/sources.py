"""
sources.py — Raw OHLCV fetchers.

Pure functions copied/trimmed from the old unified_market_data.py. They return a
DataFrame indexed by UTC datetime with columns [Open, High, Low, Close, Volume]
(crypto also has TakerBase). No DB, no broker entanglement.

For the Phase-1 BACKTEST we use yfinance for both crypto and US equities (one
consistent ~730d/1h history). Binance is included for live/paper use later.
"""
from __future__ import annotations

import logging
import requests
import pandas as pd
from typing import Optional

logger = logging.getLogger("engine.data.sources")


def classify_symbol(symbol: str) -> str:
    """Returns asset class: crypto / indian_equity / forex / commodity / index / unknown."""
    s = symbol.upper()
    if any(s.endswith(x) for x in ["-USD", "-USDT", "-BTC"]) or s in ("BTC", "ETH", "SOL", "XRP"):
        return "crypto"
    if s.endswith(".NS") or s.endswith(".BO"):
        return "indian_equity"
    if "=X" in s or "/" in s:
        return "forex"
    if "=F" in s:
        return "commodity"
    if s.startswith("^"):
        return "index"
    return "unknown"


def to_binance_symbol(symbol: str) -> Optional[str]:
    """Convert BTC-USD / BTC/USDT → BTCUSDT."""
    s = symbol.upper().replace("-", "").replace("/", "")
    if s.endswith("USD") and not s.endswith("USDT"):
        s = s + "T"
    return s if len(s) >= 6 else None


BINANCE_INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}

YFINANCE_MAX_PERIOD = {
    "1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d", "30m": "60d",
    "1h": "730d", "1d": "10y",
}


def fetch_yfinance_ohlcv(symbol: str, interval: str = "1h", bars: int = 20000) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from yfinance. UTC-indexed [Open, High, Low, Close, Volume]."""
    try:
        import yfinance as yf
        period = YFINANCE_MAX_PERIOD.get(interval, "60d")
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if df is None or df.empty:
            logger.warning("yfinance empty for %s", symbol)
            return None
        df = df.rename(columns=str.title)
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        # Normalise index to tz-aware UTC for consistent date grouping.
        idx = pd.to_datetime(df.index)
        if getattr(idx, "tz", None) is None:
            idx = idx.tz_localize("UTC")
        else:
            idx = idx.tz_convert("UTC")
        df.index = idx
        df.index.name = "Datetime"
        if len(df) > bars:
            df = df.iloc[-bars:]
        return df
    except Exception as e:
        logger.error("yfinance fetch failed for %s: %s", symbol, str(e)[:120])
        return None


def fetch_binance_ohlcv(symbol: str, interval: str = "1h", bars: int = 1000) -> Optional[pd.DataFrame]:
    """Fetch OHLCV from Binance REST (max 1000 bars/call). For live/paper use."""
    binance_sym = to_binance_symbol(symbol)
    if not binance_sym:
        return None
    b_interval = BINANCE_INTERVAL_MAP.get(interval, interval)
    try:
        url = (f"https://api.binance.com/api/v3/klines"
               f"?symbol={binance_sym}&interval={b_interval}&limit={min(bars, 1000)}")
        resp = requests.get(url, timeout=8)
        if resp.status_code != 200:
            logger.warning("binance %s status %s", symbol, resp.status_code)
            return None
        data = resp.json()
        if not data:
            return None
        df = pd.DataFrame(data, columns=[
            "OpenTime", "Open", "High", "Low", "Close", "Volume",
            "CloseTime", "QuoteVol", "Trades", "TakerBase", "TakerQuote", "Ignore",
        ])
        for col in ("Open", "High", "Low", "Close", "Volume", "TakerBase"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.index = pd.to_datetime(df["OpenTime"], unit="ms", utc=True)
        df.index.name = "Datetime"
        return df[["Open", "High", "Low", "Close", "Volume", "TakerBase"]].dropna()
    except Exception as e:
        logger.error("binance fetch failed for %s: %s", symbol, str(e)[:120])
        return None
