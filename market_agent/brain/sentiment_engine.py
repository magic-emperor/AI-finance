"""
Phase 4 Step 4.9 — Sentiment Engine
Multi-layer, multi-timeframe sentiment scoring.

Produces three distinct sentiment signals:
  1. Immediate  — news from last 2h   (intraday relevance)
  2. Daily      — news from last 24h  (swing/daily relevance)
  3. Structural — analyst targets / earnings (macro context)

Usage:
    from market_agent.brain.sentiment_engine import SentimentEngine
    engine = SentimentEngine(gemini_client)
    result = engine.score('ITC.NS', strategy_mode='Intraday (Scalp)')
"""
import json
import re
import structlog
from typing import Optional

logger = structlog.get_logger()


class SentimentEngine:
    """
    Correct multi-layer sentiment scoring per Phase 2 Guide Part C.

    Replaces the old flat sentiment_score → base_bias * 0.25 approach.
    Each layer has different weights depending on the strategy timeframe.

    Return dict schema:
    {
        'composite':  float  # -1.0 to +1.0, weighted by strategy_mode
        'immediate':  float  # last 2h news
        'daily':      float  # last 24h news
        'structural': float  # analyst/earnings context
        'confidence': float  # 0-1, higher when all layers agree
        'sources_used': list[str]
        'direction':  'BUY'|'SELL'|'HOLD'
        'evidence':   str    # one-line summary for council debate
    }
    """

    def __init__(self, gemini_client=None, news_fetcher=None):
        """
        gemini_client : can call generate(prompt) -> str
        news_fetcher  : optional injected callable(symbol, hours) -> list[str]
                        defaults to the project's existing RSS scrapers
        """
        self.gemini       = gemini_client
        self.news_fetcher = news_fetcher
        self._last_sources: list = []

    def score(self, symbol: str, strategy_mode: str = 'Intraday (Scalp)') -> dict:
        """
        Returns composite and per-layer sentiment scores.
        strategy_mode: 'Intraday (Scalp)' or 'Swing'
        """
        immediate  = self._score_recent_news(symbol, hours=2)
        daily      = self._score_recent_news(symbol, hours=24)
        structural = self._score_structural(symbol)

        # Weighting per Phase 2 Guide
        if strategy_mode == 'Intraday (Scalp)':
            composite = immediate * 0.60 + daily * 0.30 + structural * 0.10
        else:
            composite = immediate * 0.15 + daily * 0.35 + structural * 0.50

        confidence = self._score_confidence(immediate, daily, structural)

        # Derive direction from composite
        if composite > 0.25:
            direction = 'BUY'
        elif composite < -0.25:
            direction = 'SELL'
        else:
            direction = 'HOLD'

        evidence = (
            f'Sentiment composite={composite:+.2f} | '
            f'immediate={immediate:+.2f} | '
            f'daily={daily:+.2f} | '
            f'structural={structural:+.2f} | '
            f'confidence={confidence:.0%}'
        )

        return {
            'composite':    round(composite, 3),
            'immediate':    round(immediate, 3),
            'daily':        round(daily, 3),
            'structural':   round(structural, 3),
            'confidence':   round(confidence, 3),
            'sources_used': self._last_sources[:],
            'direction':    direction,
            'evidence':     evidence,
        }

    # ── Private helpers ──────────────────────────────────────────────────────

    def _fetch_headlines(self, symbol: str, hours: int) -> list:
        """Gather headlines from available scrapers."""
        headlines = []
        self._last_sources = []

        # Use injected fetcher if provided (from existing news_aggregator)
        if self.news_fetcher:
            try:
                headlines.extend(self.news_fetcher(symbol, hours) or [])
                self._last_sources.append('injected_fetcher')
            except Exception:
                pass

        # Fallback: try existing project scrapers
        if not headlines:
            try:
                from market_agent.data.ingestion.news_aggregator import fetch_headlines
                fetched = fetch_headlines(symbol, max_age_hours=hours)
                if fetched:
                    headlines.extend(fetched)
                    self._last_sources.append('news_aggregator')
            except Exception:
                pass

        return headlines

    def _score_recent_news(self, symbol: str, hours: int) -> float:
        """
        Score headlines with Gemini. Returns -1.0 to +1.0.
        Falls back to 0.0 if Gemini unavailable or no headlines.
        """
        headlines = self._fetch_headlines(symbol, hours)
        if not headlines:
            return 0.0
        if not self.gemini:
            return 0.0

        prompt = f"""Score these {symbol} headlines for trading sentiment.
Return ONLY JSON: {{"score": -0.8, "reason": "one sentence"}}
score range: -1.0 (very bearish) to +1.0 (very bullish), 0.0 = neutral
Headlines: {headlines[:5]}"""

        try:
            response = self.gemini.generate(prompt)
            m = re.search(r'\{.*?\}', response, re.DOTALL)
            if m:
                result = json.loads(m.group())
                score  = float(result.get('score', 0.0))
                score  = max(-1.0, min(1.0, score))
                logger.debug(
                    'sentiment_scored',
                    symbol=symbol, hours=hours,
                    score=score, reason=result.get('reason', '')[:60]
                )
                return score
        except Exception as e:
            logger.debug('sentiment_gemini_failed', symbol=symbol, hours=hours, error=str(e)[:60])

        return 0.0

    def _score_structural(self, symbol: str) -> float:
        """
        Structural sentiment: analyst price targets vs current price.
        Returns +ve if consensus target is above current price, -ve if below.
        Falls back to 0.0 if data unavailable.
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            info   = ticker.info or {}
            target = info.get('targetMeanPrice')
            current = info.get('currentPrice') or info.get('regularMarketPrice')

            if target and current and current > 0:
                upside = (target - current) / current
                # Normalise: ±20% upside maps to ±1.0 score
                score  = max(-1.0, min(1.0, upside / 0.20))
                self._last_sources.append('yfinance_analyst')
                return round(score, 3)
        except Exception:
            pass
        return 0.0

    def _score_confidence(self, immediate: float, daily: float, structural: float) -> float:
        """Higher confidence when all layers agree in direction."""
        values = [immediate, daily, structural]
        signs  = [1 if v > 0.1 else (-1 if v < -0.1 else 0) for v in values]
        all_agree     = len(set(signs)) == 1
        avg_magnitude = sum(abs(v) for v in values) / 3
        base          = avg_magnitude * 0.7
        bonus         = 0.30 if all_agree else 0.0
        return round(min(1.0, base + bonus), 3)
