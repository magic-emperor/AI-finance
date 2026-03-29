import structlog
from datetime import datetime
from typing import Dict, Any, List

logger = structlog.get_logger()

class ReportSynthesizer:
    """
    Layer 9: Communication
    Collates all agent internal states into a structured report.
    """
    def __init__(self):
        pass

    def synthesize(self, 
                  symbol: str,
                  perception: Dict[str, Any],
                  analytics: Dict[str, Any],
                  prediction: Dict[str, Any],
                  evaluation: Dict[str, Any],
                  research: str,
                  opportunities: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Gathers all the raw data for the narrator to use.
        """
        report = {
            "metadata": {
                "symbol": symbol,
                "timestamp": datetime.now().isoformat(),
                "agent_version": "1.0.0-Stable"
            },
            "market_state": {
                "regime": analytics.get("regime"),
                "volatility_class": self._classify_volatility(perception.get("volatility", 0)),
                "current_atr": perception.get("atr"),
                "detected_patterns": analytics.get("active_patterns", []),
                "mtf_alignment": analytics.get("mtf_alignment"),
                "relative_volume": perception.get("relative_volume"),
                "volume_class": perception.get("volume_class")
            },
            "model_intelligence": {
                "forecast": prediction.get("direction"),
                "raw_confidence": prediction.get("confidence"),
                "calibrated_confidence": evaluation.get("calibrated_confidence", prediction.get("confidence")),
                "price_range_expected": prediction.get("price_range")
            },
            "self_awareness": {
                "reliability_score": evaluation.get("reliability_score", "UNKNOWN"),
                "recent_error_attribution": evaluation.get("attribution", "NONE"),
                "agent_status": evaluation.get("status", "ACTIVE")
            },
            "web_grounding": {
                "summary": research if research else "Researching deeper context due to low confidence..." if evaluation.get("status") == "SILENCE_MODE" else "No research triggered",
                "memory_matched": "YES" if "Grounding" in (research or "") else "NO"
            },
            "broad_market_radar": opportunities or []
        }
        
        logger.info("report_synthesized", symbol=symbol)
        return report

    def _classify_volatility(self, vol: float) -> str:
        if vol < 0.001: return "LOW"
        if vol < 0.003: return "NORMAL"
        return "HIGH/STRESS"

if __name__ == "__main__":
    synthesizer = ReportSynthesizer()
    
    # Mock data from all layers
    perception = {"volatility": 0.002, "atr": 10.5}
    analytics = {"regime": "BULL_TREND", "patterns": ["Hammer"]}
    prediction = {"direction": "UP", "confidence": 0.85, "price_range": [2150, 2160]}
    evaluation = {"calibrated_confidence": 0.72, "reliability_score": "HIGH", "attribution": "Regime Match"}
    research = "Found 2 news items on Reuters. Grounding: Similar to Jan 2024 event."
    
    final_report = synthesizer.synthesize("RELIANCE.NS", perception, analytics, prediction, evaluation, research)
    import json
    print(json.dumps(final_report, indent=2))
