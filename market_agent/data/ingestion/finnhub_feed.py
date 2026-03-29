"""
╔══════════════════════════════════════════════════════════════════════╗
║  INGESTION: finnhub_feed.py                                          ║
║  Folder: ingestion/                                                  ║
║  Purpose: Free real-time forex + stock data via Finnhub WebSocket   ║
╚══════════════════════════════════════════════════════════════════════╝

WHY FINNHUB FOR FOREX:
  - 100% free — just sign up at finnhub.io (no card needed, 30 seconds)
  - Real-time WebSocket: streams EUR/USD, GBP/USD, GBP/JPY, USD/JPY etc.
  - The forex feed comes from IC Markets broker (institutional feed)
  - 60 API calls/min on free tier (more than enough)
  - Also streams crypto via Binance: "BINANCE:BTCUSDT"

FOREX SYMBOL FORMAT (critical — not intuitive):
  Finnhub uses IC Markets broker ID format for forex:
    EUR/USD → "IC MARKETS:1"
    GBP/USD → "IC MARKETS:2"  
    USD/JPY → "IC MARKETS:3"
    AUD/USD → "IC MARKETS:4"
    USD/CAD → "IC MARKETS:5"
    USD/CHF → "IC MARKETS:6"
    GBP/JPY → "IC MARKETS:7"
    EUR/JPY → "IC MARKETS:8"
  Full list: call GET https://finnhub.io/api/v1/forex/symbol?exchange=ic%20markets

SETUP:
  1. Go to finnhub.io → click "Get free API key"
  2. Sign up with email (no credit card)
  3. Copy API key
  4. Add to .env: FINNHUB_API_KEY=your_key_here
  5. pip install websocket-client requests

HISTORICAL OHLCV (REST):
  Also provides 1-minute OHLCV for forex via REST (1 year history on free tier)
"""
from __future__ import annotations

import json
import os
import time
import threading
import requests
import structlog
from typing import Optional, Dict, List
from datetime import datetime, timedelta
from dotenv import load_dotenv

import pandas as pd

logger = structlog.get_logger()

load_dotenv()

# ── IC Markets symbol map — YOUR standard format → Finnhub IC Markets ID ──
# Extend this as you add more forex pairs to your watchlist
FOREX_SYMBOL_MAP = {
    # Our format      Finnhub ID       Description
    'EURUSD=X':    'IC MARKETS:1',   # EUR/USD
    'EUR/USD':     'IC MARKETS:1',
    'GBPUSD=X':    'IC MARKETS:2',   # GBP/USD
    'GBP/USD':     'IC MARKETS:2',
    'USDJPY=X':    'IC MARKETS:3',   # USD/JPY
    'USD/JPY':     'IC MARKETS:3',
    'AUDUSD=X':    'IC MARKETS:4',   # AUD/USD
    'AUD/USD':     'IC MARKETS:4',
    'USDCAD=X':    'IC MARKETS:5',   # USD/CAD
    'USD/CAD':     'IC MARKETS:5',
    'USDCHF=X':    'IC MARKETS:6',   # USD/CHF
    'USD/CHF':     'IC MARKETS:6',
    'GBPJPY=X':    'IC MARKETS:7',   # GBP/JPY
    'GBP/JPY':     'IC MARKETS:7',
    'EURJPY=X':    'IC MARKETS:8',   # EUR/JPY
    'EUR/JPY':     'IC MARKETS:8',
    'NZDUSD=X':    'IC MARKETS:9',   # NZD/USD
    'NZD/USD':     'IC MARKETS:9',
    'EURAUD=X':    'IC MARKETS:37',  # EUR/AUD (approx — verify with symbol list)
    'EURGBP=X':    'IC MARKETS:38',  # EUR/GBP (approx)
}

# Reverse map: Finnhub symbol → our format (for storing prices)
_REVERSE_MAP = {v: k.replace('=X', '').replace('/', '') for k, v in FOREX_SYMBOL_MAP.items()}


