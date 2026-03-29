import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional
import structlog

logger = structlog.get_logger()

class CrossAssetMacroRegime:
    """
    Phase 12 Extraordinary: Global Macro Context
    
    Ingests cross-asset data to determine global market regime:
    - USD/INR exchange rate
    - S&P 500 Futures (ES=F)
    - Gold (GC=F)
    - Crude Oil (CL=F)
    
    Logic: If global markets crash overnight, shift Monday bias to "Cautious"
    """
    
    GLOBAL_ASSETS = {
        "usdinr": "USDINR=X",      # Dollar strength = INR weakness
        "sp500_futures": "ES=F",   # US market sentiment
        "gold": "GC=F",            # Risk-off indicator
        "crude": "CL=F",           # Commodity cycle / inflation
        "vix": "^VIX",             # Fear gauge
    }
    
    REGIME_THRESHOLDS = {
        "sp500_crash": -0.02,      # -2% overnight = crash
        "sp500_rally": 0.015,      # +1.5% overnight = strong rally
        "vix_fear": 25,            # VIX > 25 = elevated fear
        "vix_panic": 35,           # VIX > 35 = panic
        "gold_flight": 0.015,      # Gold up 1.5% = risk-off
        "crude_shock": 0.05,       # Crude +/- 5% = supply shock
    }
    
    def __init__(self):
        self.latest_data = {}
        self.regime = "UNKNOWN"
        self.bias = "NEUTRAL"
    
    def fetch_global_snapshot(self) -> Dict:
        """
        Fetch latest price data for all global assets.
        Returns overnight changes and current levels.
        """
        logger.info("global_macro_fetch_started")
        snapshot = {}
        
        for name, ticker in self.GLOBAL_ASSETS.items():
            try:
                data = yf.Ticker(ticker)
                hist = data.history(period="5d", interval="1d")
                
                if len(hist) >= 2:
                    current = float(hist['Close'].iloc[-1])
                    previous = float(hist['Close'].iloc[-2])
                    change = (current - previous) / previous
                    
                    snapshot[name] = {
                        "ticker": ticker,
                        "current": round(current, 2),
                        "previous": round(previous, 2),
                        "change_pct": round(change * 100, 2),
                        "change": round(change, 4)
                    }
                    logger.debug("asset_fetched", name=name, change=f"{change*100:.2f}%")
            except Exception as e:
                logger.error("asset_fetch_failed", name=name, error=str(e))
                snapshot[name] = {"error": str(e)}
        
        self.latest_data = snapshot
        return snapshot
    
    def determine_regime(self, snapshot: Optional[Dict] = None) -> Dict:
        """
        Analyze global data to determine market regime.
        Returns regime classification and trading bias.
        """
        if snapshot is None:
            snapshot = self.latest_data
        
        if not snapshot:
            return {"regime": "UNKNOWN", "bias": "NEUTRAL", "reasons": ["No data"]}
        
        reasons = []
        bias_score = 0  # Positive = bullish, Negative = bearish
        
        # S&P 500 Futures Analysis
        sp500 = snapshot.get("sp500_futures", {})
        if "change" in sp500:
            change = sp500["change"]
            if change <= self.REGIME_THRESHOLDS["sp500_crash"]:
                bias_score -= 3
                reasons.append(f"S&P 500 crashed {sp500['change_pct']}% overnight")
            elif change >= self.REGIME_THRESHOLDS["sp500_rally"]:
                bias_score += 2
                reasons.append(f"S&P 500 rallied {sp500['change_pct']}% overnight")
        
        # VIX Analysis
        vix = snapshot.get("vix", {})
        if "current" in vix:
            level = vix["current"]
            if level >= self.REGIME_THRESHOLDS["vix_panic"]:
                bias_score -= 4
                reasons.append(f"VIX at panic level: {level}")
            elif level >= self.REGIME_THRESHOLDS["vix_fear"]:
                bias_score -= 2
                reasons.append(f"VIX elevated: {level}")
            elif level < 15:
                bias_score += 1
                reasons.append(f"VIX calm: {level}")
        
        # Gold (Risk-Off) Analysis
        gold = snapshot.get("gold", {})
        if "change" in gold:
            change = gold["change"]
            if change >= self.REGIME_THRESHOLDS["gold_flight"]:
                bias_score -= 2
                reasons.append(f"Gold surged {gold['change_pct']}% (risk-off)")
        
        # Crude Oil Analysis
        crude = snapshot.get("crude", {})
        if "change" in crude:
            change = abs(crude["change"])
            if change >= self.REGIME_THRESHOLDS["crude_shock"]:
                bias_score -= 1
                reasons.append(f"Crude oil shock: {crude['change_pct']}%")
        
        # USD/INR Analysis
        usdinr = snapshot.get("usdinr", {})
        if "change" in usdinr:
            change = usdinr["change"]
            if change >= 0.005:  # Dollar strengthening 0.5%+
                bias_score -= 1
                reasons.append(f"USD strengthening vs INR: {usdinr['change_pct']}%")
            elif change <= -0.005:  # INR strengthening
                bias_score += 1
                reasons.append(f"INR strengthening vs USD: {usdinr['change_pct']}%")
        
        # Determine final regime and bias
        if bias_score <= -4:
            regime = "RISK_OFF"
            bias = "VERY_CAUTIOUS"
        elif bias_score <= -2:
            regime = "CAUTIOUS"
            bias = "CAUTIOUS"
        elif bias_score >= 3:
            regime = "RISK_ON"
            bias = "BULLISH"
        elif bias_score >= 1:
            regime = "CONSTRUCTIVE"
            bias = "NEUTRAL_BULLISH"
        else:
            regime = "NEUTRAL"
            bias = "NEUTRAL"
        
        self.regime = regime
        self.bias = bias
        
        result = {
            "regime": regime,
            "bias": bias,
            "bias_score": bias_score,
            "reasons": reasons,
            "timestamp": datetime.now().isoformat(),
            "data": snapshot
        }
        
        logger.info("global_regime_determined", regime=regime, bias=bias, score=bias_score)
        return result
    
    def get_monday_bias(self) -> Dict:
        """
        Convenience method: Fetch data and return Monday trading bias.
        Call this Sunday night or early Monday morning.
        """
        self.fetch_global_snapshot()
        return self.determine_regime()

if __name__ == "__main__":
    print("Cross-Asset Macro Regime Detector")
    print("=" * 50)
    
    detector = CrossAssetMacroRegime()
    result = detector.get_monday_bias()
    
    print(f"Regime: {result['regime']}")
    print(f"Trading Bias: {result['bias']}")
    print(f"Bias Score: {result['bias_score']}")
    print("\nReasons:")
    for r in result["reasons"]:
        print(f"  - {r}")
    print("\nAsset Data:")
    for name, data in result.get("data", {}).items():
        if "error" not in data:
            print(f"  {name}: {data.get('current', 'N/A')} ({data.get('change_pct', 'N/A')}%)")
