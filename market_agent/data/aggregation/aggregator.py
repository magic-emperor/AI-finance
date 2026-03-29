import pandas as pd
import structlog
from typing import List, Dict, Any

logger = structlog.get_logger()

class MultiTimeframeAggregator:
    """
    Layer 1: Perception (Multi-Timeframe)
    Aggregates lower timeframe data into higher ones.
    """
    
    @staticmethod
    def aggregate(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
        """
        Aggregates OHLCV data. 
        target_timeframe should be compatible with pandas resample (e.g., '5min', '15min', '1H').
        """
        if df.empty:
            return df
            
        # Ensure index is datetime
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
            
        resampler = df.resample(target_timeframe)
        
        agg_df = resampler.agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last',
            'volume': 'sum'
        }).dropna()
        
        logger.info("data_aggregated", 
                    original_records=len(df), 
                    target_records=len(agg_df), 
                    target_timeframe=target_timeframe)
                    
        return agg_df

    def process_symbol_multiple_tf(self, df_1m: pd.DataFrame, timeframes: List[str] = ["5min", "15min", "1H"]) -> Dict[str, pd.DataFrame]:
        """
        Processes a single data stream into multiple timeframes.
        """
        results = {"1min": df_1m}
        for tf in timeframes:
            results[tf] = self.aggregate(df_1m, tf)
        return results

if __name__ == "__main__":
    # Test aggregation
    dates = pd.date_range("2023-01-01", periods=60, freq="1min")
    df = pd.DataFrame({
        "open": range(60),
        "high": range(1, 61),
        "low": range(-1, 59),
        "close": range(60),
        "volume": [100] * 60
    }, index=dates)
    
    aggregator = MultiTimeframeAggregator()
    df_5m = aggregator.aggregate(df, '5min')
    print("5-Minute Aggregation Result Example:")
    print(df_5m.head())
