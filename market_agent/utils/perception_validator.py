import pandas as pd
import structlog
from market_agent.data.storage.postgres import PostgresStorage
from market_agent.data.aggregation.aggregator import MultiTimeframeAggregator
from market_agent.patterns.perception import PerceptionEngine

logger = structlog.get_logger()

def verify_perception(symbol="RELIANCE.NS"):
    storage = PostgresStorage()
    aggregator = MultiTimeframeAggregator()
    engine = PerceptionEngine()
    
    logger.info("verifying_phase_2", symbol=symbol)
    
    # 1. Pull 1m data
    raw_data = storage.get_latest_data(symbol, "1m", limit=500)
    if not raw_data:
        logger.error("verification_failed_no_data")
        return
        
    df_1m = pd.DataFrame([{"timestamp": d["timestamp"], **d["data"]} for d in raw_data])
    df_1m.set_index("timestamp", inplace=True)
    df_1m.symbol = symbol  # metadata
    
    # 2. Aggregate to 15m
    df_15m = aggregator.aggregate(df_1m, "15min")
    df_15m.symbol = symbol
    
    # 3. Get Perception Snapshots
    snap_1m = engine.get_perception_snapshot(df_1m)
    snap_15m = engine.get_perception_snapshot(df_15m)
    
    print(f"\n--- PERCEPTION VERIFICATION: {symbol} ---")
    print(f"1m Latest - Close: {snap_1m['close']}, ATR: {snap_1m['atr']:.2f}, Volatility: {snap_1m['volatility']:.5f}")
    print(f"15m Latest - Close: {snap_15m['close']}, ATR: {snap_15m['atr']:.2f}, Volatility: {snap_15m['volatility']:.5f}")
    
    if snap_15m['atr'] is not None and snap_15m['volatility'] is not None:
        logger.info("phase_2_validation_passed")
    else:
        logger.warning("phase_2_validation_partial")

if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE.NS"
    verify_perception(symbol)
