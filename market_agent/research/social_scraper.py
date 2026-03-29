
import requests
from bs4 import BeautifulSoup
import structlog
from typing import Dict, Any

logger = structlog.get_logger()

class SocialScraper:
    """
    Scrapes sentiment from StockTwits and Reddit as a fallback for API restrictions.
    """
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

    def get_stocktwits_sentiment(self, symbol: str) -> Dict[str, Any]:
        """Scrapes sentiment from StockTwits symbol page."""
        try:
            # Clean symbol for StockTwits (e.g. BTC-USD -> BTC.X)
            clean_symbol = symbol.replace("-USD", ".X").upper()
            url = f"https://stocktwits.com/symbol/{clean_symbol}"
            
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code != 200:
                logger.warning("stocktwits_scrape_failed", status=response.status_code)
                return {"sentiment": 0.5, "mood": "NEUTRAL"}
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Simple heuristic: Look for 'bullish'/'bearish' keywords in recent posts
            text = soup.get_text().lower()
            bulls = text.count("bullish")
            bears = text.count("bearish")
            
            if bulls + bears == 0:
                return {"sentiment": 0.5, "mood": "NEUTRAL"}
                
            sentiment = bulls / (bulls + bears)
            mood = "BULLISH" if sentiment > 0.6 else ("BEARISH" if sentiment < 0.4 else "NEUTRAL")
            
            return {"sentiment": round(sentiment, 2), "mood": mood}
            
        except Exception as e:
            logger.error("social_scrape_error", error=str(e))
            return {"sentiment": 0.5, "mood": "NEUTRAL"}

    def get_reddit_sentiment(self, subreddit: str = "WallStreetBets") -> Dict[str, Any]:
        """Guest scrape of top threads from Reddit."""
        try:
            url = f"https://www.reddit.com/r/{subreddit}/top/.rss"
            # Using RSS as a 'stable' scrape path for guest access
            import feedparser
            feed = feedparser.parse(url)
            
            combined_text = ""
            for entry in feed.entries[:10]:
                combined_text += entry.title + " " + entry.summary
                
            combined_text = combined_text.lower()
            bulls = combined_text.count("moon") + combined_text.count("calls") + combined_text.count("🚀")
            bears = combined_text.count("crash") + combined_text.count("puts") + combined_text.count("📉")
            
            if bulls + bears == 0:
                return {"sentiment": 0.5, "mood": "NEUTRAL"}
                
            sentiment = bulls / (bulls + bears)
            mood = "EXCITED" if sentiment > 0.7 else ("CAUTIOUS" if sentiment < 0.3 else "WAITING")
            
            return {"sentiment": round(sentiment, 2), "mood": mood}
        except Exception as e:
            logger.error("reddit_scrape_error", error=str(e))
            return {"sentiment": 0.5, "mood": "NEUTRAL"}

# Singleton
social_scraper = SocialScraper()
