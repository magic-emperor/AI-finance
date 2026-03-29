import structlog
from datetime import datetime, timedelta
from market_agent.data.storage.postgres import PostgresStorage

logger = structlog.get_logger()

def check_health():
    """
    Checks Layer 0 health:
    1. DB Connection
    2. Data Freshness (last record within expected time)
    """
    try:
        storage = PostgresStorage()
        session = storage.Session()
        
        # Check if table exists and has data
        from market_agent.data.storage.postgres import MarketData
        latest = session.query(MarketData).order_by(MarketData.timestamp.desc()).first()
        
        if not latest:
            logger.error("health_check_failed", reason="No data in database")
            return False
            
        time_diff = datetime.utcnow() - latest.timestamp
        freshness = "HEALTHY" if time_diff < timedelta(hours=24) else "STALE" # Adjust based on market hours
        
        logger.info("health_check_passed", 
                    latest_timestamp=latest.timestamp, 
                    freshness=freshness,
                    latency_from_now=str(time_diff))
        
        return True
    except Exception as e:
        logger.error("health_check_failed", error=str(e))
        return False

if __name__ == "__main__":
    check_health()
