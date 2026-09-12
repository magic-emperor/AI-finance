# ============================================================
# DEPRECATED - THIS FILE IS NO LONGER ACTIVE
#
# autonomous_analyst.py was a standalone analyst that ran
# news + technical + Gemini pre-analysis. It was never wired
# into the main signal generation pipeline (cortex.py uses
# Gemini directly via grand_council, not via this module).
#
# The news + sentiment function is handled by:
#   - market_agent/brain/sentiment_engine.py (sentiment scoring)
#   - cortex.py's grand_council (Gemini analysis)
#
# Kept for reference. DO NOT import this file into live trading.
# ============================================================
pass


# import structlog
# from typing import Dict, List, Any, Optional
# from datetime import datetime
# import time

# logger = structlog.get_logger()

# class AutonomousAnalyst:
#     """
#     The 'Mind' of the system. 
#     Performs Pre-Analysis using real technical indicators + Gemini AI.
#     Produces dynamic, data-driven conclusions — never hardcoded strings.
#     """
#     def __init__(self, news_aggregator=None, storage=None):
#         from market_agent.research.news_aggregator import news_aggregator as default_agg
#         self.news_aggregator = news_aggregator or default_agg
#         try:
#             from market_agent.data.storage.postgres import PostgresStorage
#             self.storage = storage or PostgresStorage()
#         except Exception:
#             self.storage = None
        
#         # AI client (rate-limited, cached)
#         try:
#             from market_agent.brain.gemini_client import gemini_client
#             self.gemini = gemini_client
#         except Exception:
#             self.gemini = None
        
#         # Technical analysis engine
#         try:
#             from market_agent.patterns.technical_analysis import ta_engine
#             self.ta_engine = ta_engine
#         except Exception:
#             self.ta_engine = None
            
#         self.last_analysis = {}
#         self._analysis_count = 0

#     def analyze_market_state(self, symbol: str, price_data: Dict, macro_data: Dict, 
#                               df_price_history=None) -> Dict[str, Any]:
#         """
#         Main analysis pipeline.
#         Steps:
#         1. Fetch News & Score Them
#         2. Run Full Technical Analysis (Fibonacci, RSI, MACD, etc.)
#         3. Attempt Gemini AI Analysis (rate-limited)
#         4. Formulate dynamic conclusion from real data
#         """
#         logger.info("autonomous_analysis_started", symbol=symbol)
#         self._analysis_count += 1
        
#         # 1. Fetch live news (Agentic Search)
#         news = []
#         try:
#             news = self.news_aggregator.fetch_news(symbol)
#         except Exception as e:
#             logger.error("news_fetch_failed", error=str(e))
        
#         # 2. Run Technical Analysis
#         tech_analysis = {}
#         if self.ta_engine and df_price_history is not None and len(df_price_history) >= 10:
#             try:
#                 tech_analysis = self.ta_engine.full_analysis(df_price_history)
#             except Exception as e:
#                 logger.error("tech_analysis_failed", error=str(e))
        
#         # 3. Check Data Integrity
#         missing = []
#         current_price = price_data.get('Close') if isinstance(price_data, dict) else None
#         if not current_price: missing.append("Real-time Price")
#         if not news: missing.append("Verified News Stream")
#         if not macro_data: missing.append("Global Macro Context")
#         if not tech_analysis: missing.append("Technical Indicators")
        
#         # 4. Calculate News Sentiment
#         if news:
#             avg_sentiment = sum(n.get('sentiment_score', 0) for n in news) / len(news)
#             max_impact = max(n.get('impact_score', 0) for n in news)
#         else:
#             avg_sentiment = 0.0
#             max_impact = 0.0

#         # 5. Try Gemini AI for enhanced sentiment (if news available)
#         gemini_sentiment = None
#         if self.gemini and self.gemini.is_available and news:
#             try:
#                 headlines = [n.get('title', '') for n in news[:8] if n.get('title')]
#                 if headlines:
#                     gemini_sentiment = self.gemini.score_sentiment(headlines, symbol)
#                     if gemini_sentiment:
#                         # Blend: 70% Gemini, 30% keyword-based
#                         gemini_sent = gemini_sentiment.get("overall_sentiment", 0.0)
#                         avg_sentiment = (gemini_sent * 0.7) + (avg_sentiment * 0.3)
#                         max_impact = max(max_impact, gemini_sentiment.get("overall_impact", 0.0))
#             except Exception as e:
#                 logger.error("gemini_sentiment_failed", error=str(e))

#         # 6. Formulate 'The Thought' — DYNAMIC, never hardcoded
#         thought = self._formulate_thought(
#             symbol, avg_sentiment, max_impact, price_data, missing, 
#             tech_analysis, news
#         )

#         # 7. Try Gemini for richer analysis (every Nth cycle)
#         gemini_analysis = None
#         if self.gemini and self.gemini.is_available and tech_analysis:
#             try:
#                 headlines = [n.get('title', '') for n in news[:5]] if news else []
#                 gemini_analysis = self.gemini.analyze_market(
#                     symbol, tech_analysis, headlines, 
#                     price=current_price
#                 )
#                 if gemini_analysis:
#                     thought = gemini_analysis  # Use AI analysis when available
#             except Exception as e:
#                 logger.error("gemini_analysis_failed", error=str(e))
        
