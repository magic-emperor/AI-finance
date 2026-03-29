"""
Phase 45: Data Orchestrator
Coordinates all data sources with fallback logic:
1. Check PostgreSQL cache first
2. Try primary source (NSE/Screener)
3. Fallback to secondary sources
4. Update cache on success
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import structlog

from .base_scraper import RateLimitError, normalize_symbol
from .screener_scraper import ScreenerScraper
from .moneycontrol_scraper import MoneycontrolScraper
from .nse_scraper import NSEScraper

logger = structlog.get_logger()


class DataOrchestrator:
    """
    Coordinates multiple data sources with intelligent fallback.
    Provides a unified interface for all data needs.
    """
    
    def __init__(self, db_storage=None):
        """
        Args:
            db_storage: Optional PostgreSQL storage instance for caching
        """
        self.db = db_storage
        
        # Initialize scrapers
        self.screener = ScreenerScraper()
        self.moneycontrol = MoneycontrolScraper()
        self.nse = NSEScraper()
        
        # Track source health
        self.source_health = {
            'screener': True,
            'moneycontrol': True,
            'nse': True,
            'yfinance': True,
        }
        
        logger.info("orchestrator_initialized", sources=list(self.source_health.keys()))
    
    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches fundamental data with fallback logic.
        Priority: PostgreSQL Cache → Screener.in → yfinance
        """
        result = {
            'symbol': symbol,
            'sources_tried': [],
            'fetched_at': datetime.now().isoformat(),
        }
        
        # 1. Try PostgreSQL cache
        if self.db:
            cached = self._get_from_cache('fundamentals', symbol)
            if cached:
                result['source'] = 'cache'
                result.update(cached)
                return result
        
        # 2. Try Screener.in (primary - unlimited)
        if self.source_health['screener']:
            try:
                result['sources_tried'].append('screener')
                data = self.screener.fetch(symbol)
                if data and 'error' not in data:
                    result['source'] = 'screener.in'
                    result.update(data)
                    self._save_to_cache('fundamentals', symbol, data)
                    return result
            except RateLimitError:
                self.source_health['screener'] = False
                logger.warning("screener_rate_limited")
            except Exception as e:
                logger.error("screener_failed", error=str(e))
        
        # 3. Fallback to yfinance
        if self.source_health['yfinance']:
            try:
                result['sources_tried'].append('yfinance')
                import yfinance as yf
                ticker = yf.Ticker(symbol)
                info = ticker.info
                
                if info:
                    yf_data = {
                        'market_cap': info.get('marketCap'),
                        'revenue': info.get('totalRevenue'),
                        'net_profit': info.get('netIncomeToCommon'),
                        'eps': info.get('trailingEps'),
                        'pe_ratio': info.get('trailingPE'),
                        'book_value': info.get('bookValue'),
                        'debt': info.get('totalDebt'),
                        'cash': info.get('totalCash'),
                        'profit_margin': info.get('profitMargins'),
                    }
                    result['source'] = 'yfinance'
                    result.update(yf_data)
                    return result
                    
            except Exception as e:
                logger.error("yfinance_failed", error=str(e))
        
        result['error'] = 'All sources failed'
        return result
    
    def get_shareholding(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches shareholding pattern.
        Priority: NSE Official → Screener.in
        """
        result = {
            'symbol': symbol,
            'sources_tried': [],
        }
        
        # 1. Try NSE Official (most reliable)
        if self.source_health['nse']:
            try:
                result['sources_tried'].append('nse')
                data = self.nse.fetch(symbol)
                if 'shareholding' in data:
                    result['source'] = 'nse_official'
                    result.update(data['shareholding'])
                    return result
            except Exception as e:
                logger.debug("nse_shareholding_fallback", error=str(e))
        
        # 2. Fallback to Screener.in
        try:
            result['sources_tried'].append('screener')
            data = self.screener.fetch(symbol)
            if 'shareholding' in data:
                result['source'] = 'screener.in'
                result.update(data['shareholding'])
                return result
        except Exception as e:
            logger.error("shareholding_all_failed", error=str(e))
        
        return result
    
    def get_news(self, symbol: str = None, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Fetches news from all available sources.
        Deduplicates and sorts by recency.
        """
        all_news = []
        
        # 1. Moneycontrol (primary)
        try:
            mc_news = self.moneycontrol.fetch(symbol)
            all_news.extend(mc_news)
        except Exception as e:
            logger.debug("moneycontrol_news_failed", error=str(e))
        
        # 2. Could add more sources here (Economic Times, LiveMint, etc.)
        
        # Deduplicate by headline similarity
        seen_headlines = set()
        unique_news = []
        for article in all_news:
            headline = article.get('headline', '')[:50].lower()
            if headline not in seen_headlines:
                seen_headlines.add(headline)
                unique_news.append(article)
        
        # Sort by newest first (if timestamp available)
        unique_news.sort(key=lambda x: x.get('fetched_at', ''), reverse=True)
        
        return unique_news[:limit]
    
    def get_live_price(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches live price.
        Priority: Cache (< 1 min) → NSE → yfinance
        """
        result = {'symbol': symbol}
        
        # 1. Try NSE (official)
        if self.source_health['nse']:
            try:
                data = self.nse.fetch(symbol)
                if 'last_price' in data:
                    result['source'] = 'nse'
                    result.update(data)
                    return result
            except Exception as e:
                logger.debug("nse_price_fallback", error=str(e))
        
        # 2. Fallback to yfinance
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info = ticker.info
            
            result['source'] = 'yfinance'
            result['last_price'] = info.get('currentPrice') or info.get('regularMarketPrice')
            result['change_percent'] = info.get('regularMarketChangePercent')
            return result
            
        except Exception as e:
            logger.error("all_price_sources_failed", error=str(e))
        
        return result
    
    def get_full_analysis(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches comprehensive data from all sources.
        Returns a complete picture for analysis.
        """
        return {
            'symbol': symbol,
            'fetched_at': datetime.now().isoformat(),
            'fundamentals': self.get_fundamentals(symbol),
            'shareholding': self.get_shareholding(symbol),
            'news': self.get_news(symbol, limit=10),
            'price': self.get_live_price(symbol),
        }
    
    def _get_from_cache(self, data_type: str, symbol: str) -> Optional[Dict]:
        """Gets data from PostgreSQL cache if available and fresh."""
        if not self.db:
            return None
        # TODO: Implement cache retrieval from DB
        return None
    
    def _save_to_cache(self, data_type: str, symbol: str, data: Dict):
        """Saves data to PostgreSQL cache."""
        if not self.db:
            return
        # TODO: Implement cache storage to DB
        pass
    
    def reset_source_health(self):
        """Resets all source health flags (for retry after cooldown)."""
        for key in self.source_health:
            self.source_health[key] = True
        logger.info("source_health_reset")


# Quick test
if __name__ == "__main__":
    orchestrator = DataOrchestrator()
    
    print("=== Full Analysis: RELIANCE ===")
    data = orchestrator.get_full_analysis("RELIANCE.NS")
    
    print("\nFundamentals:")
    for key, value in data['fundamentals'].items():
        if key not in ['sources_tried', 'fetched_at']:
            print(f"  {key}: {value}")
    
    print("\nShareholding:")
    for key, value in data['shareholding'].items():
        if key not in ['sources_tried']:
            print(f"  {key}: {value}")
    
    print(f"\nNews: {len(data['news'])} articles")
    for article in data['news'][:3]:
        print(f"  - {article['headline'][:50]}...")
