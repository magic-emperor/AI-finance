import pandas as pd
import numpy as np
import structlog
from typing import Dict, Any

logger = structlog.get_logger()

class PerceptionEngine:
    """
    Layer 1: Perception
    Calculates pure observation metrics without interpretation.
    """
    
    @staticmethod
    def calculate_atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
        """
        Average True Range (ATR)
        """
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        
        return true_range.rolling(window=window).mean()

    @staticmethod
    def calculate_volatility(df: pd.DataFrame, window: int = 20) -> pd.Series:
        """
        Rolling Volatility (Standard Deviation of log returns)
        """
        log_returns = np.log(df['close'] / df['close'].shift(1))
        return log_returns.rolling(window=window).std()

    @staticmethod
    def calculate_vwap(df: pd.DataFrame) -> pd.Series:
        """
        Volume Weighted Average Price (VWAP)
        """
        # Typically VWAP is calculated intraday (reset per session)
        # For simplicity in this perception layer, we can provide a rolling or cumulative version
        v = df['volume']
        p = (df['high'] + df['low'] + df['close']) / 3
        return (p * v).cumsum() / v.cumsum()

    def get_perception_snapshot(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Returns a dictionary of current market observations.
        """
        if len(df) < 20:
            return {"error": "Insufficient data"}
            
        latest_idx = df.index[-1]
        atr = self.calculate_atr(df)
        vol = self.calculate_volatility(df)
        vwap = self.calculate_vwap(df)
        
        # New: Volume Analysis
        avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
        curr_volume = df['volume'].iloc[-1]
        relative_volume = curr_volume / avg_volume if avg_volume > 0 else 1.0
        
        snapshot = {
            "timestamp": latest_idx,
            "close": float(df['close'].iloc[-1]),
            "atr": float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else None,
            "volatility": float(vol.iloc[-1]) if not pd.isna(vol.iloc[-1]) else None,
            "vwap": float(vwap.iloc[-1]) if not pd.isna(vwap.iloc[-1]) else None,
            "relative_price_v_vwap": float(df['close'].iloc[-1] / vwap.iloc[-1]) if not pd.isna(vwap.iloc[-1]) else 1.0,
            "relative_volume": float(relative_volume),
            "volume_class": "SPIKE" if relative_volume > 2.0 else "NORMAL"
        }
        
        logger.info("perception_snapshot_generated", symbol=getattr(df, 'symbol', 'unknown'), **snapshot)
        return snapshot

if __name__ == "__main__":
    # Test with dummy data
    dates = pd.date_range("2023-01-01", periods=100, freq="1min")
    df = pd.DataFrame({
        "open": np.random.randn(100) + 100,
        "high": np.random.randn(100) + 101,
        "low": np.random.randn(100) + 99,
        "close": np.random.randn(100) + 100,
        "volume": np.random.randint(100, 1000, 100)
    }, index=dates)
    
    engine = PerceptionEngine()
    print(engine.get_perception_snapshot(df))
