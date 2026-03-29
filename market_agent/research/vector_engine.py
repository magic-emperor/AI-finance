from sentence_transformers import SentenceTransformer
import torch
import numpy as np
import structlog
from typing import List, Union

logger = structlog.get_logger()

class VectorEngine:
    """
    Layer 8: Long-Term Memory
    Handles the conversion of text and market states into vector embeddings.
    Uses 'all-MiniLM-L6-v2' (384 dimensions) for high speed and low memory usage.
    """
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = SentenceTransformer(model_name).to(self.device)
        logger.info("vector_engine_initialized", model=model_name, device=self.device)

    def encode(self, text: Union[str, List[str]]) -> np.ndarray:
        """
        Converts text into a vector embedding.
        """
        if isinstance(text, str):
            text = [text]
            
        embeddings = self.model.encode(text, convert_to_numpy=True)
        return embeddings

    def encode_market_state(self, symbol: str, regime: str, metrics: dict) -> np.ndarray:
        """
        Converts a complex market state into a searchable string, then embeds it.
        Includes MTF and Volume context for high-precision grounding.
        """
        state_str = (
            f"Symbol: {symbol} | Regime: {regime} | "
            f"MTF Alignment: {metrics.get('mtf_alignment', 'NEUTRAL')} | "
            f"Rel Volume: {metrics.get('relative_volume', 1.0):.2f} | "
            f"Volatility: {metrics.get('volatility', 0):.5f} | "
            f"ATR: {metrics.get('atr', 0):.2f}"
        )
        return self.encode(state_str)[0]

if __name__ == "__main__":
    import numpy as np
    engine = VectorEngine()
    
    # Test text encoding
    vec = engine.encode("Global markets are crashing due to inflation.")
    print(f"Vector Shape: {vec.shape}")
    
    # Test market state encoding
    market_vec = engine.encode_market_state("RELIANCE.NS", "BULL_TREND", {"volatility": 0.001, "atr": 10.5})
    print(f"Market State Vector Shape: {market_vec.shape}")
