"""
News sentiment rules — Path A §11a Step 4 (optional).
When sentiment > X and RSI < Y, historical avg return = Z. Expose as guidance for RAG and optional "news brain".
"""

from typing import Dict, Any, Optional
import structlog

logger = structlog.get_logger()


def get_news_sentiment_guidance(
    symbol: str,
    current_sentiment: Optional[float] = None,
    current_rsi: Optional[float] = None,
    k: int = 10,
) -> str:
    """
    Recall past NEWS_SENTIMENT outcomes for symbol; aggregate by sentiment bucket and return guidance string.
    Used in RAG and optionally by a "news brain" signal.
    """
    try:
        from market_agent.brain.council_memory import get_council_memory
        mem = get_council_memory()
        similar = mem.recall_similar(
            query=f"{symbol} news sentiment outcome",
            memory_type="NEWS_SENTIMENT",
            k=k,
        )
        if not similar:
            return ""
        returns = [s.get("return_1d") for s in similar if s.get("return_1d") is not None]
        sentiments = [s.get("sentiment_score") for s in similar if s.get("sentiment_score") is not None]
        if not returns:
            return ""
        avg_return = sum(returns) / len(returns)
        avg_sent = sum(sentiments) / len(sentiments) if sentiments else 0
        n = len(returns)
        if current_sentiment is not None and current_rsi is not None:
            bucket = "positive" if current_sentiment > 0.15 else "negative" if current_sentiment < -0.15 else "neutral"
            return (
                f"NEWS SENTIMENT GUIDANCE ({symbol}): Based on {n} past similar sentiment→return cases, "
                f"avg next-day return was {avg_return:+.2f}%. Current sentiment bucket: {bucket}, RSI: {current_rsi:.0f}. "
                f"Use to inform caution or conviction; do not blindly follow."
            )
        return (
            f"NEWS SENTIMENT GUIDANCE ({symbol}): Based on {n} past cases, "
            f"avg next-day return after similar news sentiment was {avg_return:+.2f}% (avg sentiment {avg_sent:+.2f}). "
            f"Use to inform caution or conviction."
        )
    except Exception as e:
        logger.debug("news_sentiment_guidance_failed", symbol=symbol, error=str(e)[:60])
        return ""


def news_signal_from_guidance(symbol: str, current_price: float, atr: float) -> Optional[str]:
    """
    Optional 8th "news brain": map sentiment guidance to BUY/SELL/WAIT.
    Returns direction only if guidance is strong enough; else WAIT.
    """
    guidance = get_news_sentiment_guidance(symbol)
    if not guidance or "avg next-day return" not in guidance:
        return None
    try:
        from market_agent.brain.council_memory import get_council_memory
        mem = get_council_memory()
        similar = mem.recall_similar(
            query=f"{symbol} news sentiment",
            memory_type="NEWS_SENTIMENT",
            k=5,
        )
        if not similar:
            return None
        returns = [s.get("return_1d") for s in similar if s.get("return_1d") is not None]
        if not returns:
            return None
        avg = sum(returns) / len(returns)
        if avg > 0.3:
            return "BUY"
        if avg < -0.3:
            return "SELL"
        return "WAIT"
    except Exception:
        return None
