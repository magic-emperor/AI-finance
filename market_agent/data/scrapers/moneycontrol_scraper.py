"""
Phase 45: Moneycontrol News Scraper
Fetches 24/7 news coverage for Indian stocks from Moneycontrol.
- Breaking news
- Company-specific news
- Sector news
- Market analysis
Works even during off-market hours!
"""

from typing import Dict, Any, List
from bs4 import BeautifulSoup
from datetime import datetime
import re
import structlog

from .base_scraper import BaseScraper, normalize_symbol

logger = structlog.get_logger()


class MoneycontrolScraper(BaseScraper):
    """
    Scrapes news from Moneycontrol.com
    Provides 24/7 news coverage for Indian markets.
    """
    
    BASE_URL = "https://www.moneycontrol.com"
    NEWS_URL = f"{BASE_URL}/news/business/stocks"
    COMPANY_NEWS_URL = f"{BASE_URL}/company-article"
    
    # Symbol to Moneycontrol company code mapping (samples)
    SYMBOL_MAP = {
        'RELIANCE': 'RI',
        'TCS': 'TCS',
        'INFY': 'IT',
        'HDFC': 'HDF01',
        'ICICIBANK': 'ICI02',
        'ITC': 'ITC',
        'BHARTIARTL': 'BA',
        'HCLTECH': 'HCL02',
        'KOTAKBANK': 'KMB',
        'SBIN': 'SBI',
        'TATASTEEL': 'TIS',
        'WIPRO': 'W05',
        'TATAMOTORS': 'TM03',
        'MARUTI': 'MS24',
        'HDFCBANK': 'HDF01',
    }
    
    def __init__(self):
        super().__init__(
            min_delay_seconds=2.0,
            max_delay_seconds=4.0,
            cache_ttl_minutes=5  # News changes frequently
        )
    
    def get_source_name(self) -> str:
        return "Moneycontrol"
    
    def fetch(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Fetches news articles. If symbol provided, fetches company-specific news.
        Otherwise fetches general market news.
        """
        if symbol:
            return self._fetch_company_news(symbol)
        else:
            return self._fetch_market_news()
    
    def _fetch_market_news(self) -> List[Dict[str, Any]]:
        """Fetches general stock market news."""
        try:
            html = self.fetch_with_cache(self.NEWS_URL, cache_key="mc_market_news", ttl_minutes=5)
            return self._parse_news_page(html)
        except Exception as e:
            logger.error("moneycontrol_market_news_failed", error=str(e))
            return []
    
    def _fetch_company_news(self, symbol: str) -> List[Dict[str, Any]]:
        """Fetches news for a specific company."""
        clean_symbol = normalize_symbol(symbol)
        
        # Try to find company code
        mc_code = self.SYMBOL_MAP.get(clean_symbol, clean_symbol)
        
        # Moneycontrol search URL
        search_url = f"{self.BASE_URL}/news/tags/{clean_symbol.lower()}.html"
        
        try:
            html = self.fetch_with_cache(search_url, cache_key=f"mc_news_{clean_symbol}", ttl_minutes=5)
            news = self._parse_news_page(html)
            
            # Add symbol to each news item
            for item in news:
                item['symbol'] = symbol
            
            return news
            
        except Exception as e:
            logger.error("moneycontrol_company_news_failed", symbol=symbol, error=str(e))
            return []
    
    def _parse_news_page(self, html: str) -> List[Dict[str, Any]]:
        """Parses a news page and extracts articles."""
        soup = BeautifulSoup(html, 'html.parser')
        articles = []
        
        # Try multiple selectors for different page layouts
        news_items = (
            soup.find_all('li', class_='clearfix') or
            soup.find_all('div', class_='news_item') or
            soup.find_all('article')
        )
        
        for item in news_items[:20]:  # Limit to 20 articles
            article = self._parse_article(item)
            if article:
                articles.append(article)
        
        logger.info("moneycontrol_parsed", count=len(articles))
        return articles
    
    def _parse_article(self, item) -> Dict[str, Any]:
        """Parses a single news article element."""
        try:
            # Find headline
            headline_elem = item.find(['h2', 'h3', 'a'])
            if not headline_elem:
                return None
            
            headline = headline_elem.get_text(strip=True)
            if not headline or len(headline) < 10:
                return None
            
            # Find link
            link = None
            link_elem = item.find('a', href=True)
            if link_elem:
                link = link_elem['href']
                if not link.startswith('http'):
                    link = self.BASE_URL + link
            
            # Find timestamp
            time_elem = item.find(['time', 'span'], class_=re.compile(r'(time|date|ago)', re.I))
            timestamp = time_elem.get_text(strip=True) if time_elem else None
            
            # Find summary
            summary_elem = item.find(['p', 'div'], class_=re.compile(r'(desc|summary|content)', re.I))
            summary = summary_elem.get_text(strip=True) if summary_elem else None
            
            # Analyze sentiment (basic keyword analysis)
            sentiment = self._analyze_sentiment(headline + (summary or ''))
            
            return {
                'headline': headline,
                'summary': summary,
                'url': link,
                'timestamp': timestamp,
                'source': self.get_source_name(),
                'sentiment': sentiment,
                'fetched_at': datetime.now().isoformat(),
            }
            
        except Exception as e:
            logger.debug("article_parse_failed", error=str(e))
            return None
    
    def _analyze_sentiment(self, text: str) -> float:
        """
        Basic sentiment analysis using keywords.
        Returns: -1 (negative) to +1 (positive)
        """
        text = text.lower()
        
        positive_words = [
            'rally', 'surge', 'gain', 'jump', 'soar', 'bullish', 'growth',
            'profit', 'rise', 'up', 'high', 'best', 'record', 'boost',
            'strong', 'positive', 'beat', 'outperform', 'dividend'
        ]
        
        negative_words = [
            'fall', 'drop', 'crash', 'slump', 'bearish', 'loss', 'down',
            'low', 'worst', 'decline', 'weak', 'negative', 'miss', 'cut',
            'warning', 'concern', 'risk', 'debt', 'default', 'sell'
        ]
        
        positive_count = sum(1 for word in positive_words if word in text)
        negative_count = sum(1 for word in negative_words if word in text)
        
        total = positive_count + negative_count
        if total == 0:
            return 0.5  # Neutral
        
        return (positive_count - negative_count + total) / (2 * total)
    
    def get_breaking_news(self) -> List[Dict[str, Any]]:
        """Fetches the latest breaking news from the homepage."""
        try:
            html = self.fetch_with_cache(f"{self.BASE_URL}/news/latest-news", cache_key="mc_breaking", ttl_minutes=2)
            return self._parse_news_page(html)
        except Exception as e:
            logger.error("moneycontrol_breaking_failed", error=str(e))
            return []


# Quick test
if __name__ == "__main__":
    scraper = MoneycontrolScraper()
    
    # Test general news
    print("=== Market News ===")
    news = scraper.fetch()
    for article in news[:5]:
        print(f"  [{article['sentiment']:.2f}] {article['headline'][:60]}...")
    
    # Test company news
    print("\n=== RELIANCE News ===")
    news = scraper.fetch("RELIANCE.NS")
    for article in news[:5]:
        print(f"  [{article['sentiment']:.2f}] {article['headline'][:60]}...")
