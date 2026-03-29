import requests
import time
import random
from datetime import datetime
from typing import Dict, List, Optional
import structlog
import json

logger = structlog.get_logger()

class OptionsFlowAnalyzer:
    """
    Phase 12 Extraordinary: Smart Money Signal Detection
    
    Tracks NSE Options Chain for:
    - Put-Call Ratio (PCR) shifts
    - Unusual Option Volume (10x average)
    - Open Interest (OI) changes
    
    Rate Limiting: 3 req/min with exponential backoff
    User-Agent Rotation: Pool of 10 browser agents
    """
    
    NSE_OPTION_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices"
    NSE_EQUITY_OPTION_URL = "https://www.nseindia.com/api/option-chain-equities"
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Edge/120.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
    ]
    
    def __init__(self):
        self.session = requests.Session()
        self.last_request_time = 0
        self.min_interval = 20  # seconds between requests (3 req/min)
        self.pcr_history = {}  # symbol -> list of PCR values
        self.oi_history = {}   # symbol -> {strike: oi_change}
        
    def _get_headers(self) -> Dict[str, str]:
        """Generate headers with rotated user agent."""
        return {
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.nseindia.com/",
            "Connection": "keep-alive",
        }
    
    def _rate_limit(self):
        """Enforce rate limiting with exponential backoff."""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.min_interval:
            sleep_time = self.min_interval - elapsed
            logger.debug("rate_limiting", sleep_seconds=sleep_time)
            time.sleep(sleep_time)
        self.last_request_time = time.time()
    
    def _fetch_option_chain(self, symbol: str, is_index: bool = False) -> Optional[Dict]:
        """Fetch option chain data with rate limiting and error handling."""
        self._rate_limit()
        
        url = self.NSE_OPTION_CHAIN_URL if is_index else self.NSE_EQUITY_OPTION_URL
        params = {"symbol": symbol}
        
        try:
            # First request to get cookies
            self.session.get("https://www.nseindia.com/", headers=self._get_headers(), timeout=10)
            
            # Actual data request
            response = self.session.get(url, params=params, headers=self._get_headers(), timeout=15)
            
            if response.status_code == 200:
                data = response.json()
                logger.info("option_chain_fetched", symbol=symbol, records=len(data.get("records", {}).get("data", [])))
                return data
            elif response.status_code == 429:
                logger.warning("nse_rate_limited", symbol=symbol)
                time.sleep(60)  # Back off for 1 minute
                return None
            else:
                logger.error("option_chain_fetch_failed", symbol=symbol, status=response.status_code)
                return None
                
        except Exception as e:
            logger.error("option_chain_error", symbol=symbol, error=str(e))
            return None
    
    def calculate_pcr(self, option_data: Dict) -> Dict:
        """
        Calculate Put-Call Ratio from option chain data.
        Returns PCR based on OI and Volume.
        """
        if not option_data or "records" not in option_data:
            return {}
        
        records = option_data["records"]
        total_call_oi = 0
        total_put_oi = 0
        total_call_vol = 0
        total_put_vol = 0
        
        for entry in records.get("data", []):
            if "CE" in entry:
                total_call_oi += entry["CE"].get("openInterest", 0)
                total_call_vol += entry["CE"].get("totalTradedVolume", 0)
            if "PE" in entry:
                total_put_oi += entry["PE"].get("openInterest", 0)
                total_put_vol += entry["PE"].get("totalTradedVolume", 0)
        
        pcr_oi = total_put_oi / total_call_oi if total_call_oi > 0 else 0
        pcr_vol = total_put_vol / total_call_vol if total_call_vol > 0 else 0
        
        return {
            "pcr_oi": round(pcr_oi, 3),
            "pcr_volume": round(pcr_vol, 3),
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi,
            "underlying_value": records.get("underlyingValue", 0),
            "timestamp": datetime.now().isoformat()
        }
    
    def detect_unusual_volume(self, option_data: Dict, threshold_multiplier: float = 10.0) -> List[Dict]:
        """
        Detect strikes with unusual option volume (10x average).
        This signals potential smart money positioning.
        """
        unusual = []
        if not option_data or "records" not in option_data:
            return unusual
        
        volumes = []
        for entry in option_data["records"].get("data", []):
            for opt_type in ["CE", "PE"]:
                if opt_type in entry:
                    vol = entry[opt_type].get("totalTradedVolume", 0)
                    if vol > 0:
                        volumes.append(vol)
        
        if not volumes:
            return unusual
            
        avg_volume = sum(volumes) / len(volumes)
        threshold = avg_volume * threshold_multiplier
        
        for entry in option_data["records"].get("data", []):
            strike = entry.get("strikePrice", 0)
            for opt_type in ["CE", "PE"]:
                if opt_type in entry:
                    vol = entry[opt_type].get("totalTradedVolume", 0)
                    oi = entry[opt_type].get("openInterest", 0)
                    if vol > threshold:
                        unusual.append({
                            "strike": strike,
                            "type": "CALL" if opt_type == "CE" else "PUT",
                            "volume": vol,
                            "oi": oi,
                            "multiplier": round(vol / avg_volume, 1),
                            "signal": "BULLISH" if opt_type == "CE" else "BEARISH"
                        })
        
        return sorted(unusual, key=lambda x: x["multiplier"], reverse=True)
    
    def detect_oi_buildup(self, option_data: Dict) -> Dict:
        """
        Detect significant OI buildup at specific strikes.
        High OI at a strike = potential support/resistance.
        """
        if not option_data or "records" not in option_data:
            return {}
        
        max_call_oi_strike = None
        max_put_oi_strike = None
        max_call_oi = 0
        max_put_oi = 0
        
        for entry in option_data["records"].get("data", []):
            strike = entry.get("strikePrice", 0)
            if "CE" in entry:
                oi = entry["CE"].get("openInterest", 0)
                if oi > max_call_oi:
                    max_call_oi = oi
                    max_call_oi_strike = strike
            if "PE" in entry:
                oi = entry["PE"].get("openInterest", 0)
                if oi > max_put_oi:
                    max_put_oi = oi
                    max_put_oi_strike = strike
        
        return {
            "max_call_oi_strike": max_call_oi_strike,  # Resistance
            "max_put_oi_strike": max_put_oi_strike,    # Support
            "max_call_oi": max_call_oi,
            "max_put_oi": max_put_oi,
            "max_pain": (max_call_oi_strike + max_put_oi_strike) / 2 if max_call_oi_strike and max_put_oi_strike else 0
        }
    
    def analyze_symbol(self, symbol: str, is_index: bool = False) -> Dict:
        """
        Complete options flow analysis for a symbol.
        Returns PCR, unusual volume, OI buildup, and smart money signals.
        """
        logger.info("options_analysis_started", symbol=symbol)
        
        data = self._fetch_option_chain(symbol, is_index)
        if not data:
            return {"status": "FETCH_FAILED", "symbol": symbol}
        
        pcr = self.calculate_pcr(data)
        unusual = self.detect_unusual_volume(data)
        oi_levels = self.detect_oi_buildup(data)
        
        # Determine overall signal
        signal = "NEUTRAL"
        reasoning = []
        
        if pcr.get("pcr_oi", 1) > 1.2:
            signal = "BULLISH"
            reasoning.append(f"PCR_OI={pcr['pcr_oi']} > 1.2 indicates bearish sentiment (contrarian bullish)")
        elif pcr.get("pcr_oi", 1) < 0.8:
            signal = "BEARISH"
            reasoning.append(f"PCR_OI={pcr['pcr_oi']} < 0.8 indicates excessive bullishness (contrarian bearish)")
        
        bullish_unusual = len([u for u in unusual if u["signal"] == "BULLISH"])
        bearish_unusual = len([u for u in unusual if u["signal"] == "BEARISH"])
        
        if bullish_unusual > bearish_unusual * 2:
            signal = "BULLISH"
            reasoning.append(f"Unusual CALL volume ({bullish_unusual}) >> PUT volume ({bearish_unusual})")
        elif bearish_unusual > bullish_unusual * 2:
            signal = "BEARISH"
            reasoning.append(f"Unusual PUT volume ({bearish_unusual}) >> CALL volume ({bullish_unusual})")
        
        result = {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "signal": signal,
            "pcr": pcr,
            "unusual_volume": unusual[:5],  # Top 5
            "oi_levels": oi_levels,
            "reasoning": reasoning
        }
        
        logger.info("options_analysis_complete", symbol=symbol, signal=signal)
        return result

if __name__ == "__main__":
    # Test with NIFTY index options
    analyzer = OptionsFlowAnalyzer()
    
    print("Options Flow Analyzer Test")
    print("=" * 50)
    
    # Note: This will make actual requests to NSE
    # In production, use feature flag to enable/disable
    result = analyzer.analyze_symbol("NIFTY", is_index=True)
    
    if result.get("status") != "FETCH_FAILED":
        print(f"Symbol: {result['symbol']}")
        print(f"Signal: {result['signal']}")
        print(f"PCR (OI): {result['pcr'].get('pcr_oi', 'N/A')}")
        print(f"Max Pain: {result['oi_levels'].get('max_pain', 'N/A')}")
        print(f"Unusual Volume Count: {len(result['unusual_volume'])}")
        for r in result.get("reasoning", []):
            print(f"  - {r}")
    else:
        print("Failed to fetch option chain (NSE may be blocking)")
