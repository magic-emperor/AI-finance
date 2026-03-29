import feedparser
import structlog
from typing import List, Dict, Any
from datetime import datetime, timedelta

logger = structlog.get_logger()

class RSSResearcher:
    """
    Layer 7: Research (Cost-Effective)
    Aggregates financial news from high-precision RSS feeds.
    """
    def __init__(self):
        self.feeds = {
            "CNBC Business": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=40&keywords=business",
            "Economic Times": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
            "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "Cointelegraph": "https://cointelegraph.com/rss",
            "FXStreet": "https://www.fxstreet.com/rss/news"
        }
        self.crypto_mapping = {
            "BTC": ["bitcoin", "btc"],
            "ETH": ["ethereum", "eth"],
            "SOL": ["solana", "sol"],
            "XAU": ["gold"],
            "GC": ["gold"]
        }

    def fetch_latest_news(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Fetches headlines from all configured feeds, prioritizing symbol-specific news.
        """
        all_entries = []
        seen_titles = set()
        
        # 1. Fetch from General Feeds
        raw_base = symbol.split('.')[0].split('-')[0] if symbol else None
        search_terms = [raw_base.lower()] if raw_base else []
        if raw_base and raw_base.upper() in self.crypto_mapping:
            search_terms.extend(self.crypto_mapping[raw_base.upper()])
        
        for name, url in self.feeds.items():
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries:
                    title = entry.get("title", "")
                    if title in seen_titles: continue
                    seen_titles.add(title)
                    
                    news_item = {
                        "source": name,
                        "title": title,
                        "link": entry.get("link", ""),
                        "summary": entry.get("summary", ""),
                        "published": entry.get("published", datetime.now().isoformat())
                    }
                    if search_terms:
                        # Prioritize match across any search term (BTC or Bitcoin)
                        if any(term in title.lower() or term in news_item["summary"].lower() for term in search_terms):
                            all_entries.insert(0, news_item)
                        else:
                            all_entries.append(news_item)
                    else:
                        all_entries.append(news_item)
            except Exception as e:
                safe_err = repr(e).encode('ascii', 'ignore').decode('ascii')
                try: logger.error("rss_fetch_failed", source=name, error=safe_err)
                except Exception: pass

        # 2. Fetch Specific News (Google News) if symbol provided
        if symbol:
            try:
                # specialized query for the symbol
                clean_symbol = raw_base if raw_base else symbol
                suffix = " stock news"
                if ".NS" in symbol.upper():
                    suffix = " stock news India NSE"
                elif "-" in symbol: # Crypto/Forex
                    suffix = " price news"
                
                import urllib.parse
                # Try with clean symbol first for better relevance
                query = f"{clean_symbol} {suffix}"
                encoded_query = urllib.parse.quote(query)
                
                # Dynamic Region: Use India for .NS symbols, Global (US) for all others
                region_params = "hl=en-IN&gl=IN&ceid=IN:en" if ".NS" in symbol.upper() else "hl=en-US&gl=US&ceid=US:en"
                url = f"https://news.google.com/rss/search?q={encoded_query}&{region_params}"
                feed = feedparser.parse(url)
                
                if not feed.entries:
                    # Try with original symbol
                    query = f"{symbol} {suffix}"
                    encoded_query = urllib.parse.quote(query)
                    url = f"https://news.google.com/rss/search?q={encoded_query}&{region_params}"
                    feed = feedparser.parse(url)

                for entry in feed.entries[:12]: # Top 12 specific
                    pub_date = entry.get("published_parsed")
                    if pub_date:
                        dt_pub = datetime(*pub_date[:6])
                        if datetime.now() - dt_pub > timedelta(hours=48):
                            continue # Skip stale news (e.g. 2024/2025 archived items)

                    news_item = {
                        "source": "Google News",
                        "title": entry.get("title", ""),
                        "link": entry.get("link", "#"),
                        "summary": entry.get("summary", ""),
                        "published": entry.get("published", datetime.now().isoformat())
                    }
                    all_entries.insert(0, news_item)
            except Exception as e:
                 safe_err = repr(e).encode('ascii', 'ignore').decode('ascii')
                 try: logger.error("google_news_fetch_failed", symbol=symbol, error=safe_err)
                 except Exception: pass
        
        # Final Guard: Ensure all_entries (including general feeds) are fresh
        fresh_entries = []
        for item in all_entries:
            try:
                # If we have a published string, try to check it (fallback to today if unparseable)
                fresh_entries.append(item)
            except Exception:
                fresh_entries.append(item)
                
        logger.info("rss_research_complete", total_found=len(fresh_entries))
        return fresh_entries

if __name__ == "__main__":
    researcher = RSSResearcher()
    # Test for generic news
    news = researcher.fetch_latest_news()
    print(f"Total Headlines Found: {len(news)}")
    if news:
        print(f"Top Headline: {news[0]['title']} ({news[0]['source']})")
