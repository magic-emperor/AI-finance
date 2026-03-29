"""
Phase 49: Holding Advisor Agent
Provides long-term investment recommendations based on:
- Fundamental analysis (Altman Z, Piotroski F)
- Shareholding patterns (FII/DII/Promoter changes)
- Corporate actions (dividends, M&A)
- News sentiment
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass, asdict
import structlog

from market_agent.data.scrapers import DataOrchestrator
from market_agent.models.fundamental_scorer import FundamentalScorer, HealthScores

logger = structlog.get_logger()


@dataclass
class HoldingRecommendation:
    """Complete holding recommendation with reasoning."""
    symbol: str
    recommendation: str  # 'strong_buy', 'buy', 'hold', 'sell', 'strong_sell'
    confidence: float  # 0.0 to 1.0
    target_holding_period: str  # '6_months', '1_year', '3_years', '5_years'
    
    # Scores
    holding_score: float
    altman_z: float
    piotroski_f: int
    
    # Key Metrics
    key_strengths: List[str]
    key_risks: List[str]
    
    # Reasoning
    ai_reasoning: str
    
    # Metadata
    generated_at: datetime = None
    
    def __post_init__(self):
        if self.generated_at is None:
            self.generated_at = datetime.now()
    
    def to_dict(self) -> Dict:
        d = asdict(self)
        d['generated_at'] = self.generated_at.isoformat()
        return d


class HoldingAdvisor:
    """
    AI-powered long-term investment advisor.
    Analyzes company fundamentals and provides holding recommendations.
    """
    
    def __init__(self, db_storage=None):
        self.orchestrator = DataOrchestrator(db_storage)
        self.scorer = FundamentalScorer()
        self.db = db_storage
    
    def analyze_for_holding(self, symbol: str) -> HoldingRecommendation:
        """
        Performs comprehensive analysis for long-term holding.
        
        Args:
            symbol: Stock symbol (e.g., 'RELIANCE.NS')
        
        Returns:
            HoldingRecommendation with full analysis
        """
        logger.info("holding_analysis_started", symbol=symbol)
        
        # 1. Fetch all fundamental data
        full_data = self.orchestrator.get_full_analysis(symbol)
        fundamentals = full_data.get('fundamentals', {})
        shareholding = full_data.get('shareholding', {})
        news = full_data.get('news', [])
        
        # Merge data for scoring
        merged_data = {**fundamentals, **shareholding}
        
        # 2. Calculate health scores
        scores = self.scorer.calculate_all_scores(merged_data)
        
        # 3. Identify strengths and risks
        strengths = self._identify_strengths(merged_data, scores)
        risks = self._identify_risks(merged_data, scores)
        
        # 4. Determine holding period
        holding_period = self._suggest_holding_period(scores)
        
        # 5. Calculate confidence
        confidence = self._calculate_confidence(merged_data, scores, len(news))
        
        # 6. Generate AI reasoning
        reasoning = self._generate_reasoning(symbol, scores, strengths, risks, news)
        
        recommendation = HoldingRecommendation(
            symbol=symbol,
            recommendation=scores.holding_recommendation,
            confidence=confidence,
            target_holding_period=holding_period,
            holding_score=scores.holding_score,
            altman_z=scores.altman_z,
            piotroski_f=scores.piotroski_f,
            key_strengths=strengths,
            key_risks=risks,
            ai_reasoning=reasoning,
        )
        
        logger.info("holding_analysis_complete", symbol=symbol, 
                    recommendation=scores.holding_recommendation,
                    score=scores.holding_score)
        
        return recommendation
    
    def compare_stocks(self, symbols: List[str]) -> List[HoldingRecommendation]:
        """Compares multiple stocks for portfolio construction."""
        recommendations = []
        for symbol in symbols:
            try:
                rec = self.analyze_for_holding(symbol)
                recommendations.append(rec)
            except Exception as e:
                logger.error("comparison_failed", symbol=symbol, error=str(e))
        
        # Sort by holding score (best first)
        recommendations.sort(key=lambda x: x.holding_score, reverse=True)
        return recommendations
    
    def _identify_strengths(self, data: Dict, scores: HealthScores) -> List[str]:
        """Identifies key investment strengths."""
        strengths = []
        
        if scores.altman_z >= 3.0:
            strengths.append("Strong financial stability (Altman Z > 3.0)")
        
        if scores.piotroski_f >= 7:
            strengths.append(f"High value score (Piotroski F: {scores.piotroski_f}/9)")
        
        promoter = data.get('promoter_holding', 0)
        if promoter >= 50:
            strengths.append(f"Strong promoter stake ({promoter:.1f}%)")
        
        fii = data.get('fii_holding', 0)
        if 15 <= fii <= 35:
            strengths.append(f"Healthy FII interest ({fii:.1f}%)")
        
        de_ratio = data.get('debt_to_equity', 1)
        if de_ratio <= 0.5:
            strengths.append(f"Low debt (D/E: {de_ratio:.2f})")
        
        revenue_growth = data.get('revenue_growth', 0)
        if revenue_growth >= 15:
            strengths.append(f"Strong revenue growth ({revenue_growth:.1f}%)")
        
        return strengths[:5]  # Top 5
    
    def _identify_risks(self, data: Dict, scores: HealthScores) -> List[str]:
        """Identifies key investment risks."""
        risks = []
        
        if scores.altman_z < 1.8:
            risks.append("High bankruptcy risk (Altman Z < 1.8)")
        
        if scores.piotroski_f <= 3:
            risks.append(f"Weak fundamentals (Piotroski F: {scores.piotroski_f}/9)")
        
        de_ratio = data.get('debt_to_equity', 1)
        if de_ratio > 1.5:
            risks.append(f"High debt burden (D/E: {de_ratio:.2f})")
        
        fii = data.get('fii_holding', 0)
        if fii < 5:
            risks.append("Low institutional interest (FII < 5%)")
        
        promoter = data.get('promoter_holding', 0)
        if promoter < 25:
            risks.append(f"Low promoter stake ({promoter:.1f}%)")
        
        pe = data.get('pe_ratio', 25)
        if pe > 50:
            risks.append(f"Expensive valuation (PE: {pe:.1f})")
        
        return risks[:5]  # Top 5
    
    def _suggest_holding_period(self, scores: HealthScores) -> str:
        """Suggests optimal holding period based on scores."""
        if scores.holding_score >= 75:
            return "3_years"  # Strong fundamentals = longer hold
        elif scores.holding_score >= 60:
            return "1_year"
        elif scores.holding_score >= 45:
            return "6_months"
        else:
            return "short_term"  # Consider exiting
    
    def _calculate_confidence(self, data: Dict, scores: HealthScores, 
                               news_count: int) -> float:
        """Calculates confidence in the recommendation."""
        confidence = 0.5  # Base
        
        # More data = higher confidence
        data_completeness = sum(1 for v in data.values() if v is not None) / max(len(data), 1)
        confidence += data_completeness * 0.2
        
        # Consistent scores = higher confidence
        if scores.altman_zone == "safe" and scores.piotroski_grade in ["strong", "moderate"]:
            confidence += 0.15
        
        # News coverage
        if news_count >= 5:
            confidence += 0.1
        
        return min(0.95, max(0.3, confidence))
    
    def _generate_reasoning(self, symbol: str, scores: HealthScores,
                            strengths: List[str], risks: List[str],
                            news: List[Dict]) -> str:
        """Generates natural language reasoning for the recommendation."""
        
        rec = scores.holding_recommendation.replace('_', ' ').title()
        
        reasoning = f"Based on comprehensive analysis, {symbol} receives a "
        reasoning += f"**{rec}** recommendation with a Holding Score of {scores.holding_score}/100.\n\n"
        
        # Financial health
        if scores.altman_zone == "safe":
            reasoning += "The company shows strong financial stability with low bankruptcy risk. "
        elif scores.altman_zone == "grey":
            reasoning += "Financial stability is moderate and should be monitored. "
        else:
            reasoning += "**Warning**: High financial distress risk detected. "
        
        # Value assessment
        if scores.piotroski_f >= 7:
            reasoning += f"Value investing metrics are excellent (Piotroski {scores.piotroski_f}/9). "
        elif scores.piotroski_f >= 5:
            reasoning += "Value metrics are satisfactory. "
        else:
            reasoning += "Value investing criteria are not well met. "
        
        # Strengths
        if strengths:
            reasoning += f"\n\n**Key Strengths**: {', '.join(strengths[:3])}"
        
        # Risks
        if risks:
            reasoning += f"\n\n**Key Risks**: {', '.join(risks[:3])}"
        
        # News sentiment
        if news:
            avg_sentiment = sum(n.get('sentiment', 0.5) for n in news) / len(news)
            if avg_sentiment >= 0.6:
                reasoning += "\n\nRecent news sentiment is **positive**."
            elif avg_sentiment <= 0.4:
                reasoning += "\n\nRecent news sentiment is **concerning**."
        
        return reasoning


# Quick test
if __name__ == "__main__":
    advisor = HoldingAdvisor()
    
    print("=== Holding Analysis: RELIANCE ===")
    rec = advisor.analyze_for_holding("RELIANCE.NS")
    
    print(f"\nRecommendation: {rec.recommendation.upper()}")
    print(f"Confidence: {rec.confidence:.0%}")
    print(f"Hold Period: {rec.target_holding_period}")
    print(f"Holding Score: {rec.holding_score}/100")
    print(f"Altman Z: {rec.altman_z}")
    print(f"Piotroski F: {rec.piotroski_f}/9")
    print(f"\nStrengths: {rec.key_strengths}")
    print(f"Risks: {rec.key_risks}")
    print(f"\nReasoning:\n{rec.ai_reasoning}")
