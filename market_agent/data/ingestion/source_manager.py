import structlog
from typing import Dict, Any, Optional
import time

logger = structlog.get_logger()

class DataSourceManager:
    """
    Layer 0: Resilient Data Ingestion
    Manages API health, budget tracking, and graceful degradation.
    """
    
    def __init__(self, daily_budget_inr: float = 500.0):
        self.daily_budget = daily_budget_inr
        self.current_spend = 0.0
        self.api_status = {"truedata": "disengaged", "yfinance": "active"}
        self.usage_history = []

    def check_budget_guard(self) -> bool:
        """
        Prevents overspending on paid APIs.
        Shut off at 80% to provide safety margin.
        """
        if self.current_spend >= (self.daily_budget * 0.8):
            logger.warning("budget_limit_approaching", spend=self.current_spend, limit=self.daily_budget)
            return False
        return True

    def get_data_source(self, preferred: str = "truedata") -> str:
        """
        Decision engine for data source selection.
        Implements graceful degradation to free sources.
        """
        if preferred == "truedata":
            if self.check_budget_guard() and self.api_status.get("truedata") == "active":
                return "truedata"
            else:
                logger.info("falling_back_to_free_source", reason="Budget limit or API inactive")
                return "yfinance"
        return "yfinance"

    def track_usage(self, api_name: str, cost: float):
        """
        Tracks spending in real-time.
        """
        self.current_spend += cost
        self.usage_history.append({
            "timestamp": time.time(),
            "api": api_name,
            "cost": cost
        })
        logger.info("usage_tracked", api=api_name, cost=cost, total_spend=self.current_spend)

    def validate_connection(self, api_name: str) -> bool:
        """
        Actual health check (Placeholder for connection logic).
        """
        # In production, this would involve a ping or small data fetch
        is_healthy = True if api_name == "yfinance" else False 
        self.api_status[api_name] = "active" if is_healthy else "inactive"
        return is_healthy

if __name__ == "__main__":
    manager = DataSourceManager(daily_budget_inr=100.0)
    print(f"Preferred source: {manager.get_data_source('truedata')}")
    manager.track_usage("truedata", 81.0) # Break the 80% limit
    print(f"Source after budget breach: {manager.get_data_source('truedata')}")
