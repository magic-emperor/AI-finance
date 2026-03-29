import pandas as pd
import structlog
from market_agent.data.storage.postgres import PostgresStorage
from market_agent.models.trainer import Trainer
from market_agent.models.preprocessing import DataPreprocessor
from market_agent.learning.evaluator import RegretEngine
from market_agent.learning.learning_engine import LearningEngine

logger = structlog.get_logger()

class ReflectionRunner:
    """
    Phase 11: Institutional Reflection Loop
    Forces the agent to 'Study' its most recent history (Layer 6).
    """
    
    def __init__(self):
        self.storage = PostgresStorage()
        self.trainer = Trainer(input_size=7)
        self.prep = DataPreprocessor()
        self.regret = RegretEngine(self.storage)
        self.learning = LearningEngine(self.trainer, self.regret, self.prep)

    def execute_reflection(self, symbol: str, interval: str = "1m", limit: int = 10000):
        """
        Fetches historical data from DB and runs a study session.
        """
        logger.info("reflection_session_started", symbol=symbol, limit=limit)
        
        # 1. Fetch from Layer 0 (Postgres)
        raw_data = self.storage.get_latest_data(symbol, interval, limit=limit)
        if not raw_data:
            logger.warning("reflection_failed_no_data", symbol=symbol)
            return

        # 2. Convert to DataFrame
        df = pd.DataFrame([{"t": d["timestamp"], **d["data"]} for d in raw_data])
        df.set_index("t", inplace=True)
        
        # 3. Trigger Learning Engine
        # We use manual_retrain to simulate a full-history study
        self.learning.run_manual_retrain(symbol, df)
        
        logger.info("reflection_session_complete", symbol=symbol)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Reflection Runner")
    parser.add_argument("--symbol", default="ITC.NS", help="Symbol to study")
    args = parser.parse_args()
    
    runner = ReflectionRunner()
    runner.execute_reflection(args.symbol)
