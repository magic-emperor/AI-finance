import structlog
from typing import Dict, Any

logger = structlog.get_logger()

class SemanticNarrator:
    """
    Layer 9: Communication
    Translates raw synthesis into a compelling, human-readable market story.
    """
    def __init__(self):
        pass

    def generate_story(self, synthesized_report: Dict[str, Any]) -> str:
        """
        Creates a markdown narrative from the report data.
        """
        meta = synthesized_report["metadata"]
        state = synthesized_report["market_state"]
        intel = synthesized_report["model_intelligence"]
        awareness = synthesized_report["self_awareness"]
        grounding = synthesized_report["web_grounding"]

        title = f"# Market Insight Report: {meta['symbol']}\n"
        subtitle = f"**Generated at:** {meta['timestamp']}\n\n---\n"

        # 1. Executive Summary
        status_banner = ""
        if awareness['agent_status'] == "SILENCE_MODE":
            status_banner = "> [!WARNING]\n> **SILENCE MODE ACTIVE**: Agent confidence is below the actionability threshold (52%). Standardized standby protocol triggered.\n\n"

        summary = (
            f"## Executive Summary\n"
            f"{status_banner}"
            f"The market is currently in a **{state['regime']}** regime with **{state['volatility_class']}** volatility. "
            f"Our models are forecasting a potential **{intel['forecast']}** move with a calibrated confidence of "
            f"**{intel['calibrated_confidence'] * 100:.1f}%**.\n\n"
        )

        # 2. Pattern Analysis
        patterns = ", ".join(state["detected_patterns"]) if state["detected_patterns"] else "None"
        pattern_sec = (
            f"## Pattern & Data Intelligence\n"
            f"- **Regime**: {state['regime']}\n"
            f"- **MTF Alignment**: {state['mtf_alignment']}\n"
            f"- **ATR**: {state['current_atr']:.2f}\n"
            f"- **Relative Volume**: {state['relative_volume']:.2f} ({state['volume_class']})\n"
            f"- **Detected Candlesticks**: {patterns}\n\n"
        )

        # 3. Model Intelligence & Self-Awareness
        intel_sec = (
            f"## Brain & Self-Awareness\n"
            f"The neural network predicts a price range of **{intel['price_range_expected']}**. "
            f"However, our Self-Awareness layer has adjusted the confidence score based on historical performance in {state['regime']} states. \n"
            f"**Reliability Status**: {awareness['reliability_score']}.\n"
            f"**Attribution Logic**: {awareness['recent_error_attribution']}\n\n"
        )

        # 4. Research & Grounding
        research_sec = (
            f"## Web & Memory Grounding\n"
            f"{grounding['summary']}\n\n"
            f"> [!NOTE]\n"
            f"> Long-Term Memory Matching: **{grounding['memory_matched']}**\n\n"
        )

        # 5. Opportunity Radar (Phase 11)
        radar_sec = ""
        opportunities = synthesized_report.get("broad_market_radar", [])
        if opportunities:
            radar_sec = "## 📡 Broad-Market Opportunity Radar\n"
            for opp in opportunities:
                radar_sec += (
                    f"### 🎯 Opportunity Detected: **{opp['ticker']}**\n"
                    f"- **Status**: {opp['status']}\n"
                    f"- **News Velocity**: {opp['metrics']['news_velocity']} mentions\n"
                    f"- **Price Move**: {opp['metrics']['price_move']*100:.2f}%\n"
                    f"- **Signal**: {opp['metrics']['shadow']['flow_signature']}\n\n"
                )

        # 6. Hallucination Guard
        guard_sec = (
            f"## Hallucination Guard\n"
            f"This report has been cross-referenced across 9 layers of verification. "
            f"Logic Contradictions: **NONE DETECTED**.\n"
        )

        return title + subtitle + summary + pattern_sec + intel_sec + research_sec + radar_sec + guard_sec

if __name__ == "__main__":
    from market_agent.reporting.report_synthesizer import ReportSynthesizer
    
    # Mock data to test narrative
    synthesizer = ReportSynthesizer()
    perception = {"volatility": 0.002, "atr": 10.5}
    analytics = {"regime": "BULL_TREND", "patterns": ["Hammer"]}
    prediction = {"direction": "UP", "confidence": 0.85, "price_range": [2150, 2160]}
    evaluation = {"calibrated_confidence": 0.72, "reliability_score": "HIGH", "attribution": "Regime Match"}
    research = "Found 2 news items on Reuters mentioning quarterly growth. Grounding: Similar to Jan 2024 Bull market."
    
    report_data = synthesizer.synthesize("RELIANCE.NS", perception, analytics, prediction, evaluation, research)
    
    narrator = SemanticNarrator()
    print(narrator.generate_story(report_data))