#         analysis = {
#             "symbol": symbol,
#             "timestamp": datetime.now().isoformat(),
#             "sentiment": round(avg_sentiment, 2),
#             "volatility_alert": max_impact > 0.8,
#             "missing_data": missing,
#             "conclusion": thought,
#             "status": "ANALYSIS_COMPLETE" if not missing else "PARTIAL_DATA_ADAPTATION",
#             "active_news": news[:5],
#             "tech_analysis": tech_analysis,
#             "gemini_enhanced": gemini_analysis is not None,
#             "gemini_sentiment": gemini_sentiment,
#         }
        
#         # PERSISTENCE: Save to DB for perpetual memory
#         if self.storage:
#             try:
#                 self.storage.store_brain_thought(
#                     symbol=symbol,
#                     sentiment=avg_sentiment,
#                     conclusion=thought,
#                     missing_data=missing,
#                     news_list=news[:5]
#                 )
#             except Exception as e:
#                 logger.error("persistence_failed", error=str(e))

#         # FAISS MEMORY: Store analysis for semantic recall
#         try:
#             from market_agent.brain.council_memory import get_council_memory
#             memory = get_council_memory()
#             regime = price_data.get("regime", "") if isinstance(price_data, dict) else ""
#             tech_summary = ""
#             if tech_analysis:
#                 tech_summary = f"RSI={tech_analysis.get('rsi', {}).get('value', 'N/A')}"
#             news_summary = ""
#             if news:
#                 news_summary = "; ".join(
#                     n.get("title", "")[:80] for n in news[:3] if n.get("title")
#                 )
#             memory.store_analysis(
#                 symbol=symbol, regime=regime,
#                 conclusion=str(thought)[:300],
#                 sentiment=avg_sentiment,
#                 tech_summary=tech_summary,
#                 news_summary=news_summary
#             )
#         except Exception as e:
#             logger.error("faiss_store_failed", error=str(e))
        
#         self.last_analysis[symbol] = analysis
#         return analysis

#     def _formulate_thought(self, symbol: str, sentiment: float, impact: float, 
#                            price_data: Dict, missing: List[str],
#                            tech_analysis: Dict = None, news: List = None) -> str:
#         """
#         Dynamic reasoning engine — builds conclusion from actual data.
#         Never returns a generic/static string.
#         """
#         parts = []
        
#         # Part 1: Data completeness
#         if missing:
#             parts.append(f"Operating with {len(missing)} blind spot(s): {', '.join(missing)}.")
        
#         # Part 2: Technical analysis summary (the real meat)
#         if tech_analysis and "consensus" in tech_analysis:
#             consensus = tech_analysis["consensus"]
#             direction = consensus.get("direction", "HOLD")
#             confidence = consensus.get("confidence", 0)
#             reasons = consensus.get("reasons", [])
            
#             parts.append(f"Technical consensus for {symbol}: {direction} (confidence {confidence:.0%}).")
            
#             # RSI insight
#             rsi = tech_analysis.get("rsi", {})
#             if rsi:
#                 parts.append(f"RSI at {rsi.get('value', 'N/A')} ({rsi.get('signal', 'N/A')}).")
            
#             # Fibonacci insight
#             fib = tech_analysis.get("fibonacci", {})
#             if fib:
#                 fib_dir = fib.get("direction", "")
#                 key_level = fib.get("retracements", {}).get("0.618", "N/A")
#                 parts.append(f"Fibonacci {fib_dir} trend, key 0.618 level at {key_level}.")
            
#             # EMA trend
#             ema = tech_analysis.get("ema_ribbon", {})
#             if ema:
#                 parts.append(f"EMA ribbon shows {ema.get('trend', 'N/A')}.")
            
#             # Top reasons
#             if reasons:
#                 parts.append(f"Key signals: {'; '.join(reasons[:2])}.")
#         else:
#             # Minimal fallback when no tech analysis available
#             parts.append(f"Technical data for {symbol} is still being processed.")
        
#         # Part 3: News/sentiment overlay
#         if impact > 0.8:
#             if sentiment < -0.3:
#                 parts.append(f"CRITICAL: Highly negative news (sentiment {sentiment:+.2f}). Risk of sell-off detected.")
#             elif sentiment > 0.3:
#                 parts.append(f"CRITICAL: High-impact positive catalyst (sentiment {sentiment:+.2f}). Upward momentum expected.")
#             else:
#                 parts.append(f"High-impact news detected but sentiment mixed ({sentiment:+.2f}).")
#         elif news and len(news) > 0:
#             top_headline = news[0].get('title', '')[:80] if news else ''
#             if sentiment > 0.1:
#                 parts.append(f"News sentiment positive ({sentiment:+.2f}). Top catalyst: {top_headline}.")
#             elif sentiment < -0.1:
#                 parts.append(f"News sentiment negative ({sentiment:+.2f}). Watch: {top_headline}.")
#             else:
#                 parts.append(f"News sentiment neutral ({sentiment:+.2f}). No strong catalyst detected.")
#         else:
#             parts.append("No verified news stream available — relying on technical indicators.")
        
#         return " ".join(parts)


# analyst_agent = AutonomousAnalyst()