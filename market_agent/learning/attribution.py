"""
Phase 3: AI-Driven Failure Attribution

Replaces hardcoded if/else strings with REAL analysis:
- FAISS recall of similar past failures
- AI-generated failure explanations
- Data-only fallback with actual metrics (no templates)
"""

import structlog
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = structlog.get_logger()


class AttributionEngine:
    """
    Analyzes WHY a prediction failed using:
    1. FAISS recall of similar past failures
    2. AI-generated analysis
    3. Actual market data comparison
    """
    
    def __init__(self):
        self._council_memory = None
        self._gemini = None
    
    @property
    def council_memory(self):
        if self._council_memory is None:
            try:
                from market_agent.brain.council_memory import get_council_memory
                self._council_memory = get_council_memory()
            except Exception:
                pass
        return self._council_memory
    
    @property
    def gemini(self):
        if self._gemini is None:
            try:
                from market_agent.brain.gemini_client import gemini_client
                self._gemini = gemini_client
            except Exception:
                pass
        return self._gemini
    
    def attribute_failure(self, prediction: Dict[str, Any],
                           outcome: Dict[str, Any]) -> str:
        """
        AI-powered failure attribution.
        
        Args:
            prediction: {direction, regime, confidence, model_id, symbol, ...}
            outcome: {direction, actual_price, ...}
        
        Returns: Human-readable explanation of what went wrong.
        """
        pred_dir = prediction.get("direction", "UNKNOWN")
        actual_dir = outcome.get("direction", "UNKNOWN")
        regime = prediction.get("regime", "UNKNOWN")
        model = prediction.get("model_id", "Unknown Model")
        symbol = prediction.get("symbol", "Unknown")
        confidence = prediction.get("confidence", 0)
        
        # Success case
        if pred_dir == actual_dir:
            return f"SUCCESS: {model} correctly predicted {actual_dir} in {regime} regime."
        
        # 1. FAISS recall: find similar past failures
        similar_context = ""
        if self.council_memory:
            try:
                similar = self.council_memory.recall_similar(
                    query=f"{model} predicted {pred_dir} but actual was {actual_dir} in {regime}",
                    memory_type="TRADE_LOSS", k=3
                )
                if similar:
                    similar_context = "Similar past failures: " + "; ".join(
                        s.get("text", "")[:120] for s in similar
                    )
            except Exception:
                pass
        
        # 2. Try AI analysis
        if self.gemini and self.gemini.is_available:
            try:
                prompt = (
                    f"Failure analysis for {model} on {symbol}:\n"
                    f"- Predicted: {pred_dir} with {confidence:.0%} confidence\n"
                    f"- Actual: {actual_dir}\n"
                    f"- Regime: {regime}\n"
                    f"{similar_context}\n"
                    f"In 2-3 sentences, explain why the prediction failed "
                    f"and what data the model likely missed."
                )
                ai_result = self.gemini._call_ai(
                    prompt,
                    f"attribution_{model}_{symbol}"
                )
                if ai_result:
                    # Store this failure in FAISS for future recall
                    self._store_failure(model, symbol, pred_dir, actual_dir,
                                         regime, ai_result)
                    return ai_result
            except Exception:
                pass
        
        # 3. Data-only fallback (no hardcoded interpretation)
        fallback = (
            f"FAILURE: {model} predicted {pred_dir} but actual was {actual_dir}. "
            f"Regime: {regime}. Confidence: {confidence:.0%}. Symbol: {symbol}."
        )
        if similar_context:
            fallback += f" {similar_context}"
        
        self._store_failure(model, symbol, pred_dir, actual_dir, regime, fallback)
        return fallback

    def get_recent_attributions(self, symbol: str = None, limit: int = 5) -> List[Dict[str, Any]]:
        """
        Fetch recent failure attributions from FAISS (TRADE_LOSS memories).
        Returns list of { failure_type, analysis, date, brain } for UI display.
        """
        if not self.council_memory:
            return []
        try:
            query = f"{symbol or 'failure'} prediction miss" if symbol else "prediction failure attribution"
            memories = self.council_memory.recall_similar(
                query=query, k=limit, memory_type="TRADE_LOSS", symbol=symbol
            )
            out = []
            for m in memories:
                out.append({
                    "failure_type": "PREDICTION_MISS",
                    "analysis": m.get("text", m.get("attribution", ""))[:400],
                    "date": m.get("timestamp", "N/A"),
                    "brain": m.get("brain", m.get("model_id", "N/A")),
                })
            return out
        except Exception:
            return []
    
    def batch_attribute(self, failures: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Batch attribute multiple failures. Returns list with attributions added.
        """
        results = []
        for failure in failures:
            prediction = failure.get("prediction", failure)
            outcome = failure.get("outcome", {})
            
            attribution = self.attribute_failure(prediction, outcome)
            result = {**failure, "attribution": attribution}
            results.append(result)
        
        return results
    
    def _store_failure(self, model: str, symbol: str, predicted: str,
                        actual: str, regime: str, analysis: str):
        """Store failure in FAISS for future recall."""
        if not self.council_memory:
            return
        
        try:
            self.council_memory.store(
                text=(
                    f"{model} failed on {symbol}: predicted {predicted}, "
                    f"actual {actual} in {regime}. Analysis: {analysis[:200]}"
                ),
                memory_type="TRADE_LOSS",
                metadata={
                    "brain": model, "symbol": symbol,
                    "predicted": predicted, "actual": actual,
                    "regime": regime
                }
            )
        except Exception:
            pass


# Singleton
_engine = None

def get_attribution_engine() -> AttributionEngine:
    global _engine
    if _engine is None:
        _engine = AttributionEngine()
    return _engine


if __name__ == "__main__":
    engine = AttributionEngine()
    result = engine.attribute_failure(
        {"direction": "UP", "regime": "BULL_TREND", "model_id": "AMV-LSTM",
         "symbol": "RELIANCE.NS", "confidence": 0.8},
        {"direction": "DOWN"}
    )
    print(result)
