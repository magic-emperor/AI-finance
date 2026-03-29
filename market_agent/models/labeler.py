"""
Phase 17.3: Self-Correction Labeler

This script runs hourly to closed the loop:
1. Fetch 'Pending' predictions from Postgres.
2. Fetch the actual price move from the market (or DB).
3. Update the 'Predictions' table with the result (WIN/LOSS).
4. This fuels the 'Self-Correction' training routine.
"""

import pandas as pd
import structlog
from datetime import datetime, timedelta
import torch

from market_agent.data.storage.postgres import PostgresStorage
from market_agent.dashboard.signal_engine import SignalDirection

logger = structlog.get_logger()

class SelfCorrectionLabeler:
    def __init__(self):
        self.storage = PostgresStorage()
        
    def label_past_predictions(self):
        """Find predictions that haven't been graded and check if they hit target/stop."""
        logger.info("labeling_routine_started")
        
        # 1. Get predictions from last 24 hours that are null in 'actual_direction'
        pending = self.storage.get_pending_evaluations(limit=100)
        
        if not pending:
            logger.info("no_pending_predictions_to_label")
            return
            
        for pred in pending:
            symbol = pred.symbol
            pred_time = pred.prediction_time
            
            # Fetch lookback period (e.g. 5 hours after prediction)
            market_data = self.storage.get_market_data_after(symbol, pred_time, limit=10)
            
            if not market_data:
                continue
                
            # Grade it
            # Simple logic: Did it go UP or DOWN in the next few bars?
            initial_price = market_data[0].close
            final_price = market_data[-1].close
            
            actual_dir = 2 if final_price > initial_price else (0 if final_price < initial_price else 1)
            is_correct = (actual_dir == pred.predicted_direction)
            
            # Update DB
            self.storage.update_prediction_result(
                prediction_id=pred.id,
                actual_direction=actual_dir,
                was_correct=is_correct
            )
            
            logger.info("prediction_labeled", 
                        symbol=symbol, 
                        correct=is_correct, 
                        pred=pred.predicted_direction, 
                        actual=actual_dir)

if __name__ == "__main__":
    labeler = SelfCorrectionLabeler()
    labeler.label_past_predictions()
