import argparse
import pandas as pd
import structlog
from market_agent.data.storage.postgres import PostgresStorage
from market_agent.models.trainer import Trainer
from market_agent.models.preprocessing import DataPreprocessor
from market_agent.learning.evaluator import RegretEngine
from market_agent.learning.learning_engine import LearningEngine

logger = structlog.get_logger()

def main():
    parser = argparse.ArgumentParser(description="AI Agent Manual Study CLI")
    parser.add_argument("--symbol", type=str, required=True, help="Ticker symbol to retrain on")
    parser.add_argument("--timeframe", type=str, default="1m", help="Timeframe (1m, 5m, etc.)")
    parser.add_argument("--limit", type=int, default=500, help="Number of recent candles to use")
    parser.add_argument("--epochs", type=int, default=5, help="Number of training epochs")
    
    args = parser.parse_args()
    
    # 1. Setup Stack
    storage = PostgresStorage()
    preprocessor = DataPreprocessor()
    trainer = Trainer(input_size=7)
    regret = RegretEngine(storage)
    engine = LearningEngine(trainer, regret, preprocessor)
    
    # 2. Fetch Data
    logger.info("manual_study_started", symbol=args.symbol, limit=args.limit)
    raw_data = storage.get_latest_data(args.symbol, args.timeframe, limit=args.limit)
    
    if not raw_data:
        logger.error("no_data_found_for_symbol", symbol=args.symbol)
        return
        
    df = pd.DataFrame([{"timestamp": d["timestamp"], **d["data"]} for d in raw_data])
    df.set_index("timestamp", inplace=True)
    
    # 3. Trigger Learning
    engine.run_manual_retrain(args.symbol, df)
    
    print(f"\nSUCCESS: MANUAL STUDY COMPLETE for {args.symbol}")
    print(f"Model checkpoint saved to: market_agent/models/checkpoints/manual_{args.symbol}.pth")

if __name__ == "__main__":
    main()
