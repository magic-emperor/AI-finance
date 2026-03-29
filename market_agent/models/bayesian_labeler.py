import numpy as np
import structlog
from scipy.stats import pareto
from typing import Dict, Any
from market_agent.models.regime_priors import RegimeAwarePriors

logger = structlog.get_logger()

class BayesianLabeler:
    """
    Layer 5: Statistical Labeling
    Uses Bayesian Pareto models to label moves as 'SIGNAL' vs 'NOISE'.
    Protects against overfitting on small datasets by anchoring to historical priors.
    """
    
    def __init__(self):
        self.priors = RegimeAwarePriors()

    def label_move(self, returns: float, regime: str, volatility_spike: bool = False) -> str:
        """
        Labels a move based on its probability under the current regime's Pareto prior.
        """
        prior = self.priors.get_prior(regime, volatility_spike)
        alpha = prior["alpha"]
        scale = prior["scale"]
        
        abs_return = abs(returns)
        
        # If move is below the scale (minimum interesting move), it's noise
        if abs_return < scale:
            return "NOISE"
            
        # Calculate survival function (probability of seeing a move this size or larger)
        # 1 - CDF
        prob_larger = (scale / abs_return) ** alpha
        
        # Institutional Moves are typically in the top 5-10% of the distribution
        if prob_larger < 0.10:
            label = "INSTITUTIONAL_SIGNAL"
        elif prob_larger < 0.25:
            label = "MOMENTUM_SIGNAL"
        else:
            label = "NOISE"
            
        logger.debug("move_labeled", 
                     returns=returns, 
                     prob_larger=f"{prob_larger:.4f}", 
                     label=label)
        return label

if __name__ == "__main__":
    labeler = BayesianLabeler()
    # Test a 1.5% move in a Bull market
    print(f"1.5% in Bull: {labeler.label_move(0.015, 'BULL')}")
    # Test a 1.5% move in a Crash regime (should be common noise)
    print(f"1.5% in Crash: {labeler.label_move(0.015, 'CRASH')}")
    # Test a 5% move
    print(f"5% move: {labeler.label_move(0.05, 'BULL')}")
