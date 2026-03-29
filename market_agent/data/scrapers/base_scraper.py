"""
Phase 45: Base Scraper Class
Provides common functionality for all web scrapers:
- Rate limiting
- User-agent rotation
- Caching
- Error handling
"""

import time
import random
import requests
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import structlog
from functools import lru_cache

logger = structlog.get_logger()

# User-Agent Pool for rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36 OPR/105.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]


class RateLimitError(Exception):
    """Raised when rate limit is hit."""
    pass


class ScraperCache:
    """Simple in-memory cache with TTL."""
    
    def __init__(self, default_ttl_minutes: int = 5):
        self._cache: Dict[str, Dict] = {}
        self.default_ttl = timedelta(minutes=default_ttl_minutes)
    
    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            entry = self._cache[key]
            if datetime.now() < entry['expires']:
                logger.debug("cache_hit", key=key)
                return entry['value']
            else:
                del self._cache[key]
        return None
    
    def set(self, key: str, value: Any, ttl_minutes: int = None):
        ttl = timedelta(minutes=ttl_minutes) if ttl_minutes else self.default_ttl
        self._cache[key] = {
            'value': value,
            'expires': datetime.now() + ttl,
            'created': datetime.now()
        }
    
    def clear(self):
        self._cache.clear()


class BaseScraper(ABC):
    """
    Abstract base class for all scrapers.
    Handles rate limiting, user-agent rotation, and caching.
    """
    
    def __init__(self, 
                 min_delay_seconds: float = 2.0,
                 max_delay_seconds: float = 5.0,
                 cache_ttl_minutes: int = 5):
        self.min_delay = min_delay_seconds
        self.max_delay = max_delay_seconds
        self.cache = ScraperCache(cache_ttl_minutes)
        self.last_request_time = 0
        self.request_count = 0
        self.session = requests.Session()
    
    def _get_headers(self) -> Dict[str, str]:
        """Returns headers with a random user-agent."""
        return {
            'User-Agent': random.choice(USER_AGENTS),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    
    def _respect_rate_limit(self):
        """Ensures minimum delay between requests."""
        elapsed = time.time() - self.last_request_time
        delay = random.uniform(self.min_delay, self.max_delay)
        
        if elapsed < delay:
            sleep_time = delay - elapsed
            logger.debug("rate_limit_wait", seconds=sleep_time)
            time.sleep(sleep_time)
        
        self.last_request_time = time.time()
    
    def _make_request(self, url: str, method: str = 'GET', **kwargs) -> requests.Response:
        """Makes an HTTP request with rate limiting and error handling."""
        self._respect_rate_limit()
        
        headers = self._get_headers()
        headers.update(kwargs.pop('headers', {}))
        
        try:
            response = self.session.request(
                method, 
                url, 
                headers=headers, 
                timeout=30,
                **kwargs
            )
            self.request_count += 1
            
            if response.status_code == 429:
                raise RateLimitError(f"Rate limited by {url}")
            
            response.raise_for_status()
            return response
            
        except requests.exceptions.RequestException as e:
            logger.error("scraper_request_failed", url=url, error=str(e))
            raise
    
    def fetch_with_cache(self, url: str, cache_key: str = None, ttl_minutes: int = None) -> str:
        """Fetches URL with caching."""
        key = cache_key or url
        
        cached = self.cache.get(key)
        if cached:
            return cached
        
        response = self._make_request(url)
        content = response.text
        
        self.cache.set(key, content, ttl_minutes)
        return content
    
    @abstractmethod
    def fetch(self, symbol: str) -> Dict[str, Any]:
        """Fetches data for a given symbol. Must be implemented by subclasses."""
        pass
    
    @abstractmethod
    def get_source_name(self) -> str:
        """Returns the name of the data source."""
        pass


# Utility functions
def normalize_symbol(symbol: str) -> str:
    """Normalizes stock symbols (e.g., 'RELIANCE.NS' -> 'RELIANCE')."""
    return symbol.replace('.NS', '').replace('.BO', '').upper()


def parse_indian_number(value: str) -> float:
    """Parses Indian number format (e.g., '1,234.56 Cr' -> 12345600000)."""
    if not value or value == '-':
        return 0.0
    
    value = value.strip().replace(',', '')
    
    multiplier = 1
    if 'Cr' in value:
        multiplier = 10_000_000  # 1 Crore = 10 million
        value = value.replace('Cr', '').strip()
    elif 'L' in value:
        multiplier = 100_000  # 1 Lakh = 100 thousand
        value = value.replace('L', '').strip()
    
    try:
        return float(value) * multiplier
    except ValueError:
        return 0.0
