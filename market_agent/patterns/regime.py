import pandas as pd
import numpy as np
import structlog
from typing import Dict, Any

logger = structlog.get_logger()

class RegimeDetector:
    """
    Layer 2: Deterministic Analytics
    Classifies the market environment (Regime Detection).
    """

    def __init__(self, slow_period: int = 50, fast_period: int = 20, vol_window: int = 20):  # CHANGED: Shorter periods for intraday (was 200/50—too long, causes INSUFFICIENT_DATA)
        self.slow_period = slow_period
        self.fast_period = fast_period
        self.vol_window = vol_window

    def classify_regime(self, df: pd.DataFrame, market_type: str = 'stock') -> Dict[str, Any]:  # CHANGED: Added market_type param ('stock', 'forex', 'crypto') for market-specific handling
        """
        Determines if the market is BULL, BEAR, RANGE, or VOLATILE.
        """
        if len(df) < self.slow_period:
            return {"regime": "INSUFFICIENT_DATA"}

        # 1. Trend Indicators
        sma_fast = df['close'].rolling(window=self.fast_period).mean()
        sma_slow = df['close'].rolling(window=self.slow_period).mean()
        
        curr_price = df['close'].iloc[-1]
        curr_fast = sma_fast.iloc[-1]
        curr_slow = sma_slow.iloc[-1]

        # CHANGED: Added adaptive vol_threshold based on historical avg_vol * multiplier
        # Multiplier tuned per market_type (from research: crypto higher vol, forex lower)
        pct_change = df['close'].pct_change()
        avg_vol = pct_change.std()  # Historical avg volatility
        if market_type == 'crypto':
            vol_threshold = avg_vol * 2.0  # Higher for 24/7 extreme vol (research: 15-28%)
        elif market_type == 'forex':
            vol_threshold = avg_vol * 1.2  # Lower for stable pairs (research: 0.5-1%)
        else:  # 'stock' (Indian NSE-like)
            vol_threshold = avg_vol * 1.5  # Medium for session-based vol spikes
        
        vol = pct_change.rolling(window=self.vol_window).std().iloc[-1]
        is_volatile = vol > vol_threshold  # CHANGED: Adaptive instead of fixed 0.015 (too low, misfires to CHAOS)

        # 2. Trend Classification
        is_uptrend   = curr_fast > curr_slow and curr_price > curr_fast
        is_downtrend = curr_fast < curr_slow and curr_price < curr_fast

        # 3. Regime Classification
        regime = "RANGE" # Default
        
        if is_volatile:
            regime = "VOLATILE_CHAOS"
        elif is_uptrend:
            regime = "BULL_TREND"
        elif is_downtrend:
            regime = "BEAR_TREND"
            
        # CHANGED: Added hybrid regimes (VOLATILE_BULL/BEAR) if volatile but trend present (allows brains to vote in vol)
        if is_volatile and is_uptrend:
            regime = "VOLATILE_BULL"
        elif is_volatile and is_downtrend:
            regime = "VOLATILE_BEAR"

        result = {
            "regime": regime,
            "trend_strength": "STRONG" if abs(curr_fast - curr_slow) / curr_slow > 0.02 else "WEAK",
            "volatility_state": "HIGH" if is_volatile else "NORMAL",
            "price_v_slow_sma": float(curr_price / curr_slow)
        }
        
        logger.info("regime_detected", symbol=getattr(df, 'symbol', 'unknown'), **result)
        return result

if __name__ == "__main__":
    # Test with simulated data
    dates = pd.date_range("2023-01-01", periods=300, freq="1H")
    # Simulate a bull trend
    prices = np.linspace(100, 150, 300) + np.random.randn(300)
    df = pd.DataFrame({"close": prices}, index=dates)
    
    detector = RegimeDetector()
    print(detector.classify_regime(df, market_type='stock'))  # CHANGED: Added market_type in test