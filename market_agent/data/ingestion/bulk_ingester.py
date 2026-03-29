import yfinance as yf
import pandas as pd
import structlog
from datetime import datetime, timedelta
from market_agent.data.storage.postgres import PostgresStorage
from typing import List

logger = structlog.get_logger()

class BulkFreeIngester:
    """
    Phase 11: Data Ingestion (Free-First Strategy)
    Fetches raw native historical data without synthetic upsampling.
    """
    
    def __init__(self, storage: PostgresStorage):
        self.storage = storage

    def fetch_native_history(self, symbol: str, interval: str, period: str):
        """
        Fetches and stores data for a specific native timeframe.
        Handles yfinance 1m limit by splitting into 7-day chunks if 1m.
        """
        logger.info("bulk_ingestion_started", symbol=symbol, interval=interval, period=period)
        
        try:
            ticker = yf.Ticker(symbol)
            
            if interval == "1m":
                # yfinance allows max 7 days of 1m data per request, up to 30 days total
                days_to_fetch = 30 # standard limit for 1m
                end_date = datetime.now()
                total_records = 0
                
                for i in range(0, days_to_fetch, 7):
                    start_chunk = end_date - timedelta(days=i+7)
                    end_chunk = end_date - timedelta(days=i)
                    
                    logger.debug("fetching_1m_chunk", start=start_chunk, end=end_chunk)
                    df = ticker.history(start=start_chunk, end=end_chunk, interval="1m")
                    
                    if not df.empty:
                        total_records += self._store_df(symbol, interval, df)
                
                logger.info("bulk_ingestion_complete", interval="1m", total=total_records)
            else:
                df = ticker.history(period=period, interval=interval)
                if not df.empty:
                    count = self._store_df(symbol, interval, df)
                    logger.info("bulk_ingestion_complete", symbol=symbol, interval=interval, total_records=count)
                        
        except Exception as e:
            logger.error("bulk_ingestion_failed", error=str(e), symbol=symbol)

    def _store_df(self, symbol: str, interval: str, df: pd.DataFrame) -> int:
        records_stored = 0
        for timestamp, row in df.iterrows():
            dt = timestamp.to_pydatetime()
            ohlc = {
                "open": float(row['Open']),
                "high": float(row['High']),
                "low": float(row['Low']),
                "close": float(row['Close']),
                "volume": int(row['Volume'])
            }
            self.storage.store_ohlc(symbol, dt, interval, ohlc)
            records_stored += 1
        return records_stored

    def run_institutional_bootstrap(self, symbols: List[str]):
        """
        Bootstraps a fresh environment with 2 years of 1H data 
        and 1 month of 1m data per symbol.
        """
        for sym in symbols:
            # Native 1H for 2 years (Regime Context)
            self.fetch_native_history(sym, "1h", "2y")
            
            # Native 1m for 30d (Pattern Context - 1m limit is usually 30-60d)
            self.fetch_native_history(sym, "1m", "1mo")

if __name__ == "__main__":
    import os
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Bulk Historical Ingester")
    parser.add_argument("--symbols", nargs="+", default=["ITC.NS"], help="List of symbols to bootstrap")
    args = parser.parse_args()
    
    storage = PostgresStorage()
    ingester = BulkFreeIngester(storage)
    
    logger.info("starting_cli_bootstrap", symbols=args.symbols)
    ingester.run_institutional_bootstrap(args.symbols)
