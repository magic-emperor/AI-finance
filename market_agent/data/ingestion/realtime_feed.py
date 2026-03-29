
import json
import threading
import time
import structlog
from datetime import datetime
import websockets
import asyncio
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = structlog.get_logger()

class RealTimePriceFeed:
    """
    High-Velocity Price Feed Handler.
    Supports Binance WebSocket for Crypto and AngelOne for NIFTY.
    """
    def __init__(self):
        self.prices = {} # {symbol: price}
        self.last_update = {} # {symbol: timestamp}
        self._stop_event = threading.Event()
        self._threads = []

    def start_binance_stream(self, symbols=["BTCUSDT", "ETHUSDT"]):
        """Starts a background thread for Binance WebSocket."""
        thread = threading.Thread(target=self._run_binance_loop, args=(symbols,), daemon=True)
        thread.start()
        self._threads.append(thread)
        logger.info("binance_stream_started", symbols=symbols)

    def _run_binance_loop(self, symbols):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._binance_listener(symbols))

    async def _binance_listener(self, symbols):
        # Format: btcusdt@aggTrade (pushes every single executed trade in real-time)
        # Binance WS is case-sensitive for the stream name (must be lowercase)
        streams = "/".join([f"{s.lower()}@aggTrade" for s in symbols])
        url = f"wss://stream.binance.com:9443/ws/{streams}"
        
        logger.info("binance_ws_connecting", url=url)
        
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(url, ping_interval=None) as websocket:
                    logger.info("binance_ws_connected")
                    while not self._stop_event.is_set():
                        message = await websocket.recv()
                        data = json.loads(message)
                        
                        # Binance aggTrade format: s=Symbol, p=Price
                        symbol = data.get('s') # e.g. BTCUSDT
                        price = float(data.get('p', 0))
                        
                        if price > 0:
                            # Map back to standard naming: BTCUSDT -> BTC-USD
                            # This needs to match what get_live_quote looks for!
                            # get_live_quote uses "BTC-USD".replace("-USD", "USDT") -> "BTCUSDT"
                            # So we should store as "BTCUSDT" to match that logic, 
                            # OR change get_live_quote/feed to use a consistent key.
                            # Let's clean it to "BTCUSDT" (Upper) as the key for simplicity in lookup
                            # But wait, logic in command_center.py: 
                            # binance_sym = sym.replace("-USD", "USDT").replace("/", "")
                            # feed.prices[binance_sym]
                            
                            self.prices[symbol] = price
                            self.last_update[symbol] = datetime.now()
                            # logger.debug("binance_tick", symbol=symbol, price=price) # Too noisy
                        
            except Exception as e:
                logger.error("binance_ws_error", error=str(e))
                await asyncio.sleep(5) # Reconnect delay

    def get_price(self, symbol):
        """Returns the latest cached price, or None if older than 30s."""
        if symbol in self.prices:
            if (datetime.now() - self.last_update[symbol]).total_seconds() < 30:
                return self.prices[symbol]
        return None

    def get_price_with_age(self, symbol):
        """
        Always returns (price, age_seconds) for display purposes.
        price  — latest cached price, or None if symbol never received
        age_s  — seconds since last update, or None if no data yet
        The UI can show a stale badge when age_s > 30.
        """
        if symbol in self.prices:
            age_s = (datetime.now() - self.last_update[symbol]).total_seconds()
            return self.prices[symbol], age_s
        return None, None

    def fetch_binance_history(self, symbol: str, interval: str = "1m", limit: int = 500):
        """
        Utility for Scout: Fetches historical klines from Binance REST API.
        Returns a pandas DataFrame with [Open, High, Low, Close, Volume] indexed by Datetime.
        """
        import requests
        import pandas as pd
        
        # Mapping: BTC-USD -> BTCUSDT
        clean_sym = symbol.replace("-USD", "USDT").replace("/", "").upper()
        url = f"https://api.binance.com/api/v3/klines?symbol={clean_sym}&interval={interval}&limit={limit}"
        
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                # Binance kline: [Open time, Open, High, Low, Close, Volume, Close time, ...]
                df = pd.DataFrame(data, columns=[
                    'OpenTime', 'Open', 'High', 'Low', 'Close', 'Volume',
                    'CloseTime', 'QuoteAssetVol', 'NumTrades', 'TakerBuyBase', 'TakerBuyQuote', 'Ignore'
                ])
                
                # Convert to numeric
                for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
                    df[col] = pd.to_numeric(df[col])
                
                # Convert timestamp to Datetime index
                df['Datetime'] = pd.to_datetime(df['OpenTime'], unit='ms')
                df.set_index('Datetime', inplace=True)
                
                return df[['Open', 'High', 'Low', 'Close', 'Volume']]
            else:
                logger.warning("binance_history_error", status=resp.status_code, symbol=symbol)
        except Exception as e:
            logger.error("binance_history_failed", symbol=symbol, error=str(e))
            
        return None

# Singleton instance
price_feed = RealTimePriceFeed()
