import datetime
import time
import structlog
from typing import List

logger = structlog.get_logger()

class MarketScheduler:
    """
    Manages operational modes based on market hours and sessions.
    Default: IST (NSE) market hours.
    """
    def __init__(self, start_time="09:15", end_time="15:30", timezone="Asia/Kolkata"):
        self.start_time = datetime.datetime.strptime(start_time, "%H:%M").time()
        self.end_time = datetime.datetime.strptime(end_time, "%H:%M").time()
        
        # Hardcoded for simplicity, but can be improved with pytz
        logger.info("scheduler_initialized", market_hours=f"{start_time}-{end_time}")

    def is_market_open(self):
        """Checks if the current time is within trading hours and not a weekend."""
        now = datetime.datetime.now()
        
        # Weekend check
        if now.weekday() >= 5: # Saturday=5, Sunday=6
            return False
            
        curr_time = now.time()
        return self.start_time <= curr_time <= self.end_time

    def get_operational_mode(self):
        """Determines if the agent should be in LIVE or STUDY mode."""
        if self.is_market_open():
            return "ACTIVE_RESEARCH"
        else:
            return "STUDY_MODE"

    def wait_until_session_change(self):
        """Sleeps until the next significant mode shift."""
        mode = self.get_operational_mode()
        logger.info("waiting_for_next_session", current_mode=mode)
        
        while self.get_operational_mode() == mode:
            time.sleep(60) # Check every minute
            
        logger.info("session_changed", new_mode=self.get_operational_mode())

if __name__ == "__main__":
    scheduler = MarketScheduler()
    print(f"Current Mode: {scheduler.get_operational_mode()}")
    print(f"Is Market Open: {scheduler.is_market_open()}")
