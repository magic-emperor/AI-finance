import structlog
import numpy as np
import datetime
from typing import Dict, List, Any

logger = structlog.get_logger()

class CuriosityEngine:
    """
    Layer 6: Curiosity (Research Triggers)
    Identifies 'Surprises' and 'Contradictions' that require external research.
    """
    def __init__(self, surprise_threshold=0.15, ood_threshold=3.0):
        self.surprise_threshold = surprise_threshold
        self.ood_threshold = ood_threshold
        self.research_queue = []

    def check_for_surprise(self, prediction: Dict[str, Any], reality: Dict[str, Any]):
        """
        Triggers curiosity if reality deviates significantly from prediction.
        """
        error = reality.get("error_magnitude", 0)
        # Flip/Refine Logic: If we were WRONG or if our confidence was suspiciously low
        if error > self.surprise_threshold:
            self._trigger_research(
                "High Surprise",
                f"Model predicted {prediction.get('direction')} with confidence {prediction.get('confidence')}, "
                f"but actual move was {reality.get('outcome')}. Error magnitude: {error:.2f}"
            )

    def check_logic_gap(self, prediction: Dict[str, Any], analytics: Dict[str, Any]):
        """
        NEW: Triggers research if the agent is uncertain (Low Confidence).
        """
        conf = prediction.get("confidence", 1.0)
        if conf < 0.52:
            self._trigger_research(
                "Low Confidence Blindspot",
                f"Agent confidence is {conf:.2f} (Below edge threshold of 0.52). "
                f"Entering Research Mode to find missing context."
            )

    def check_for_contradiction(self, nn_output: Dict[str, Any], rule_output: Dict[str, Any]):
        """
        Triggers curiosity if the Neural Network disagrees with Deterministic Rules.
        """
        nn_dir = nn_output.get("direction")
        rule_regime = rule_output.get("regime")
        
        # Simple contradiction: NN says UP but Trend/Regime is Strong BEAR or vice-versa
        if nn_dir == "UP" and rule_regime == "BEAR_TREND":
            self._trigger_research(
                "Logic Contradiction",
                f"Neural Network predicts UP, but Deterministic Rules identify a BEAR_TREND. Investigation required."
            )
        elif nn_dir == "DOWN" and rule_regime == "BULL_TREND":
            self._trigger_research(
                "Logic Contradiction",
                f"Neural Network predicts DOWN, but Deterministic Rules identify a BULL_TREND. Investigation required."
            )

    def check_ood(self, symbol: str, current_metrics: Dict[str, Any]):
        """
        Out-of-Distribution Detection.
        Checks if volatility or volume is anomalous (Black Swan detection).
        """
        vol = current_metrics.get("volatility", 0)
        # Assuming we have a way to get 'normal' distributions (Z-score logic)
        # Simplified: Check if current vol is 3x avg vol
        # if vol > threshold: trigger
        pass

    def _trigger_research(self, reason: str, context: str):
        task = {
            "id": f"REQ_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "reason": reason,
            "context": context,
            "timestamp": datetime.datetime.now().isoformat(),
            "status": "PENDING"
        }
        self.research_queue.append(task)
        logger.warning("curiosity_triggered", reason=reason, task_id=task["id"])
        return task

    def get_pending_tasks(self):
        return [t for t in self.research_queue if t["status"] == "PENDING"]

if __name__ == "__main__":
    engine = CuriosityEngine()
    
    # Test 1: Contradiction
    engine.check_for_contradiction(
        {"direction": "UP", "confidence": 0.8},
        {"regime": "BEAR_TREND"}
    )
    
    # Test 2: Surprise
    engine.check_for_surprise(
        {"direction": "UP", "confidence": 0.9},
        {"outcome": "COLLAPSE", "error_magnitude": 0.45}
    )
    
    print(f"Pending Research Tasks: {len(engine.get_pending_tasks())}")
    for t in engine.get_pending_tasks():
        print(f" - [{t['reason']}]: {t['context']}")
