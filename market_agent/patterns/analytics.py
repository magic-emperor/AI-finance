import pandas as pd
import structlog
from typing import Dict, Any
from market_agent.patterns.candlesticks import CandlestickRecognizer
from market_agent.patterns.regime import RegimeDetector
from market_agent.data.aggregation.aggregator import MultiTimeframeAggregator

logger = structlog.get_logger()

class AnalyticsEngine:
    """
    Layer 2: Deterministic Analytics (Unified)
    Coordinates pattern recognition and regime detection.
    """
    
    def __init__(self):
        self.recognizer = CandlestickRecognizer()
        self.detector = RegimeDetector()
        self.aggregator = MultiTimeframeAggregator()

    def analyze(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Runs full deterministic analysis on the provided data.
        """
        symbol = getattr(df, 'symbol', 'unknown')
        logger.info("analytics_execution_started", symbol=symbol)
        
        # 1. Detect Regime (Is the market Down, Up, or Sideways?)
        regime_info = self.detector.classify_regime(df)
        
        if regime_info.get("regime") == "INSUFFICIENT_DATA":
             return {
                "symbol": symbol,
                "timestamp": df.index[-1],
                "regime": "WAITING_FOR_DATA",
                "trend_strength": "UNKNOWN",
                "active_patterns": [],
                "mtf_alignment": "UNKNOWN",
                "summary": "Waiting for more candles for trend analysis."
            }

        # 2. MTF Aligment Check (Adding 15m and 1H validation)
        df_15m = self.aggregator.aggregate(df, '15min')
        df_1h = self.aggregator.aggregate(df, '1H')
        
        regime_15m = self.detector.classify_regime(df_15m)["regime"] if len(df_15m) >= 20 else "INSUFFICIENT"
        regime_1h = self.detector.classify_regime(df_1h)["regime"] if len(df_1h) >= 20 else "INSUFFICIENT"
        
        # Alignment logic: Does 1m trend match 15m or 1H?
        mtf_aligned = "NEUTRAL"
        if regime_info["regime"] == regime_15m == regime_1h:
            mtf_aligned = "STRONG_ALIGNMENT"
        elif regime_info["regime"] == regime_15m or regime_info["regime"] == regime_1h:
            mtf_aligned = "PARTIAL_ALIGNMENT"
        else:
            mtf_aligned = "DIVERGENCE"

        # 3. Extract Patterns
        pattern_flags = self.recognizer.get_all_patterns(df)
        
        # 4. Find active patterns on the latest candle
        latest_patterns = pattern_flags.iloc[-1]
        active_list = latest_patterns[latest_patterns == True].index.tolist()
        
        analysis = {
            "symbol": symbol,
            "timestamp": df.index[-1],
            "regime": regime_info["regime"],
            "trend_strength": regime_info["trend_strength"],
            "active_patterns": active_list,
            "mtf_alignment": mtf_aligned,
            "higher_tf_context": {"15m": regime_15m, "1h": regime_1h},
            "summary": f"Market: {regime_info['regime']} (MTF: {mtf_aligned}). Signals: {len(active_list)}."
        }
        
        logger.info("analytics_complete", **analysis)
        return analysis

if __name__ == "__main__":
    # Test integration
    from market_agent.data.storage.postgres import PostgresStorage
    storage = PostgresStorage()
    raw_data = storage.get_latest_data("RELIANCE.NS", "1m", limit=300)
    
    if raw_data:
        df = pd.DataFrame([{"timestamp": d["timestamp"], **d["data"]} for d in raw_data])
        df.set_index("timestamp", inplace=True)
        df.symbol = "RELIANCE.NS"
        
        engine = AnalyticsEngine()
        print(engine.analyze(df))
