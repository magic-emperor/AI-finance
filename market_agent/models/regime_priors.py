import numpy as np
import structlog
from typing import Dict, Any

logger = structlog.get_logger()

class RegimeAwarePriors:
    """
    Layer 2: Statistical Context
    Manages Bayesian Priors for the Pareto distribution based on market regimes.
    Anchors are derived from 10+ years of sector benchmarks.
    """
    
    # Shape Parameter (Alpha/Xi): Heaviness of the tail
    # Higher = Thinner tail (Retail noise)
    # Lower = Fatter tail (Institutional moves/Crashes)
    REGIME_PRIORS = {
        "BULL": {"alpha": 2.5, "scale": 0.005},
        "BEAR": {"alpha": 1.8, "scale": 0.008},
        "CRASH": {"alpha": 1.2, "scale": 0.02},  # Extreme fat tails
        "WAITING_FOR_DATA": {"alpha": 2.2, "scale": 0.006}
    }

    @classmethod
    def get_prior(cls, regime: str, volatility_spike: bool = False) -> Dict[str, float]:
        """
        Returns the appropriate prior. 
        If a vol spike is detected, it automatically shifts to the 'BEAR' or 'CRASH' prior.
        """
        if volatility_spike:
            logger.warning("volatility_spike_detected", shifting_to="CRASH_PRIOR")
            return cls.REGIME_PRIORS["CRASH"]
            
        prior = cls.REGIME_PRIORS.get(regime, cls.REGIME_PRIORS["WAITING_FOR_DATA"])
        logger.info("prior_selected", regime=regime, **prior)
        return prior
