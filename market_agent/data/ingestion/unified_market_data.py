"""
═══════════════════════════════════════════════════════════════════════
UNIFIED MARKET DATA LAYER v2
Single interface for ALL symbols across ALL sources
with automatic fallback chains and thread safety.
═══════════════════════════════════════════════════════════════════════

USAGE:
    from data_fixes.unified_market_data import market_data
    
    # Live price (auto-selects best source)
    price = market_data.get_price('BTC-USD')    # → Binance WS
    price = market_data.get_price('ITC.NS')     # → AngelOne → Breeze → yfinance
    price = market_data.get_price('EURUSD=X')   # → yfinance (until OANDA added)
    
    # Historical OHLCV (for brains)
    df = market_data.get_ohlcv('BTC-USD', interval='1h', bars=200)
    df = market_data.get_ohlcv('ITC.NS', interval='1h', bars=200)
"""
from __future__ import annotations

import os
import time
import threading
import requests
import pandas as pd
import structlog
from typing import Optional, Dict, Tuple
from datetime import datetime, timedelta

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════
# SYMBOL ROUTING TABLE
# ═══════════════════════════════════════════════════════════════

def classify_symbol(symbol: str) -> str:
    """Returns asset class: 'crypto', 'indian_equity', 'forex', 'commodity', 'index'"""
    s = symbol.upper()
    if any(s.endswith(x) for x in ['-USD', '-USDT', '-BTC']) or s in ('BTC', 'ETH', 'SOL', 'XRP'):
        return 'crypto'
    if s.endswith('.NS') or s.endswith('.BO'):
        return 'indian_equity'
    if '=X' in s or '/' in s:
        return 'forex'
    if '=F' in s:
        return 'commodity'
    if s.startswith('^'):
        return 'index'
    return 'unknown'


# CoinGecko ID mapping (only for actual crypto)
COINGECKO_IDS = {
    'BTC-USD': 'bitcoin',  'BTC-USDT': 'bitcoin',  'BTC': 'bitcoin',
    'ETH-USD': 'ethereum', 'ETH-USDT': 'ethereum', 'ETH': 'ethereum',
    'SOL-USD': 'solana',   'XRP-USD': 'ripple',     'DOGE-USD': 'dogecoin',
    'ADA-USD': 'cardano',  'MATIC-USD': 'matic-network', 'AVAX-USD': 'avalanche-2',
    'BNB-USD': 'binancecoin', 'LINK-USD': 'chainlink',
}

# Binance symbol mapping
def to_binance_symbol(symbol: str) -> Optional[str]:
    """Convert BTC-USD or BTC/USDT to BTCUSDT"""
    s = symbol.upper().replace('-', '').replace('/', '')
    # Handle -USD → USDT mapping
    if s.endswith('USD') and not s.endswith('USDT'):
        s = s + 'T'
    return s if len(s) >= 6 else None


# ═══════════════════════════════════════════════════════════════
# THREAD-SAFE CACHE
# ═══════════════════════════════════════════════════════════════

