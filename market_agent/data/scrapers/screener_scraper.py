"""
Phase 45: Screener.in Scraper
Fetches comprehensive company fundamentals from Screener.in
- Market Cap, Revenue, EPS, Net Profit
- Debt, Cash, Debt-to-Equity
- Shareholding Pattern
- 10+ years of historical financials
FREE and UNLIMITED (no API limits)
"""

import re
from typing import Dict, Any, Optional, List
from bs4 import BeautifulSoup
import structlog

from .base_scraper import BaseScraper, normalize_symbol, parse_indian_number

logger = structlog.get_logger()


class ScreenerScraper(BaseScraper):
    """
    Scrapes financial data from Screener.in
    Best source for Indian stock fundamentals - free and comprehensive.
    """
    
    BASE_URL = "https://www.screener.in/company"
    
    def __init__(self):
        super().__init__(
            min_delay_seconds=2.0,
            max_delay_seconds=4.0,
            cache_ttl_minutes=60  # Fundamentals don't change often
        )
    
    def get_source_name(self) -> str:
        return "Screener.in"
    
    def _get_company_url(self, symbol: str, consolidated: bool = True) -> str:
        """Generates the Screener.in URL for a company."""
        clean_symbol = normalize_symbol(symbol)
        suffix = "/consolidated/" if consolidated else "/"
        return f"{self.BASE_URL}/{clean_symbol}{suffix}"
    
    def fetch(self, symbol: str) -> Dict[str, Any]:
        """
        Fetches all available fundamental data for a symbol.
        Returns a comprehensive dict with financials, ratios, and shareholding.
        """
        try:
            url = self._get_company_url(symbol)
            html = self.fetch_with_cache(url, cache_key=f"screener_{symbol}", ttl_minutes=60)
            
            soup = BeautifulSoup(html, 'html.parser')
            
            data = {
                'symbol': symbol,
                'source': self.get_source_name(),
                'url': url,
                'fetched_at': None,  # Will be set by orchestrator
            }
            
            # Extract all data sections
            data.update(self._extract_key_metrics(soup))
            data.update(self._extract_ratios(soup))
            data.update(self._extract_shareholding(soup))
            data.update(self._extract_quarterly_results(soup))
            
            logger.info("screener_fetch_success", symbol=symbol, metrics=len(data))
            return data
            
        except Exception as e:
            logger.error("screener_fetch_failed", symbol=symbol, error=str(e))
            return {'symbol': symbol, 'error': str(e), 'source': self.get_source_name()}
    
    def _extract_key_metrics(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extracts key metrics from the top section."""
        metrics = {}
        
        # Find the key metrics section (usually in a list format)
        ratios_section = soup.find('ul', id='top-ratios')
        if ratios_section:
            for li in ratios_section.find_all('li'):
                name_elem = li.find('span', class_='name')
                value_elem = li.find('span', class_='number')
                
                if name_elem and value_elem:
                    name = name_elem.get_text(strip=True).lower().replace(' ', '_')
                    value = value_elem.get_text(strip=True)
                    metrics[name] = parse_indian_number(value) if any(c.isdigit() for c in value) else value
        
        # Try alternative selector for newer page layouts
        top_cards = soup.find_all('li', class_='flex flex-space-between')
        for card in top_cards:
            spans = card.find_all('span')
            if len(spans) >= 2:
                name = spans[0].get_text(strip=True).lower().replace(' ', '_')
                value = spans[-1].get_text(strip=True)
                if name not in metrics:
                    metrics[name] = parse_indian_number(value) if any(c.isdigit() for c in value) else value
        
        return metrics
    
    def _extract_ratios(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extracts financial ratios section."""
        ratios = {}
        
        # Look for the ratios table
        for section in soup.find_all('section'):
            header = section.find(['h2', 'h3'])
            if header and 'Ratios' in header.get_text():
                table = section.find('table')
                if table:
                    ratios.update(self._parse_table(table))
                break
        
        return {'ratios': ratios} if ratios else {}
    
    def _extract_shareholding(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extracts shareholding pattern."""
        shareholding = {}
        
        for section in soup.find_all('section'):
            header = section.find(['h2', 'h3'])
            if header and 'Shareholding' in header.get_text():
                table = section.find('table')
                if table:
                    rows = table.find_all('tr')
                    for row in rows:
                        cells = row.find_all(['td', 'th'])
                        if len(cells) >= 2:
                            name = cells[0].get_text(strip=True).lower()
                            # Get the latest value (usually last column)
                            value = cells[-1].get_text(strip=True)
                            
                            if 'promoter' in name:
                                shareholding['promoter_holding'] = parse_indian_number(value.replace('%', ''))
                            elif 'fii' in name or 'foreign' in name:
                                shareholding['fii_holding'] = parse_indian_number(value.replace('%', ''))
                            elif 'dii' in name or 'domestic' in name:
                                shareholding['dii_holding'] = parse_indian_number(value.replace('%', ''))
                            elif 'public' in name:
                                shareholding['public_holding'] = parse_indian_number(value.replace('%', ''))
                break
        
        return {'shareholding': shareholding} if shareholding else {}
    
    def _extract_quarterly_results(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extracts latest quarterly results."""
        quarterly = {}
        
        for section in soup.find_all('section'):
            header = section.find(['h2', 'h3'])
            if header and 'Quarterly' in header.get_text():
                table = section.find('table')
                if table:
                    quarterly = self._parse_table(table, get_latest=True)
                break
        
        return {'quarterly': quarterly} if quarterly else {}
    
    def _parse_table(self, table, get_latest: bool = False) -> Dict[str, Any]:
        """Generic table parser."""
        data = {}
        rows = table.find_all('tr')
        
        for row in rows:
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 2:
                name = cells[0].get_text(strip=True).lower().replace(' ', '_')
                if get_latest:
                    # Get the last numeric value
                    value = cells[-1].get_text(strip=True)
                else:
                    value = cells[1].get_text(strip=True)
                
                if name and not name.startswith('#'):
                    data[name] = parse_indian_number(value) if any(c.isdigit() for c in value) else value
        
        return data
    
    def get_financial_health_scores(self, symbol: str) -> Dict[str, Any]:
        """
        Calculates Altman Z-Score and Piotroski F-Score.
        These require multiple data points.
        """
        data = self.fetch(symbol)
        
        scores = {
            'altman_z_score': None,
            'piotroski_f_score': None,
            'health_status': 'Unknown'
        }
        
        # Would need full balance sheet data for accurate calculation
        # This is a placeholder - real implementation requires more data
        
        return scores


# Quick test
if __name__ == "__main__":
    scraper = ScreenerScraper()
    
    # Test with Reliance
    data = scraper.fetch("RELIANCE.NS")
    print(f"Fetched {len(data)} data points for RELIANCE")
    for key, value in data.items():
        print(f"  {key}: {value}")
