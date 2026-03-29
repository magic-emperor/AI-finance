import structlog
import torch
import numpy as np
from typing import List, Dict, Any
from market_agent.learning.evaluator import RegretEngine
from market_agent.models.trainer import Trainer
from market_agent.models.preprocessing import DataPreprocessor

logger = structlog.get_logger()

class LearningEngine:
    """
    Layer 5: Learning
    The system that updates model weights based on performance feedback.
    Handles both Dynamic (Auto) and Manual modes.
    """
    def __init__(self, trainer: Trainer, regret_engine: RegretEngine, preprocessor: DataPreprocessor):
        self.trainer = trainer
        self.regret_engine = regret_engine
        self.preprocessor = preprocessor

    def run_dynamic_study(self, limit=100):
        """
        AUTOMATIC: Called during Study Mode (off-market).
        Learns from high-regret samples identified by the Regret Engine.
        """
        logger.info("starting_dynamic_study_session")
        
        # 1. Get evaluations from Regret Engine
        evaluations = self.regret_engine.evaluate_performance(limit=limit)
        
        # 2. Filter for 'High Regret' samples (simplified placeholder)
        # In a real system, we'd fetch the actual features for these samples
        if not evaluations:
            logger.info("no_new_samples_for_learning")
            return
            
        logger.info("learning_from_past_mistakes", sample_count=len(evaluations))
        
        # 3. Simulate retraining on mistake patterns (Logic to be finalized in Phase 6.2)
        # For now, we simulate the call to the trainer
        # self.trainer.fine_tune(X_mistakes, Y_dir, Y_range)
        
        logger.info("dynamic_study_session_complete")

    def run_manual_retrain(self, symbol: str, data: Any):
        """
        MANUAL: Triggered by user CLI.
        Forces the model to adapt to a specific symbol or historical period.
        """
        logger.info("manual_retrain_triggered", symbol=symbol)
        
        # Preprocess the new data provided by user
        scaled_data = self.preprocessor.fit_transform(data)
        X, Y_dir, Y_range = self.preprocessor.create_sequences(scaled_data)
        
        # Run standard training loop
        self.trainer.train(X, Y_dir, Y_range, epochs=10)
        self.trainer.save_model(f"market_agent/models/checkpoints/manual_{symbol}.pth")
        
        logger.info("manual_retrain_complete", symbol=symbol)

if __name__ == "__main__":
    # Test initialization
    from market_agent.data.storage.postgres import PostgresStorage
    storage = PostgresStorage()
    trainer = Trainer(input_size=7)
    regret = RegretEngine(storage)
    prep = DataPreprocessor()
    
    engine = LearningEngine(trainer, regret, prep)
    print("Learning Engine Initialized.")
    engine.run_dynamic_study()