class _TTLCache:
    """Thread-safe TTL cache for prices and OHLCV data."""
    def __init__(self):
        self._data: Dict[str, Tuple[any, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, ttl: float) -> Optional[any]:
        with self._lock:
            if key in self._data:
                val, ts = self._data[key]
                if time.time() - ts < ttl:
                    return val
            return None

    def set(self, key: str, value: any):
        with self._lock:
            self._data[key] = (value, time.time())

    def age(self, key: str) -> Optional[float]:
        with self._lock:
            if key in self._data:
                _, ts = self._data[key]
                return time.time() - ts
            return None


# ═══════════════════════════════════════════════════════════════
# BINANCE LIVE FEED (Fixed Version)
# ═══════════════════════════════════════════════════════════════

class BinanceLiveFeed:
    """
    Fixed Binance WebSocket feed.
    
    Fixes from original realtime_feed.py:
    1. Thread-safe price dict using threading.Lock()
    2. Stores prices under both BTCUSDT AND BTC-USD keys
    3. Proper asyncio loop lifecycle management
    4. Exponential backoff on reconnect (not flat 5s)
    5. Handles Binance maintenance (1006 close code)
    """

    def __init__(self):
        self._prices: Dict[str, float]      = {}
        self._timestamps: Dict[str, float]  = {}
        self._lock = threading.Lock()
        self._subscribed: set               = set()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, symbols: list):
        """Start streaming for list of crypto symbols like ['BTC-USD', 'ETH-USD']"""
        binance_syms = []
        for s in symbols:
            bs = to_binance_symbol(s)
            if bs:
                binance_syms.append(bs)
                self._subscribed.add(bs)

        if not binance_syms:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            args=(binance_syms,),
            daemon=True,
            name='BinanceFeed',
        )
        self._thread.start()
        logger.info('binance_feed_started', symbols=binance_syms)

    def stop(self):
        self._stop_event.set()

    def _thread_main(self, symbols: list):
        """Thread entry — runs its own event loop cleanly."""
        import asyncio
        while not self._stop_event.is_set():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._ws_listener(symbols))
            except Exception as e:
                logger.error('binance_thread_crashed', error=str(e)[:80])
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            if not self._stop_event.is_set():
                time.sleep(5)  # Wait before restarting loop

    async def _ws_listener(self, symbols: list):
        try:
            import websockets
        except ImportError:
            logger.error('websockets_not_installed')
            return

        import asyncio
        streams    = '/'.join(f'{s.lower()}@aggTrade' for s in symbols)
        url        = f'wss://stream.binance.com:9443/ws/{streams}'
        backoff    = 1.0

        while not self._stop_event.is_set():
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    backoff = 1.0  # Reset on successful connect
                    logger.info('binance_ws_connected', streams=len(symbols))
                    async for raw in ws:
                        if self._stop_event.is_set():
                            return
                        import json
                        data   = json.loads(raw)
                        sym    = data.get('s', '')          # e.g. BTCUSDT
                        price  = float(data.get('p', 0.0))
                        if sym and price > 0:
                            # Store under BOTH key formats
                            canonical = sym[:-4] + '-USD' if sym.endswith('USDT') else sym
                            now = time.time()
                            with self._lock:
                                self._prices[sym]        = price   # BTCUSDT
                                self._prices[canonical]  = price   # BTC-USD
                                self._timestamps[sym]    = now
                                self._timestamps[canonical] = now
            except Exception as e:
                logger.warning('binance_ws_disconnected', error=str(e)[:60])
                await asyncio.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    def get_price(self, symbol: str, max_age_sec: float = 30.0) -> Optional[float]:
        """Get latest price. Returns None if older than max_age_sec."""
        s = symbol.upper()
        with self._lock:
            price = self._prices.get(s) or self._prices.get(to_binance_symbol(s) or '')
            if price is None:
                return None
            ts = self._timestamps.get(s) or self._timestamps.get(to_binance_symbol(s) or '', 0)
            if time.time() - ts > max_age_sec:
                return None
            return price

    def is_live(self, symbol: str) -> bool:
        return self.get_price(symbol, max_age_sec=10) is not None


# ═══════════════════════════════════════════════════════════════
# BINANCE HISTORICAL OHLCV (Fixed)
# ═══════════════════════════════════════════════════════════════

# Maps our interval format → Binance interval format
BINANCE_INTERVAL_MAP = {
    '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
    '1h': '1h', '2h': '2h', '4h': '4h', '6h': '6h', '12h': '12h',
    '1d': '1d', '3d': '3d', '1w': '1w',
    # yfinance format aliases
    '60m': '1h', '1H': '1h', '4H': '4h', '1D': '1d',
}