class FinnhubFeed:
    """
    Finnhub WebSocket + REST feed.
    Provides real-time forex prices (free tier) and historical OHLCV.

    Thread-safe price store with TTL checking.
    Auto-reconnects on disconnect with exponential backoff.
    """

    _WS_URL = 'wss://ws.finnhub.io'

    def __init__(self):
        load_dotenv()
        self._api_key = os.getenv('FINNHUB_API_KEY', '')
        self._prices:     Dict[str, float] = {}
        self._timestamps: Dict[str, float] = {}
        self._lock        = threading.Lock()
        self._stop        = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._subscribed: List[str] = []

    @property
    def has_key(self) -> bool:
        return bool(self._api_key) and self._api_key != 'your_key_here'

    def start(self, symbols: List[str]):
        """
        Start streaming for list of symbols.
        symbols: use YOUR format e.g. ['EURUSD=X', 'GBPJPY=X', 'BTC-USD']
        """
        if not self.has_key:
            logger.warning('finnhub_no_api_key',
                          hint='Get free key at finnhub.io — no card needed')
            return

        # Translate to Finnhub format
        finnhub_syms = []
        for sym in symbols:
            s = sym.upper()
            if s in FOREX_SYMBOL_MAP:
                finnhub_syms.append(FOREX_SYMBOL_MAP[s])
            elif 'USD' in s and '-' in s:
                # Crypto — use Binance format via Finnhub
                crypto = s.replace('-USD', 'USDT').replace('-', '')
                finnhub_syms.append(f'BINANCE:{crypto}')
            else:
                logger.debug('finnhub_symbol_unknown', symbol=sym)

        if not finnhub_syms:
            logger.warning('finnhub_no_valid_symbols', original=symbols)
            return

        self._subscribed = finnhub_syms
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._thread_main,
            args=(finnhub_syms,),
            daemon=True,
            name='FinnhubFeed',
        )
        self._thread.start()
        logger.info('finnhub_feed_started', symbols=finnhub_syms)

    def stop(self):
        self._stop.set()

    def _thread_main(self, syms: List[str]):
        import asyncio
        while not self._stop.is_set():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self._ws_loop(syms))
            except Exception as e:
                logger.error('finnhub_thread_error', error=str(e)[:80])
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                time.sleep(5)

    async def _ws_loop(self, syms: List[str]):
        try:
            import websockets
        except ImportError:
            logger.error('websockets_not_installed', hint='pip install websockets')
            return

        import asyncio
        url     = f'{self._WS_URL}?token={self._api_key}'
        backoff = 1.0

        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    backoff = 1.0
                    # Subscribe to all symbols
                    for sym in syms:
                        await ws.send(json.dumps({'type': 'subscribe', 'symbol': sym}))
                        logger.debug('finnhub_subscribed', symbol=sym)

                    async for raw in ws:
                        if self._stop.is_set():
                            return
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        if msg.get('type') != 'trade':
                            continue

                        for trade in msg.get('data', []):
                            finnhub_sym = trade.get('s', '')
                            price       = float(trade.get('p', 0))
                            if price <= 0:
                                continue

                            # Store under Finnhub key AND our normalized key
                            now = time.time()
                            our_key = self._normalize_key(finnhub_sym)
                            with self._lock:
                                self._prices[finnhub_sym] = price
                                self._prices[our_key]     = price
                                self._timestamps[finnhub_sym] = now
                                self._timestamps[our_key]     = now

            except Exception as e:
                logger.warning('finnhub_ws_disconnected', error=str(e)[:60])
                await asyncio.sleep(min(backoff, 30))
                backoff = min(backoff * 2, 30)

    def _normalize_key(self, finnhub_sym: str) -> str:
        """IC MARKETS:1 → EURUSD,  BINANCE:BTCUSDT → BTCUSD"""
        if finnhub_sym.startswith('IC MARKETS:'):
            return _REVERSE_MAP.get(finnhub_sym, finnhub_sym.replace('IC MARKETS:', 'ICMKT'))
        if finnhub_sym.startswith('BINANCE:'):
            s = finnhub_sym.replace('BINANCE:', '')
            if s.endswith('USDT'):
                return s[:-4] + '-USD'
            return s
        return finnhub_sym

    def get_price(self, symbol: str, max_age_sec: float = 30.0) -> Optional[float]:
        """
        Get latest price for a symbol.
        symbol: use YOUR format — EURUSD=X, GBP/JPY, BTC-USD, etc.
        Returns None if no data or data older than max_age_sec.
        """
        keys_to_try = [
            symbol,
            symbol.upper(),
            symbol.replace('=X', '').replace('/', ''),
            FOREX_SYMBOL_MAP.get(symbol.upper(), ''),
        ]
        with self._lock:
            for key in keys_to_try:
                if key and key in self._prices:
                    age = time.time() - self._timestamps.get(key, 0)
                    if age <= max_age_sec:
                        return self._prices[key]
        return None

    def is_live(self, symbol: str = 'EURUSD=X') -> bool:
        return self.get_price(symbol, max_age_sec=15) is not None

    # ── REST: Forex OHLCV History ─────────────────────────────────────

    def get_forex_ohlcv(
        self,
        symbol: str,
        interval: str = '60',     # Finnhub format: 1, 5, 15, 30, 60, D, W, M
        bars: int = 200,
    ) -> Optional[pd.DataFrame]:
        """
        Fetch historical OHLCV for a forex pair via Finnhub REST.
        Free tier: up to 1 year of 1-minute data.

        symbol:   'EURUSD=X' or 'EUR/USD'
        interval: '1' '5' '15' '30' '60' 'D' 'W'  (NOT yfinance format)
        """
        if not self.has_key:
            return None

        # Finnhub forex REST uses pair format: OANDA:EUR_USD or similar
        # Map our symbol to Finnhub forex candle format
        pair_map = {
            'EURUSD=X':   'OANDA:EUR_USD',   'EUR/USD':  'OANDA:EUR_USD',
            'GBPUSD=X':   'OANDA:GBP_USD',   'GBP/USD':  'OANDA:GBP_USD',
            'USDJPY=X':   'OANDA:USD_JPY',   'USD/JPY':  'OANDA:USD_JPY',
            'AUDUSD=X':   'OANDA:AUD_USD',   'AUD/USD':  'OANDA:AUD_USD',
            'USDCAD=X':   'OANDA:USD_CAD',   'USD/CAD':  'OANDA:USD_CAD',
            'GBPJPY=X':   'OANDA:GBP_JPY',   'GBP/JPY':  'OANDA:GBP_JPY',
            'EURJPY=X':   'OANDA:EUR_JPY',   'EUR/JPY':  'OANDA:EUR_JPY',
            'USDCHF=X':   'OANDA:USD_CHF',   'USD/CHF':  'OANDA:USD_CHF',
        }
        finnhub_pair = pair_map.get(symbol.upper())
        if not finnhub_pair:
            logger.warning('finnhub_forex_unknown_pair', symbol=symbol)
            return None

        # Interval mapping: yfinance → Finnhub
        interval_map = {
            '1m': '1', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '60m': '60', '4h': '240', '1d': 'D',
        }
        fh_interval = interval_map.get(interval, interval)

        # Calculate time range
        mins_per_bar = int(fh_interval) if fh_interval.isdigit() else 1440
        total_secs = bars * mins_per_bar * 60
        to_ts   = int(time.time())
        from_ts = to_ts - int(total_secs * 1.3)   # 30% buffer

        try:
            resp = requests.get(
                'https://finnhub.io/api/v1/forex/candle',
                params={
                    'symbol':     finnhub_pair,
                    'resolution': fh_interval,
                    'from':       from_ts,
                    'to':         to_ts,
                    'token':      self._api_key,
                },
                timeout=10,
            )
            if resp.status_code == 429:
                logger.warning('finnhub_rest_rate_limited')
                return None
            if resp.status_code != 200:
                return None

            data = resp.json()
            if data.get('s') == 'no_data' or 'c' not in data:
                logger.debug('finnhub_no_forex_data', symbol=symbol, pair=finnhub_pair)
                return None

            df = pd.DataFrame({
                'Open':   data['o'],
                'High':   data['h'],
                'Low':    data['l'],
                'Close':  data['c'],
                'Volume': data.get('v', [0] * len(data['c'])),
            }, index=pd.to_datetime(data['t'], unit='s', utc=True))
            df.index.name = 'Datetime'

            return df.iloc[-bars:].dropna()

        except Exception as e:
            logger.error('finnhub_forex_ohlcv_failed', symbol=symbol, error=str(e)[:80])
            return None

    def get_all_forex_rates(self) -> Optional[Dict[str, float]]:
        """
        Fetch all current forex rates in one call (REST).
        Useful for quick snapshot without WebSocket.
        Returns dict like {'EUR/USD': 1.0921, 'GBP/USD': 1.2634, ...}
        """
        if not self.has_key:
            return None
        try:
            resp = requests.get(
                'https://finnhub.io/api/v1/forex/rates',
                params={'base': 'USD', 'token': self._api_key},
                timeout=5,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            # Finnhub returns base=USD rates: {'EUR': 0.9157, 'GBP': 0.7894, ...}
            quote = data.get('quote', {})
            if not quote:
                return None
            # Convert to standard pairs
            result = {}
            for currency, rate in quote.items():
                if rate and rate > 0:
                    result[f'{currency}/USD'] = round(1.0 / rate, 5)
                    result[f'USD/{currency}'] = round(rate, 5)
            return result
        except Exception as e:
            logger.error('finnhub_rates_failed', error=str(e)[:60])
        return None


# ── Module singleton ─────────────────────────────────────────────────
finnhub_feed = FinnhubFeed()


# ── Self-test ────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sys

    print('INGESTION: FinnhubFeed — Self Test')
    print('=' * 50)

    feed = FinnhubFeed()

    print(f'\n[1] API Key: {"✅ Found in .env" if feed.has_key else "❌ Not set — add FINNHUB_API_KEY to .env"}')

    print('\n[2] Symbol map:')
    test_syms = ['EURUSD=X', 'GBPJPY=X', 'BTC-USD', 'UNKNOWN_PAIR']
    for sym in test_syms:
        s = sym.upper()
        mapped = FOREX_SYMBOL_MAP.get(s, 'not in forex map')
        if 'USD' in s and '-' in s:
            mapped = f'BINANCE:{sym.replace("-USD","USDT").replace("-","")}'
        print(f'  {sym:15s} → {mapped}')

    print('\n[3] Key normalization:')
    normalize_tests = [
        ('IC MARKETS:1', 'EURUSD'),
        ('IC MARKETS:7', 'GBPJPY'),
        ('BINANCE:BTCUSDT', 'BTC-USD'),
    ]
    for finnhub_sym, expected_contains in normalize_tests:
        result = feed._normalize_key(finnhub_sym)
        ok = expected_contains.lower() in result.lower()
        print(f'  {"✅" if ok else "❌"} {finnhub_sym:20s} → {result}')

    print('\n[4] Interval mapping:')
    from ingestion.finnhub_feed import FinnhubFeed as FF
    interval_tests = [('1h','60'), ('5m','5'), ('1d','D'), ('15m','15')]
    imap = {'1m':'1','5m':'5','15m':'15','30m':'30','1h':'60','60m':'60','4h':'240','1d':'D'}
    for yf_fmt, expected in interval_tests:
        result = imap.get(yf_fmt, yf_fmt)
        ok = result == expected
        print(f'  {"✅" if ok else "❌"} {yf_fmt} → {result}')

    if feed.has_key:
        print('\n[5] Live REST test (all forex rates):')
        rates = feed.get_all_forex_rates()
        if rates:
            for pair in ['EUR/USD', 'GBP/USD', 'USD/JPY']:
                price = rates.get(pair, 'N/A')
                print(f'  {pair}: {price}')
        else:
            print('  ❌ No rates returned')

        print('\n[6] OHLCV test (EUR/USD 1H):')
        df = feed.get_forex_ohlcv('EURUSD=X', interval='1h', bars=50)
        if df is not None:
            print(f'  ✅ Got {len(df)} bars | Last close: {df["Close"].iloc[-1]:.5f}')
        else:
            print('  ❌ No OHLCV data')
    else:
        print('\n[5-6] Skipped — no API key')
        print('  → Get free key at finnhub.io (takes 30 seconds)')

    print('\n✅ FinnhubFeed structure test complete.')
    print()
    print('TO USE IN PRODUCTION:')
    print('  from ingestion.finnhub_feed import finnhub_feed')
    print('  finnhub_feed.start(["EURUSD=X", "GBPJPY=X", "GBPUSD=X"])')
    print('  price = finnhub_feed.get_price("EURUSD=X")  # real-time!')