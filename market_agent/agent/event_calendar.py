from datetime import datetime, date
from typing import Dict, List, Optional
import structlog

logger = structlog.get_logger()

class EventCalendarAlpha:
    """
    Phase 12 Extraordinary: Calendar-Based Alpha
    
    Hardcodes key market events that create predictable volatility:
    - RBI Policy announcements
    - Budget Day
    - Monthly F&O Expiry (last Thursday)
    - Quarterly results season
    
    Logic: 
    - Dampen position size by 50% on high-volatility days
    - Flag "Max Pain" levels on expiry
    """
    
    # Fixed events for 2026 (extend annually)
    RBI_POLICY_DATES = [
        date(2026, 2, 7),   # Feb policy
        date(2026, 4, 9),   # Apr policy
        date(2026, 6, 6),   # Jun policy
        date(2026, 8, 7),   # Aug policy
        date(2026, 10, 9),  # Oct policy
        date(2026, 12, 4),  # Dec policy
    ]
    
    BUDGET_DATES = [
        date(2026, 2, 1),   # Union Budget
    ]
    
    # Monthly expiry is last Thursday of each month
    # We calculate this dynamically
    
    QUARTERLY_RESULTS_WINDOWS = [
        # (start, end) - approximate windows
        (date(2026, 1, 10), date(2026, 2, 15)),  # Q3 results
        (date(2026, 4, 10), date(2026, 5, 15)),  # Q4 results
        (date(2026, 7, 10), date(2026, 8, 15)),  # Q1 results
        (date(2026, 10, 10), date(2026, 11, 15)), # Q2 results
    ]
    
    EVENT_RISK_MULTIPLIERS = {
        "RBI_POLICY": 0.5,      # 50% position size
        "BUDGET": 0.3,          # 30% position size
        "MONTHLY_EXPIRY": 0.7,  # 70% position size
        "QUARTERLY_RESULTS": 0.6, # 60% position size
        "NORMAL": 1.0           # Full position
    }
    
    def __init__(self):
        self.today = date.today()
    
    def _get_last_thursday(self, year: int, month: int) -> date:
        """Calculate the last Thursday of a given month."""
        import calendar
        
        # Get all days in the month
        cal = calendar.monthcalendar(year, month)
        
        # Thursday is index 3 (Monday=0)
        # Find the last week that has a Thursday
        for week in reversed(cal):
            if week[3] != 0:
                return date(year, month, week[3])
        
        return None
    
    def get_monthly_expiry_dates(self, year: int = 2026) -> List[date]:
        """Get all monthly expiry dates for a year."""
        expiries = []
        for month in range(1, 13):
            expiry = self._get_last_thursday(year, month)
            if expiry:
                expiries.append(expiry)
        return expiries
    
    def is_event_day(self, check_date: Optional[date] = None) -> Dict:
        """
        Check if a given date is a significant market event day.
        Returns event type and risk multiplier.
        """
        if check_date is None:
            check_date = date.today()
        
        events = []
        risk_multiplier = 1.0
        
        # Check Budget
        if check_date in self.BUDGET_DATES:
            events.append("BUDGET")
            risk_multiplier = min(risk_multiplier, self.EVENT_RISK_MULTIPLIERS["BUDGET"])
        
        # Check RBI Policy
        if check_date in self.RBI_POLICY_DATES:
            events.append("RBI_POLICY")
            risk_multiplier = min(risk_multiplier, self.EVENT_RISK_MULTIPLIERS["RBI_POLICY"])
        
        # Check Monthly Expiry
        expiries = self.get_monthly_expiry_dates(check_date.year)
        if check_date in expiries:
            events.append("MONTHLY_EXPIRY")
            risk_multiplier = min(risk_multiplier, self.EVENT_RISK_MULTIPLIERS["MONTHLY_EXPIRY"])
        
        # Check Quarterly Results Window
        for start, end in self.QUARTERLY_RESULTS_WINDOWS:
            if start <= check_date <= end:
                events.append("QUARTERLY_RESULTS")
                risk_multiplier = min(risk_multiplier, self.EVENT_RISK_MULTIPLIERS["QUARTERLY_RESULTS"])
                break
        
        if not events:
            events.append("NORMAL")
        
        return {
            "date": check_date.isoformat(),
            "events": events,
            "risk_multiplier": risk_multiplier,
            "position_size_pct": int(risk_multiplier * 100),
            "is_high_volatility": risk_multiplier < 1.0
        }
    
    def get_upcoming_events(self, days_ahead: int = 7) -> List[Dict]:
        """Get all significant events in the next N days."""
        from datetime import timedelta
        
        upcoming = []
        for i in range(days_ahead + 1):
            check_date = date.today() + timedelta(days=i)
            event_info = self.is_event_day(check_date)
            
            if event_info["is_high_volatility"]:
                upcoming.append(event_info)
        
        return upcoming
    
    def get_next_expiry(self) -> Dict:
        """Get the next monthly expiry date."""
        expiries = self.get_monthly_expiry_dates(date.today().year)
        
        for expiry in expiries:
            if expiry >= date.today():
                days_to_expiry = (expiry - date.today()).days
                return {
                    "date": expiry.isoformat(),
                    "days_to_expiry": days_to_expiry,
                    "is_expiry_week": days_to_expiry <= 4
                }
        
        # Check next year
        next_year_expiries = self.get_monthly_expiry_dates(date.today().year + 1)
        if next_year_expiries:
            expiry = next_year_expiries[0]
            days_to_expiry = (expiry - date.today()).days
            return {
                "date": expiry.isoformat(),
                "days_to_expiry": days_to_expiry,
                "is_expiry_week": days_to_expiry <= 4
            }
        
        return {"error": "No expiry found"}
    
    def adjust_position_size(self, base_size: float, check_date: Optional[date] = None) -> float:
        """
        Adjust position size based on event calendar.
        Returns dampened size on high-volatility days.
        """
        event_info = self.is_event_day(check_date)
        adjusted = base_size * event_info["risk_multiplier"]
        
        if event_info["is_high_volatility"]:
            logger.info("position_dampened", 
                       events=event_info["events"],
                       original=base_size,
                       adjusted=adjusted,
                       multiplier=event_info["risk_multiplier"])
        
        return adjusted

if __name__ == "__main__":
    print("Event Calendar Alpha")
    print("=" * 50)
    
    calendar = EventCalendarAlpha()
    
    # Check today
    today_info = calendar.is_event_day()
    print(f"\nToday ({today_info['date']}):")
    print(f"  Events: {today_info['events']}")
    print(f"  Position Size: {today_info['position_size_pct']}%")
    print(f"  High Volatility: {today_info['is_high_volatility']}")
    
    # Next expiry
    expiry = calendar.get_next_expiry()
    print(f"\nNext Expiry: {expiry.get('date', 'N/A')}")
    print(f"  Days to Expiry: {expiry.get('days_to_expiry', 'N/A')}")
    print(f"  Expiry Week: {expiry.get('is_expiry_week', 'N/A')}")
    
    # Upcoming events
    upcoming = calendar.get_upcoming_events(days_ahead=30)
    print(f"\nUpcoming High-Volatility Days (next 30 days): {len(upcoming)}")
    for event in upcoming[:5]:
        print(f"  {event['date']}: {event['events']} - {event['position_size_pct']}%")
    
    # Monthly expiries for 2026
    print("\n2026 Monthly Expiry Dates:")
    for exp in calendar.get_monthly_expiry_dates(2026):
        print(f"  {exp.strftime('%Y-%m-%d (%A)')}")