def fetch_binance_ohlcv(
    symbol: str,
    interval: str = '1h',
    bars: int = 200,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV from Binance REST API.
    Returns DataFrame with OHLCV columns.
    
    Fixed: Removed 1000 bar hard cap to allow larger initial backfills.
    Note: Binance API limit is 1000 per call; pagination may be needed 
    for >1000, but for now we prioritize local DB for large requests.
    """
    binance_sym = to_binance_symbol(symbol)
    if not binance_sym:
        return None

    b_interval = BINANCE_INTERVAL_MAP.get(interval, interval)

    try:
        url = (
            f'https://api.binance.com/api/v3/klines'
            f'?symbol={binance_sym}&interval={b_interval}&limit={bars}'
        )
        resp = requests.get(url, timeout=8)

        if resp.status_code == 429:
            logger.warning('binance_rate_limited', symbol=symbol)
            return None
        if resp.status_code == 400:
            # Invalid symbol — try without the T suffix (BTC vs BTCUSDT)
            logger.debug('binance_invalid_symbol', symbol=binance_sym)
            return None
        if resp.status_code != 200:
            return None

        data = resp.json()
        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            'OpenTime', 'Open', 'High', 'Low', 'Close', 'Volume',
            'CloseTime', 'QuoteVol', 'Trades', 'TakerBase', 'TakerQuote', 'Ignore',
        ])
        for col in ('Open', 'High', 'Low', 'Close', 'Volume', 'TakerBase'):
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df.index = pd.to_datetime(df['OpenTime'], unit='ms', utc=True)
        df.index.name = 'Datetime'
        # Keep TakerBase — used by Delta Flow Filter in signal_generators.py
        # TakerBase = volume bought aggressively (market buy orders)
        # TakerSell = Volume - TakerBase = volume sold aggressively
        return df[['Open', 'High', 'Low', 'Close', 'Volume', 'TakerBase']].dropna()

    except Exception as e:
        logger.error('binance_ohlcv_failed', symbol=symbol, error=str(e)[:80])
        return None


# ═══════════════════════════════════════════════════════════════
# YFINANCE WRAPPER (Fixed + Extended)
# ═══════════════════════════════════════════════════════════════

# yfinance interval → period mapping (max lookback per interval)
YFINANCE_MAX_PERIOD = {
    '1m':  '7d',   # yfinance max for 1m
    '2m':  '60d',
    '5m':  '60d',
    '15m': '60d',
    '30m': '60d',
    '1h':  '730d',  # 2 years
    '1d':  '10y',
}

_yf_cache = _TTLCache()

def fetch_yfinance_ohlcv(
    symbol: str,
    interval: str = '1h',
    bars: int = 200,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV from yfinance.
    
    Works for:
    - Indian equities: ITC.NS, RELIANCE.NS
    - Forex:           EURUSD=X, GBPUSD=X, USDJPY=X, GBPJPY=X
    - Crypto (delayed):BTC-USD, ETH-USD
    - Indices:         ^NSEI, ^NSEBANK, ^VIX
    - Commodities:     GC=F (Gold), CL=F (Crude), SI=F (Silver)
    
    Fixed: caches results to avoid hammering yfinance on every brain tick.
    """
    cache_key = f'yf_{symbol}_{interval}_{bars}'
    # Cache: 5 min for 1m/5m, 30 min for 1H+
    ttl = 300 if interval in ('1m', '2m', '5m') else 1800
    cached = _yf_cache.get(cache_key, ttl)
    if cached is not None:
        return cached

    try:
        import yfinance as yf
        period = YFINANCE_MAX_PERIOD.get(interval, '60d')
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=True)

        if df is None or df.empty:
            return None

        # Standardise column names
        df = df.rename(columns=str.title)
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()

        # Return last N bars
        if len(df) > bars:
            df = df.iloc[-bars:]

        _yf_cache.set(cache_key, df)
        return df

    except Exception as e:
        logger.error('yfinance_ohlcv_failed', symbol=symbol, error=str(e)[:80])
        return None


def fetch_yfinance_price(symbol: str) -> Optional[float]:
    """Single latest price from yfinance (uses fast_info)."""
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        price = tk.fast_info.get('last_price') or tk.fast_info.get('lastPrice')
        if price:
            return float(price)
        # Fallback: last candle from 1d period
        hist = tk.history(period='1d', interval='1m')
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
    except Exception as e:
        logger.debug('yfinance_price_failed', symbol=symbol, error=str(e)[:60])
    return None


# ═══════════════════════════════════════════════════════════════
# COINGECKO (Fixed — crypto only, proper thread safety)
# ═══════════════════════════════════════════════════════════════

