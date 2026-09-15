"""
cache.py — Simple on-disk cache for fetched OHLCV (pickle; no pyarrow needed).

Backtest data barely changes, so we cache aggressively to avoid hammering
yfinance on every run. TTL is generous; delete the cache_dir to force a refresh.
"""
from __future__ import annotations

import os
import time
import logging
import pandas as pd
from typing import Optional, Callable

logger = logging.getLogger("engine.data.cache")


def _safe_name(symbol: str, interval: str) -> str:
    safe = symbol.replace("/", "_").replace("\\", "_").replace("=", "_").replace("^", "idx_")
    return f"{safe}__{interval}.pkl"


def get_or_fetch(
    symbol: str,
    interval: str,
    bars: int,
    fetch_fn: Callable[[str, str, int], Optional[pd.DataFrame]],
    cache_dir: str,
    ttl_seconds: float = 12 * 3600,
    force_refresh: bool = False,
) -> Optional[pd.DataFrame]:
    """Return cached DataFrame if fresh, else fetch, cache, and return."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, _safe_name(symbol, interval))

    if not force_refresh and os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < ttl_seconds:
            try:
                df = pd.read_pickle(path)
                logger.info("cache hit %s (%d bars, age %.0fm)", symbol, len(df), age / 60)
                return df
            except Exception as e:
                logger.warning("cache read failed %s: %s", symbol, str(e)[:80])

    df = fetch_fn(symbol, interval, bars)
    if df is not None and not df.empty:
        try:
            df.to_pickle(path)
        except Exception as e:
            logger.warning("cache write failed %s: %s", symbol, str(e)[:80])
        logger.info("fetched %s (%d bars)", symbol, len(df))
    return df
