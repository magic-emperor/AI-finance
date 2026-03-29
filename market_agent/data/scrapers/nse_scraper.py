"""
Phase 45: NSE Scraper
Fetches official data from NSE India:
- Live prices
- Shareholding patterns (Promoter, FII, DII, Public)
- Bulk deals
- Corporate announcements
OFFICIAL SOURCE - Most reliable for Indian stocks
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import structlog

from .base_scraper import BaseScraper, normalize_symbol

logger = structlog.get_logger()


class NSEScraper(BaseScraper):
    """
    Scrapes data from NSE India official website.
    Requires special headers to bypass their protection.
    """
    
    BASE_URL = "https://www.nseindia.com"
    API_URL = f"{BASE_URL}/api"
    
    def __init__(self):
        super().__init__(
            min_delay_seconds=3.0,  # NSE has stricter limits
            max_delay_seconds=5.0,
            cache_ttl_minutes=5
        )
        self._session_initialized = False
    
    def get_source_name(self) -> str:
        return "NSE India"
    
    def _get_headers(self) -> Dict[str, str]:
        """NSE requires specific headers."""
        headers = super()._get_headers()
        headers.update({
            'Referer': self.BASE_URL,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Encoding': 'gzip, deflate, br',
        })
        return headers
    
    def _init_session(self):
        """Initialize session by visiting the main page first (required by NSE)."""
        if not self._session_initialized:
            try:
                self._make_request(self.BASE_URL)
                self._session_initialized = True
                logger.info("nse_session_initialized")
            except Exception as e:
                logger.error("nse_session_init_failed", error=str(e))
    
    def fetch(self, symbol: str) -> Dict[str, Any]:
        """Fetches all available data for a symbol from NSE."""
        self._init_session()
        clean_symbol = normalize_symbol(symbol)
        
        data = {
            'symbol': symbol,
            'source': self.get_source_name(),
        }
        
        # Fetch quote
        quote = self._fetch_quote(clean_symbol)
        if quote:
            data.update(quote)
        
        # Fetch shareholding
        shareholding = self._fetch_shareholding(clean_symbol)
        if shareholding:
            data['shareholding'] = shareholding
        
        return data
    
    def _fetch_quote(self, symbol: str) -> Dict[str, Any]:
        """Fetches live quote data."""
        try:
            url = f"{self.API_URL}/quote-equity?symbol={symbol}"
            response = self._make_request(url)
            data = response.json()
            
            if 'priceInfo' in data:
                price_info = data['priceInfo']
                return {
                    'last_price': price_info.get('lastPrice'),
                    'change': price_info.get('change'),
                    'change_percent': price_info.get('pChange'),
                    'open': price_info.get('open'),
                    'high': price_info.get('dayHigh'),
                    'low': price_info.get('dayLow'),
                    'close': price_info.get('previousClose'),
                    'volume': data.get('preOpenMarket', {}).get('totalTradedVolume'),
                }
            
            return {}
            
        except Exception as e:
            logger.error("nse_quote_failed", symbol=symbol, error=str(e))
            return {}
    
    def _fetch_shareholding(self, symbol: str) -> Dict[str, Any]:
        """Fetches shareholding pattern."""
        try:
            # Alternative: Use the corporates API
            url = f"{self.API_URL}/corporates-shareholding?symbol={symbol}"
            response = self._make_request(url)
            data = response.json()
            
            shareholding = {}
            
            if isinstance(data, list) and len(data) > 0:
                latest = data[0]  # Most recent filing
                
                for item in latest.get('holdings', []):
                    category = item.get('category', '').lower()
                    holding = item.get('holdingPercentage', 0)
                    
                    if 'promoter' in category:
                        shareholding['promoter'] = holding
                    elif 'foreign' in category or 'fii' in category:
                        shareholding['fii'] = holding
                    elif 'mutual' in category or 'dii' in category:
                        shareholding['dii'] = holding
                    elif 'public' in category:
                        shareholding['public'] = holding
            
            return shareholding
            
        except Exception as e:
            logger.debug("nse_shareholding_failed", symbol=symbol, error=str(e))
            return {}
    
    def get_bulk_deals(self, date: str = None) -> List[Dict[str, Any]]:
        """Fetches bulk deals for a date."""
        self._init_session()
        
        try:
            url = f"{self.API_URL}/block-deal"
            if date:
                url += f"?date={date}"
            
            response = self._make_request(url)
            return response.json().get('data', [])
            
        except Exception as e:
            logger.error("nse_bulk_deals_failed", error=str(e))
            return []
    
    def get_fii_data(self) -> Dict[str, Any]:
        """Fetches FII/DII trading activity."""
        self._init_session()
        
        try:
            url = f"{self.API_URL}/fiidii"
            response = self._make_request(url)
            return response.json()
            
        except Exception as e:
            logger.error("nse_fii_data_failed", error=str(e))
            return {}


# Quick test
if __name__ == "__main__":
    scraper = NSEScraper()
    
    print("=== RELIANCE Quote ===")
    data = scraper.fetch("RELIANCE")
    for key, value in data.items():
        print(f"  {key}: {value}")
    
    print("\n=== FII/DII Activity ===")
    fii = scraper.get_fii_data()
    print(fii)
