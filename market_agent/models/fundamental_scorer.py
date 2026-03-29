"""
Phase 48: Financial Health Scores
Calculates value investing metrics:
- Altman Z-Score: Bankruptcy prediction
- Piotroski F-Score: Value investing strength
- Holding Score: Custom composite score
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
import structlog

logger = structlog.get_logger()


@dataclass
class HealthScores:
    """Container for all financial health scores."""
    altman_z: float
    altman_zone: str  # 'safe', 'grey', 'distress'
    piotroski_f: int  # 0-9
    piotroski_grade: str  # 'strong', 'moderate', 'weak'
    holding_score: float  # 0-100 composite
    holding_recommendation: str  # 'strong_buy', 'buy', 'hold', 'sell', 'strong_sell'


class FundamentalScorer:
    """
    Calculates financial health scores for holding analysis.
    """
    
    def calculate_all_scores(self, data: Dict[str, Any]) -> HealthScores:
        """
        Calculates all health scores from fundamental data.
        
        Args:
            data: Dict containing fundamental data (from Screener/yfinance)
        
        Returns:
            HealthScores dataclass with all metrics
        """
        altman_z = self.calculate_altman_z(data)
        piotroski_f = self.calculate_piotroski_f(data)
        holding_score = self.calculate_holding_score(data, altman_z, piotroski_f)
        
        return HealthScores(
            altman_z=altman_z,
            altman_zone=self._get_altman_zone(altman_z),
            piotroski_f=piotroski_f,
            piotroski_grade=self._get_piotroski_grade(piotroski_f),
            holding_score=holding_score,
            holding_recommendation=self._get_holding_recommendation(holding_score),
        )
    
    def calculate_altman_z(self, data: Dict[str, Any]) -> float:
        """
        Altman Z-Score: Predicts bankruptcy risk.
        
        Formula (for manufacturing):
        Z = 1.2*A + 1.4*B + 3.3*C + 0.6*D + 1.0*E
        
        Where:
        A = Working Capital / Total Assets
        B = Retained Earnings / Total Assets
        C = EBIT / Total Assets
        D = Market Value of Equity / Total Liabilities
        E = Sales / Total Assets
        
        Interpretation:
        Z > 2.99: Safe zone
        1.81 < Z < 2.99: Grey zone
        Z < 1.81: Distress zone
        """
        try:
            # Get required values with safe defaults
            working_capital = self._safe_get(data, 'working_capital', 0)
            total_assets = self._safe_get(data, 'total_assets', 1)  # Avoid div by 0
            retained_earnings = self._safe_get(data, 'retained_earnings', 0)
            ebit = self._safe_get(data, 'operating_profit', 0)
            market_cap = self._safe_get(data, 'market_cap', 0)
            total_liabilities = self._safe_get(data, 'total_debt', 1)
            revenue = self._safe_get(data, 'revenue', 0)
            
            # Calculate ratios
            A = working_capital / total_assets if total_assets else 0
            B = retained_earnings / total_assets if total_assets else 0
            C = ebit / total_assets if total_assets else 0
            D = market_cap / total_liabilities if total_liabilities else 0
            E = revenue / total_assets if total_assets else 0
            
            z_score = (1.2 * A) + (1.4 * B) + (3.3 * C) + (0.6 * D) + (1.0 * E)
            
            logger.debug("altman_z_calculated", z_score=z_score)
            return round(z_score, 2)
            
        except Exception as e:
            logger.error("altman_z_failed", error=str(e))
            return 0.0
    
    def calculate_piotroski_f(self, data: Dict[str, Any]) -> int:
        """
        Piotroski F-Score: Value investing strength (0-9).
        
        Profitability (4 points):
        1. ROA > 0 → 1 point
        2. Operating Cash Flow > 0 → 1 point
        3. ROA increasing YoY → 1 point
        4. Cash Flow > Net Income (quality of earnings) → 1 point
        
        Leverage/Liquidity (3 points):
        5. Debt ratio decreasing → 1 point
        6. Current ratio increasing → 1 point
        7. No new shares issued → 1 point
        
        Operating Efficiency (2 points):
        8. Gross margin increasing → 1 point
        9. Asset turnover increasing → 1 point
        
        Interpretation:
        8-9: Strong value stock (buy)
        5-7: Moderate
        0-4: Weak (avoid)
        """
        score = 0
        
        try:
            # Profitability
            roa = self._safe_get(data, 'roa', 0)
            if roa > 0:
                score += 1
            
            operating_cf = self._safe_get(data, 'operating_cash_flow', 0)
            if operating_cf > 0:
                score += 1
            
            roa_prev = self._safe_get(data, 'roa_prev', roa - 0.01)
            if roa > roa_prev:
                score += 1
            
            net_income = self._safe_get(data, 'net_profit', 0)
            if operating_cf > net_income:
                score += 1  # Quality of earnings
            
            # Leverage/Liquidity
            debt_ratio = self._safe_get(data, 'debt_to_equity', 1)
            debt_ratio_prev = self._safe_get(data, 'debt_to_equity_prev', debt_ratio + 0.1)
            if debt_ratio < debt_ratio_prev:
                score += 1
            
            current_ratio = self._safe_get(data, 'current_ratio', 1)
            current_ratio_prev = self._safe_get(data, 'current_ratio_prev', current_ratio - 0.1)
            if current_ratio > current_ratio_prev:
                score += 1
            
            shares_outstanding = self._safe_get(data, 'shares_outstanding', 100)
            shares_prev = self._safe_get(data, 'shares_outstanding_prev', shares_outstanding)
            if shares_outstanding <= shares_prev:
                score += 1  # No dilution
            
            # Operating Efficiency
            gross_margin = self._safe_get(data, 'gross_margin', 0)
            gross_margin_prev = self._safe_get(data, 'gross_margin_prev', gross_margin - 0.01)
            if gross_margin > gross_margin_prev:
                score += 1
            
            asset_turnover = self._safe_get(data, 'asset_turnover', 0)
            asset_turnover_prev = self._safe_get(data, 'asset_turnover_prev', asset_turnover - 0.01)
            if asset_turnover > asset_turnover_prev:
                score += 1
            
            logger.debug("piotroski_f_calculated", score=score)
            return score
            
        except Exception as e:
            logger.error("piotroski_f_failed", error=str(e))
            return 0
    
    def calculate_holding_score(self, data: Dict[str, Any], 
                                 altman_z: float, piotroski_f: int) -> float:
        """
        Custom composite Holding Score (0-100).
        
        Weights:
        - Altman Z: 20%
        - Piotroski F: 20%
        - Promoter Holding: 15%
        - FII Interest: 10%
        - Revenue Growth: 15%
        - Debt Health: 10%
        - Valuation (PE): 10%
        """
        score = 0.0
        
        # Altman Z (20 points max)
        if altman_z >= 3.0:
            score += 20
        elif altman_z >= 2.0:
            score += 15
        elif altman_z >= 1.5:
            score += 10
        else:
            score += 0
        
        # Piotroski F (20 points max)
        score += (piotroski_f / 9) * 20
        
        # Promoter Holding (15 points max) - Higher is better
        promoter = self._safe_get(data, 'promoter_holding', 0)
        if promoter >= 50:
            score += 15
        elif promoter >= 40:
            score += 12
        elif promoter >= 30:
            score += 8
        else:
            score += 4
        
        # FII Interest (10 points max) - Moderate is good
        fii = self._safe_get(data, 'fii_holding', 0)
        if 10 <= fii <= 30:
            score += 10
        elif 5 <= fii < 10 or 30 < fii <= 40:
            score += 7
        else:
            score += 4
        
        # Revenue Growth (15 points max)
        revenue_growth = self._safe_get(data, 'revenue_growth', 0)
        if revenue_growth >= 20:
            score += 15
        elif revenue_growth >= 10:
            score += 12
        elif revenue_growth >= 5:
            score += 8
        elif revenue_growth >= 0:
            score += 5
        else:
            score += 0
        
        # Debt Health (10 points max) - Lower debt-to-equity is better
        de_ratio = self._safe_get(data, 'debt_to_equity', 1)
        if de_ratio <= 0.3:
            score += 10
        elif de_ratio <= 0.5:
            score += 8
        elif de_ratio <= 1.0:
            score += 5
        else:
            score += 2
        
        # Valuation PE (10 points max) - Moderate PE is good
        pe = self._safe_get(data, 'pe_ratio', 25)
        if 10 <= pe <= 20:
            score += 10
        elif 8 <= pe < 10 or 20 < pe <= 30:
            score += 7
        elif pe < 8 or pe > 30:
            score += 4
        
        return round(min(100, max(0, score)), 1)
    
    def _safe_get(self, data: Dict, key: str, default: float = 0) -> float:
        """Safely extracts a numeric value from data."""
        value = data.get(key, default)
        if value is None:
            return default
        try:
            return float(value)
        except (ValueError, TypeError):
            return default
    
    def _get_altman_zone(self, z: float) -> str:
        """Returns the Altman Z zone description."""
        if z >= 2.99:
            return "safe"
        elif z >= 1.81:
            return "grey"
        else:
            return "distress"
    
    def _get_piotroski_grade(self, f: int) -> str:
        """Returns the Piotroski F grade."""
        if f >= 8:
            return "strong"
        elif f >= 5:
            return "moderate"
        else:
            return "weak"
    
    def _get_holding_recommendation(self, score: float) -> str:
        """Returns holding recommendation based on composite score."""
        if score >= 80:
            return "strong_buy"
        elif score >= 65:
            return "buy"
        elif score >= 45:
            return "hold"
        elif score >= 30:
            return "sell"
        else:
            return "strong_sell"


# Quick test
if __name__ == "__main__":
    scorer = FundamentalScorer()
    
    # Sample data
    test_data = {
        'market_cap': 1800000000000,  # 18 Lakh Cr
        'total_assets': 1500000000000,
        'total_debt': 200000000000,
        'working_capital': 50000000000,
        'retained_earnings': 300000000000,
        'operating_profit': 150000000000,
        'revenue': 800000000000,
        'net_profit': 100000000000,
        'roa': 0.08,
        'operating_cash_flow': 120000000000,
        'current_ratio': 1.5,
        'debt_to_equity': 0.4,
        'promoter_holding': 55,
        'fii_holding': 18,
        'revenue_growth': 12,
        'pe_ratio': 22,
    }
    
    scores = scorer.calculate_all_scores(test_data)
    print(f"Altman Z-Score: {scores.altman_z} ({scores.altman_zone})")
    print(f"Piotroski F-Score: {scores.piotroski_f}/9 ({scores.piotroski_grade})")
    print(f"Holding Score: {scores.holding_score}/100")
    print(f"Recommendation: {scores.holding_recommendation.upper()}")
