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


class MarketAgentPostgresFeed:
    """
    NSE (and any other market_agent-covered symbol) feed backed DIRECTLY by
    the validated Postgres store in market_agent/data/storage/postgres.py --
    NOT a live yfinance call. That store was cross-validated against ICICI's
    own broker feed on 2026-09-13 (0.000000 drift across 2,152 bar-pairs) and
    passes 11 integrity gates (no duplicate bars, no duplicate trading days,
    single source per series, OHLC invariants, etc. -- see
    market_agent/scripts/verify_rebuild.py).

    Deliberately does NOT fall back to a live fetch on short/missing data --
    that silent-substitution pattern is what caused a backtest to silently
    run 28% of its trades on unvalidated data on 2026-09-13. A symbol with
    insufficient validated history is skipped and logged, not patched over.
    """

    def __init__(self):
        pass

    def get_ohlcv(self, symbol: str, interval: str = None, bars: int = None) -> Optional[pd.DataFrame]:
        from market_agent.data.storage.postgres import PostgresStorage
        interval = interval or "1d"
        bars = bars or 5000
        storage = PostgresStorage()
        rows = storage.get_latest_data(symbol, interval, limit=bars)
        if not rows:
            return None
        df = pd.DataFrame(
            [r["data"] for r in rows], index=[r["timestamp"] for r in rows]
        ).sort_index()
        for col in ("Open", "High", "Low", "Close"):
            if col not in df.columns:
                return None
        if "Volume" not in df.columns:
            df["Volume"] = 0
        # market_agent's store is naive-UTC (normalize_bar_timestamp); engine's
        # simulator/walk-forward code compares against tz-aware UTC timestamps
        # (`pd.Timestamp(start, tz="UTC")` in run_backtest.collect_trades), so
        # localize explicitly rather than let a naive/aware comparison raise.
        if df.index.tz is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")
        return df[["Open", "High", "Low", "Close", "Volume"]]