class _CoinGeckoClient:
    """
    Fixed CoinGecko client.
    Fixes: thread-safe rate limiting, crypto-only routing, proper cache.
    """
    _CACHE_TTL = 60   # seconds (was 30 — now longer since we have Binance for real-time)
    _MAX_RPM   = 20   # conservative (free tier: 30 RPM)

    def __init__(self):
        self._lock     = threading.Lock()
        self._history  = []   # call timestamps
        self._cache    = _TTLCache()

    def _rate_ok(self) -> bool:
        with self._lock:
            now = time.time()
            self._history = [t for t in self._history if now - t < 60]
            if len(self._history) >= self._MAX_RPM:
                return False
            self._history.append(now)
            return True

    def get_price(self, symbol: str) -> Optional[float]:
        s = symbol.upper().replace('/', '-')
        coin_id = COINGECKO_IDS.get(s)
        if not coin_id:
            return None   # Not a crypto symbol — don't try CoinGecko

        cached = self._cache.get(coin_id, self._CACHE_TTL)
        if cached is not None:
            return cached

        if not self._rate_ok():
            logger.debug('coingecko_rate_limited')
            return None

        try:
            url = f'https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd'
            resp = requests.get(url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200:
                data = resp.json()
                if coin_id in data:
                    price = float(data[coin_id]['usd'])
                    self._cache.set(coin_id, price)
                    return price
            elif resp.status_code == 429:
                logger.warning('coingecko_429_rate_limited')
        except Exception as e:
            logger.debug('coingecko_failed', error=str(e)[:60])
        return None


_coingecko = _CoinGeckoClient()


# ═══════════════════════════════════════════════════════════════
# UNIFIED MARKET DATA — THE SINGLE INTERFACE
# ═══════════════════════════════════════════════════════════════

class UnifiedMarketData:
    """
    Single interface for ALL symbols, ALL sources.
    
    Source priority chains:
    
    Crypto live price:
      Binance WS (real-time) → CoinGecko (1-min delay) → yfinance (15-min delay)
    
    Crypto OHLCV history:
      Binance REST (excellent quality) → yfinance (acceptable)
    
    Indian equity live:
      AngelOne (real-time) → Breeze (real-time) → NSEPython → yfinance (delayed)
    
    Indian equity OHLCV:
      Breeze historical → yfinance
    
    Forex live:
      yfinance (15-min delayed) ← ONLY option currently
      [Future: OANDA v20 API — add when you get a free account]
    
    Forex OHLCV:
      yfinance (acceptable for 1H+ timeframes, NOT for scalping)
    
    Global macro / indices:
      yfinance (^VIX, ^GSPC, GC=F, CL=F)
    """

    def __init__(self):
        self.binance = BinanceLiveFeed()
        self._angel  = None   # lazy-loaded
        self._breeze = None   # lazy-loaded
        self._price_cache = _TTLCache()
        self._started = False

    def start_live_feeds(self, crypto_symbols: list = None):
        """
        Start background WebSocket feeds.
        Call once at startup, not on every request.
        """
        if self._started:
            return
        default_crypto = ['BTC-USD', 'ETH-USD', 'SOL-USD', 'XRP-USD']
        symbols = crypto_symbols or default_crypto
        self.binance.start(symbols)
        self._started = True
        logger.info('unified_market_data_started', crypto=symbols)

    def _get_angel(self):
        if self._angel is None:
            try:
                from angel_one_client import angel_client
                self._angel = angel_client
            except Exception:
                pass
        return self._angel

    def _get_breeze(self):
        if self._breeze is None:
            try:
                from breeze_client import breeze_client
                self._breeze = breeze_client
            except Exception:
                pass
        return self._breeze

    # ── LIVE PRICE ────────────────────────────────────────────

    def get_price(self, symbol: str) -> Optional[float]:
        """
        Get latest price for any symbol.
        Auto-selects best available source with fallback.
        Returns None only if ALL sources fail.
        """
        asset_class = classify_symbol(symbol)
        cache_key   = f'price_{symbol}'
        # Short TTL for live prices (5s for crypto, 15s for equity/forex)
        ttl = 5 if asset_class == 'crypto' else 15
        cached = self._price_cache.get(cache_key, ttl)
        if cached is not None:
            return cached

        price = None

        if asset_class == 'crypto':
            # 1. Binance WebSocket (best — real-time)
            price = self.binance.get_price(symbol)
            # 2. CoinGecko REST
            if price is None:
                price = _coingecko.get_price(symbol)
            # 3. yfinance (last resort, 15-min delayed)
            if price is None:
                price = fetch_yfinance_price(symbol)

        elif asset_class == 'indian_equity':
            # 1. AngelOne
            angel = self._get_angel()
            if angel and angel.is_connected:
                price = angel.get_market_quote(symbol)
            # 2. Breeze
            if price is None:
                breeze = self._get_breeze()
                if breeze:
                    price = breeze.get_price(symbol)
            # 3. yfinance
            if price is None:
                price = fetch_yfinance_price(symbol)

        elif asset_class in ('forex', 'commodity', 'index'):
            # yfinance is the primary source for these
            price = fetch_yfinance_price(symbol)

        else:
            # Unknown — try yfinance as catch-all
            price = fetch_yfinance_price(symbol)

        if price and price > 0:
            self._price_cache.set(cache_key, price)

        return price if (price and price > 0) else None

    # ── HISTORICAL OHLCV ──────────────────────────────────────

    def get_ohlcv(
        self,
        symbol: str,
        interval: str = '1h',
        bars: int = 200,
    ) -> Optional[pd.DataFrame]:
        """
        Get OHLCV DataFrame for any symbol.
        Returns DataFrame with columns: Open, High, Low, Close, Volume
        
        Priority: 
        1. Local Postgres DB (fastest, supports large datasets)
        2. Primary API (Binance for crypto, Breeze for Indian equity)
        3. Fallback API (yfinance)
        """
        # 1. Check Local Database first
        try:
            from market_agent.data.storage.postgres import PostgresStorage
            storage = PostgresStorage()
            db_data = storage.get_latest_data(symbol, interval, limit=bars)
            if db_data and len(db_data) >= bars * 0.9:  # Allow 10% missing for recent gaps
                # Convert list of {timestamp, data} to DataFrame
                rows = []
                for entry in db_data:
                    d = entry['data']
                    d['Datetime'] = entry['timestamp']
                    rows.append(d)
                df = pd.DataFrame(rows).set_index('Datetime').sort_index()
                return df[['Open', 'High', 'Low', 'Close', 'Volume']]
        except Exception as e:
            logger.debug('db_ohlcv_retrieval_failed', symbol=symbol, error=str(e))

        # 2. Fallback to API chains
        asset_class = classify_symbol(symbol)

        if asset_class == 'crypto':
            # 1. Binance (best quality for crypto)
            df = fetch_binance_ohlcv(symbol, interval, bars)
            # 2. yfinance fallback
            if df is None or df.empty:
                df = fetch_yfinance_ohlcv(symbol, interval, bars)
            return df

        elif asset_class == 'indian_equity':
            # 1. Breeze (best for Indian equities — 1m resolution)
            breeze = self._get_breeze()
            if breeze and interval in ('1m', '5m', '15m', '1h'):
                df = self._breeze_ohlcv(symbol, interval, bars)
                if df is not None and not df.empty:
                    return df
            # 2. yfinance fallback
            return fetch_yfinance_ohlcv(symbol, interval, bars)

        else:
            # Forex, commodities, indices — yfinance
            return fetch_yfinance_ohlcv(symbol, interval, bars)

    def _breeze_ohlcv(self, symbol: str, interval: str, bars: int) -> Optional[pd.DataFrame]:
        """Convert yfinance-style interval to Breeze format and fetch."""
        breeze = self._get_breeze()
        if not breeze:
            return None

        # Interval mapping: yfinance → Breeze
        interval_map = {
            '1m': '1minute', '5m': '5minute', '15m': '15minute',
            '30m': '30minute', '1h': '1hour', '1d': '1day',
        }
        breeze_interval = interval_map.get(interval)
        if not breeze_interval:
            return None

        # Calculate date range for requested bars
        mins_per_bar = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '1d': 1440}
        total_mins = bars * mins_per_bar.get(interval, 60)
        from_dt = datetime.now() - timedelta(minutes=total_mins * 1.3)  # 30% buffer

        # Breeze date format: 'YYYY-MM-DDT00:00:00.000Z'
        from_str = from_dt.strftime('%Y-%m-%dT00:00:00.000Z')
        to_str   = datetime.now().strftime('%Y-%m-%dT23:59:59.000Z')

        clean_sym = symbol.replace('.NS', '').replace('.BO', '').upper()
        try:
            records = breeze.get_historical_data(clean_sym, breeze_interval, from_str, to_str)
            if not records:
                return None

            df = pd.DataFrame(records)
            # Breeze returns: datetime, open, high, low, close, volume
            df = df.rename(columns={
                'datetime': 'Datetime', 'open': 'Open', 'high': 'High',
                'low': 'Low', 'close': 'Close', 'volume': 'Volume',
            })
            for col in ('Open', 'High', 'Low', 'Close', 'Volume'):
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df['Datetime'] = pd.to_datetime(df['Datetime'])
            df = df.set_index('Datetime').sort_index()
            return df[['Open', 'High', 'Low', 'Close', 'Volume']].dropna().iloc[-bars:]

        except Exception as e:
            logger.error('breeze_ohlcv_failed', symbol=symbol, error=str(e)[:80])
            return None

    # ── SOURCE HEALTH STATUS ──────────────────────────────────

    def health_status(self) -> dict:
        """Returns current health of all data sources."""
        angel = self._get_angel()
        breeze = self._get_breeze()
        return {
            'binance_ws':  'live' if self.binance.is_live('BTC-USD') else 'disconnected',
            'angel_one':   'connected' if (angel and angel.is_connected) else 'disconnected',
            'breeze':      'connected' if (breeze and breeze.is_connected) else 'disconnected',
            'coingecko':   'active',   # REST — always available if rate OK
            'yfinance':    'active',   # Always available (with delays)
            'forex_live':  'delayed (yfinance only) — add OANDA for real-time',
        }


