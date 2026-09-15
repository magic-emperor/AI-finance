import yfinance as yf
import structlog
import time
from datetime import datetime, timedelta
from market_agent.data.storage.postgres import PostgresStorage

logger = structlog.get_logger()

class MarketFeed:
    def __init__(self, storage: PostgresStorage):
        self.storage = storage

    def fetch_and_store(self, symbol="ITC.NS", period="7d", interval="1m"):
        """
        Fetches historical data and stores it in the database.
        """
        logger.info("fetching_market_data", symbol=symbol, period=period, interval=interval)
        
        try:
            ticker = yf.Ticker(symbol)
            data = ticker.history(period=period, interval=interval)
            
            if data.empty:
                logger.warning("no_data_received", symbol=symbol)
                return

            count = 0
            for timestamp, row in data.iterrows():
                # Convert timestamp from pandas to python datetime
                dt = timestamp.to_pydatetime()
                
                # Check if we already have this data (optional but recommended for replay)
                # For now, we just insert.
                
                ohlc = {
                    "Open": float(row['Open']),
                    "High": float(row['High']),
                    "Low": float(row['Low']),
                    "Close": float(row['Close']),
                    "Volume": int(row['Volume'])
                }

                self.storage.store_ohlc(symbol, dt, interval, ohlc, source='yfinance')
                count += 1
            
            logger.info("ingestion_complete", symbol=symbol, records=count)
            
        except Exception as e:
            logger.error("ingestion_failed", error=str(e), symbol=symbol)
            raise

    def run_realtime_loop(self, symbols=["ITC.NS"], interval="1m"):
        """
        A simple loop to keep data up to date for multiple symbols.
        """
        logger.info("starting_realtime_loop", symbols=symbols)
        while True:
            try:
                for symbol in symbols:
                    self.fetch_and_store(symbol=symbol, period="1d", interval=interval)
                # Sleep for 1 minute
                time.sleep(60)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("loop_error", error=str(e))
                time.sleep(10)

if __name__ == "__main__":
    import sys
    storage = PostgresStorage()
    feed = MarketFeed(storage)
    
    # Get symbols from command line or use defaults
    user_symbols = sys.argv[1:] if len(sys.argv) > 1 else ["ITC.NS", "RELIANCE.NS"]
    
    for sym in user_symbols:
        feed.fetch_and_store(symbol=sym, period="7d", interval="1m")
