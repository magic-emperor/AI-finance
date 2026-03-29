import structlog
from typing import Dict, Any
import pandas as pd
from market_agent.data.processing.microstructure_shadow import ShadowMicrostructureAnalyzer

logger = structlog.get_logger()

class OpportunityDetector:
    """
    Phase 11: The "Coiled Spring" Engine
    Identifies if a News Spike corresponds to a breakout or a "Squeeze".
    """
    
    def __init__(self):
        self.shadow_analyzer = ShadowMicrostructureAnalyzer()

    def evaluate_opportunity(self, ticker: str, df: pd.DataFrame, news_velocity: int) -> Dict[str, Any]:
        """
        The "Deep Thinking" logic:
        1. High News + Flat Price = COILED SPRING (Pre-Spike)
        2. High News + High Price = MOMENTUM CHASE (Reactive)
        """
        if df.empty:
            return {"status": "INSUFFICIENT_DATA"}

        shadow_metrics = self.shadow_analyzer.analyze_shadow_depth(df)
        
        # 1. Volatility Squeeze Check (ATR is low)
        atr_low = True if (df['high'].max() - df['low'].min()) / df['close'].mean() < 0.01 else False
        
        # 2. Price Displacement (Is it already a rocket?)
        price_move = (df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0]
        is_flat = True if abs(price_move) < 0.005 else False # <0.5% move is 'flat'

        status = "NEUTRAL"
        if news_velocity > 5: # Mock high velocity
            if is_flat and shadow_metrics.get("est_buying_pressure", 0) > 0.6:
                status = "COILED_SPRING_ALERT"
            elif not is_flat and price_move > 0.02:
                status = "ACTIVE_ROCKET_TRACKING"

        result = {
            "ticker": ticker,
            "status": status,
            "metrics": {
                "news_velocity": news_velocity,
                "price_move": price_move,
                "is_squeezed": atr_low,
                "shadow": shadow_metrics
            }
        }
        
        if status != "NEUTRAL":
            logger.info("opportunity_detected", **result)
            
        return result

if __name__ == "__main__":
    # Test Coiled Spring
    df = pd.DataFrame({
        "high": [100, 100.1],
        "low": [99.9, 99.8],
        "close": [100, 100],
        "volume": [100, 500]
    })
    detector = OpportunityDetector()
    print(detector.evaluate_opportunity("DUMMY", df, 10))