# Module-level singleton
market_data = UnifiedMarketData()


# ═══════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    import time

    print('=' * 60)
    print('UnifiedMarketData — Integration Test')
    print('=' * 60)

    # Test symbol classification
    print('\n[1] Symbol Classification:')
    test_symbols = ['BTC-USD', 'ETH-USDT', 'ITC.NS', 'RELIANCE.NS',
                    'EURUSD=X', 'GBPJPY=X', 'GC=F', '^VIX', 'UNKNOWN']
    for s in test_symbols:
        print(f'  {s:15s} → {classify_symbol(s)}')

    # Test Binance symbol mapping
    print('\n[2] Binance Symbol Mapping:')
    for s in ['BTC-USD', 'ETH-USD', 'SOL-USD', 'BTC/USDT']:
        print(f'  {s:15s} → {to_binance_symbol(s)}')

    # Test OHLCV fetch (Binance)
    print('\n[3] Binance OHLCV Fetch (BTC-USD 1H):')
    df = fetch_binance_ohlcv('BTC-USD', '1h', 50)
    if df is not None:
        print(f'  ✅ Got {len(df)} candles | Latest close: {df["Close"].iloc[-1]:.2f}')
        print(f'  Columns: {list(df.columns)}')
    else:
        print('  ❌ Failed (check internet connection)')

    # Test yfinance OHLCV for forex
    print('\n[4] yfinance Forex OHLCV (EURUSD=X 1H):')
    df2 = fetch_yfinance_ohlcv('EURUSD=X', '1h', 50)
    if df2 is not None:
        print(f'  ✅ Got {len(df2)} candles | Latest close: {df2["Close"].iloc[-1]:.5f}')
    else:
        print('  ❌ Failed')

    # Test yfinance for Indian equity
    print('\n[5] yfinance Indian Equity (ITC.NS 1H):')
    df3 = fetch_yfinance_ohlcv('ITC.NS', '1h', 50)
    if df3 is not None:
        print(f'  ✅ Got {len(df3)} candles | Latest close: {df3["Close"].iloc[-1]:.2f}')
    else:
        print('  ❌ Failed')

    # Test unified get_ohlcv
    print('\n[6] Unified get_ohlcv routing:')
    umd = UnifiedMarketData()
    for sym in ['BTC-USD', 'EURUSD=X']:
        df = umd.get_ohlcv(sym, '1h', 30)
        status = f'✅ {len(df)} bars, last={df["Close"].iloc[-1]:.4f}' if df is not None else '❌ failed'
        print(f'  {sym:15s}: {status}')

    # Test health status
    print('\n[7] Source Health:')
    for k, v in umd.health_status().items():
        print(f'  {k:15s}: {v}')

    print('\n✅ UnifiedMarketData test complete.')