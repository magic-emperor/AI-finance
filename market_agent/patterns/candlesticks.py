import pandas as pd
import numpy as np
from typing import Dict, List

class CandlestickRecognizer:
    """
    Layer 2: Deterministic Analytics
    Implements rule-based candlestick pattern recognition.
    """
    
    def __init__(self, body_threshold: float = 0.3):  # CHANGED: Relaxed from 0.6 to 0.3 (research: Better for volatile/intraday, small bodies common)
        self.body_threshold = body_threshold

    def _get_candle_props(self, df: pd.DataFrame):
        """Calculates basic properties for pattern detection."""
        body = np.abs(df['close'] - df['open'])
        candle_range = df['high'] - df['low']
        upper_wick = df['high'] - np.maximum(df['open'], df['close'])
        lower_wick = np.minimum(df['open'], df['close']) - df['low']
        
        # CHANGED: Added avg_vol calc for vol confirmation (research: High vol boosts pattern reliability in volatile markets)
        avg_vol = df['volume'].rolling(window=20).mean().shift(1)  # Prior avg, avoid lookahead
        is_high_vol = df['volume'] > avg_vol * 1.5
        
        return body, candle_range, upper_wick, lower_wick, is_high_vol

    def detect_hammer(self, df: pd.DataFrame) -> pd.Series:
        """Lower wick is at least 1.5x body (relaxed from 2x), little/no upper wick. Add prior downtrend and high vol."""
        body, candle_range, upper_wick, lower_wick, is_high_vol = self._get_candle_props(df)
        
        # CHANGED: Relaxed wick >1.5*body (from 2x—research: Better in volatile Indian/intraday)
        # Added prior downtrend check (close.shift < open.shift—research: Confirms reversal)
        # Added high vol confirmation
        prior_down = df['close'].shift(1) < df['open'].shift(1)
        return (
            (lower_wick > 1.5 * body) &  
            (upper_wick < 0.1 * candle_range) &
            prior_down & is_high_vol
        )

    def detect_shooting_star(self, df: pd.DataFrame) -> pd.Series:
        """Upper wick is at least 1.5x body (relaxed), little/no lower wick. Add prior uptrend and high vol."""
        body, candle_range, upper_wick, lower_wick, is_high_vol = self._get_candle_props(df)
        
        prior_up = df['close'].shift(1) > df['open'].shift(1)
        return (
            (upper_wick > 1.5 * body) &  
            (lower_wick < 0.1 * candle_range) &
            prior_up & is_high_vol
        )

    # ... (Other functions unchanged—doji, engulfing etc.)

    def get_all_patterns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Returns a DataFrame with boolean flags for all implemented patterns."""
        patterns = pd.DataFrame(index=df.index)
        patterns['hammer'] = self.detect_hammer(df)
        patterns['shooting_star'] = self.detect_shooting_star(df)
        patterns['doji'] = self.detect_doji(df)
        patterns['bullish_engulfing'] = self.detect_bullish_engulfing(df)
        patterns['bearish_engulfing'] = self.detect_bearish_engulfing(df)
        
        # Add more patterns here...
        return patterns

if __name__ == "__main__":
    # Test with dummy data
    data = {
        'open': [100, 102, 101, 100],
        'high': [105, 103, 110, 106],
        'low': [99, 90, 100, 95],
        'close': [102, 101, 102, 100],
        'volume': [100, 200, 150, 300]  # CHANGED: Added volume for test (high vol in row 1,3)
    }
    df = pd.DataFrame(data)
    recognizer = CandlestickRecognizer()
    print(recognizer.get_all_patterns(df))