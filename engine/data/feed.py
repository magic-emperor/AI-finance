"""
feed.py — The single, swappable data interface.

Strategy / backtest / paper code only ever talk to a DataFeed. Today the default
feed serves crypto + US equities from yfinance (with caching). Later, an
IndianFeed (AngelOne/Kite) implements the SAME interface and nothing downstream
changes. This is the one place the data source is allowed to differ.
"""
from __future__ import annotations

import pandas as pd
from typing import Optional, Protocol

from engine.data import sources, cache
from engine import config


class DataFeed(Protocol):
    def get_ohlcv(self, symbol: str, interval: str, bars: int) -> Optional[pd.DataFrame]:
        ...


class CryptoUSFeed:
    """Backtest/paper feed for crypto + US equities via yfinance (cached)."""

    def __init__(self, cache_dir: str = None, force_refresh: bool = False):
        self.cache_dir = cache_dir or config.CACHE_DIR
        self.force_refresh = force_refresh

    def get_ohlcv(self, symbol: str, interval: str = None, bars: int = None) -> Optional[pd.DataFrame]:
        interval = interval or config.INTERVAL
        bars = bars or config.FETCH_BARS
        return cache.get_or_fetch(
            symbol, interval, bars,
            fetch_fn=sources.fetch_yfinance_ohlcv,
            cache_dir=self.cache_dir,
            force_refresh=self.force_refresh,
        )
