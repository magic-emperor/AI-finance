import pandas as pd
import structlog
from market_agent.data.storage.postgres import PostgresStorage

logger = structlog.get_logger()

class SMAValidator:
    def __init__(self, storage: PostgresStorage):
        self.storage = storage

    def validate_sma(self, symbol="ITC.NS", timeframe="1m", window=20):
        """
        Calculates SMA(20) from DB data and logs results for verification.
        """
        logger.info("performance_validation_started", symbol=symbol, window=window)
        
        # Pull enough data for SMA calculation (e.g., 100 candles)
        data = self.storage.get_latest_data(symbol, timeframe, limit=100)
        
        if not data:
            logger.error("validation_failed_no_data")
            return
        
        # Convert to DataFrame
        df = pd.DataFrame([{"timestamp": d["timestamp"], **d["data"]} for d in data])
        df.set_index("timestamp", inplace=True)
        
        # Calculate SMA
        df[f'SMA_{window}'] = df['close'].rolling(window=window).mean()
        
        latest_close = df['close'].iloc[-1]
        latest_sma = df[f'SMA_{window}'].iloc[-1]
        
        logger.info("sma_calculated", 
                    symbol=symbol, 
                    latest_timestamp=df.index[-1],
                    close=round(latest_close, 2),
                    sma20=round(latest_sma, 2) if not pd.isna(latest_sma) else "N/A")
        
        return latest_sma

if __name__ == "__main__":
    storage = PostgresStorage()
    validator = SMAValidator(storage)
    validator.validate_sma()
