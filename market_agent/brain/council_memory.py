"""
Phase 3.1: Council Memory — FAISS-Backed Intelligence Memory

Unified memory system for the entire brain ecosystem:
- Stores and recalls council debates, analyses, signals, and regime changes
- Deduplicates debates by hashing topic + symbol + regime
- Provides semantic search (find similar past situations)
- Auto-prunes old memories beyond retention window

Every brain can query: "What happened last time I saw this setup?"
"""

import hashlib
import structlog
import numpy as np
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta

logger = structlog.get_logger()


class CouncilMemory:
    """
    FAISS-backed long-term memory for all brain intelligence.
    
    Stores 6 types of memories:
    - DEBATE: Council debate topics, positions, verdicts, outcomes
    - ANALYSIS: Market analyses with sentiment, technicals, conclusions
    - SIGNAL: Trading signals with direction, confidence, resolution
    - REGIME: Regime changes and transitions
    - TRADE_WIN: Successful trade setups (what worked)
    - TRADE_LOSS: Failed trade setups (what went wrong + attribution)
    """

    MEMORY_TYPES = [
        "DEBATE", "ANALYSIS", "SIGNAL", "REGIME", "TRADE_WIN", "TRADE_LOSS", "NEWS_SENTIMENT"
    ]

    def __init__(self, faiss_index_path: str = None):
        # None = use FAISS_INDEX_PATH from config (or project default); avoids hardcoded paths
        if faiss_index_path is None:
            try:
                from market_agent.config import FAISS_INDEX_PATH
                faiss_index_path = FAISS_INDEX_PATH
            except Exception:
                import os
                faiss_index_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "faiss_index"
                )
        # FAISS engine for fast vector search
        try:
            from market_agent.research.faiss_engine import FAISSEngine
            self.faiss = FAISSEngine(dim=384, index_path=faiss_index_path)
        except Exception as e:
            logger.error("faiss_init_failed", error=str(e))
            self.faiss = None

        # Vector embedding engine
        try:
            from market_agent.research.vector_engine import VectorEngine
            self.encoder = VectorEngine()
        except Exception as e:
            logger.error("vector_engine_init_failed", error=str(e))
            self.encoder = None

        # DB storage for structured debate records
        try:
            from market_agent.data.storage.postgres import PostgresStorage
            self.storage = PostgresStorage()
        except Exception as e:
            logger.error("postgres_init_failed", error=str(e))
            self.storage = None

        # In-memory debate hash cache for quick dedup
        self._debate_hashes = set()
        self._load_recent_debate_hashes()

        logger.info("council_memory_initialized",
                     faiss_ready=self.faiss is not None,
                     encoder_ready=self.encoder is not None,
                     db_ready=self.storage is not None)

    # ═══════════════════════════════════════
    # STORE MEMORIES
    # ═══════════════════════════════════════

    def store_debate(self, topic: str, symbol: str, regime: str,
                     trigger_type: str, participants: List[Dict],
                     verdict: str, verdict_confidence: float,
                     debate_duration_sec: float = 0.0) -> Optional[str]:
        """
        Store a council debate in both FAISS (semantic) and DB (structured).
        Returns debate_hash or None if duplicate.
        """
        debate_hash = self._hash_debate(topic, symbol, regime)

        # Dedup check: skip if identical debate had good outcome recently
        if self._is_duplicate(debate_hash):
            logger.info("debate_skipped_duplicate", topic=topic[:50], symbol=symbol)
            return None

        # Build text for embedding
        participant_summary = " | ".join([
            f"{p.get('brain', 'Unknown')}: {p.get('position', 'N/A')} "
            f"(conf {p.get('confidence', 0):.0f}%)"
            for p in participants
        ])
        embed_text = (
            f"DEBATE on {symbol} ({regime}): {topic}. "
            f"Participants: {participant_summary}. "
            f"Verdict: {verdict} ({verdict_confidence:.0f}% confidence)"
        )

        # FAISS store
        if self.encoder and self.faiss:
            try:
                embedding = self.encoder.encode(embed_text)[0]
                self.faiss.add_vectors(
                    np.array([embedding]),
                    [{
                        "type": "DEBATE",
                        "symbol": symbol,
                        "regime": regime,
                        "topic": topic[:200],
                        "verdict": verdict[:200],
                        "trigger": trigger_type,
                        "confidence": verdict_confidence,
                        "debate_hash": debate_hash,
                    }]
                )
                self.faiss.save()
            except Exception as e:
                logger.error("faiss_store_debate_failed", error=str(e))

        # DB store
        if self.storage:
            try:
                try:
                    embedding_list = embedding.tolist()
                except NameError:
                    embedding_list = None
                self.storage.store_council_debate(
                    topic=topic, symbol=symbol, regime=regime,
                    trigger_type=trigger_type,
                    participants=participants,
                    verdict=verdict,
                    verdict_confidence=verdict_confidence,
                    debate_hash=debate_hash,
                    debate_duration_sec=debate_duration_sec,
                    embedding=embedding_list
                )
            except Exception as e:
                logger.error("db_store_debate_failed", error=str(e))

        self._debate_hashes.add(debate_hash)
        logger.info("debate_stored", topic=topic[:50], symbol=symbol, hash=debate_hash[:12])
        return debate_hash

    def store_analysis(self, symbol: str, regime: str,
                       conclusion: str, sentiment: float,
                       tech_summary: str = "",
                       news_summary: str = "") -> None:
        """Store a market analysis as a FAISS memory."""
        if not self.encoder or not self.faiss:
            return

        embed_text = (
            f"ANALYSIS of {symbol} ({regime}): {conclusion[:300]}. "
            f"Sentiment: {sentiment:+.2f}. "
            f"Technicals: {tech_summary[:200]}. "
            f"News: {news_summary[:200]}"
        )

        try:
            embedding = self.encoder.encode(embed_text)[0]
            self.faiss.add_vectors(
                np.array([embedding]),
                [{
                    "type": "ANALYSIS",
                    "symbol": symbol,
                    "regime": regime,
                    "sentiment": sentiment,
                    "conclusion": conclusion[:300],
                }]
            )
            self.faiss.save()
        except Exception as e:
            logger.error("faiss_store_analysis_failed", error=str(e))

    def store_signal_outcome(self, symbol: str, direction: str,
                             entry_price: float, exit_price: float,
                             accuracy: float, regime: str,
                             attribution: str = "") -> None:
        """Store a signal's outcome (win or loss) for future recall."""
        if not self.encoder or not self.faiss:
            return

        outcome = "WIN" if accuracy >= 50 else "LOSS"
        memory_type = "TRADE_WIN" if outcome == "WIN" else "TRADE_LOSS"

        embed_text = (
            f"TRADE {outcome} on {symbol}: {direction} at {entry_price:.2f}, "
            f"exited at {exit_price:.2f}. Accuracy: {accuracy:.1f}%. "
            f"Regime: {regime}. {attribution[:200]}"
        )

        try:
            embedding = self.encoder.encode(embed_text)[0]
            self.faiss.add_vectors(
                np.array([embedding]),
                [{
                    "type": memory_type,
                    "symbol": symbol,
                    "direction": direction,
                    "accuracy": accuracy,
                    "regime": regime,
                    "attribution": attribution[:200],
                }]
            )
            self.faiss.save()
        except Exception as e:
            logger.error("faiss_store_signal_failed", error=str(e))

    def store_regime_change(self, symbol: str,
                            old_regime: str, new_regime: str,
                            trigger_reason: str) -> None:
        """Store a regime transition for pattern recognition."""
        if not self.encoder or not self.faiss:
            return

        embed_text = (
            f"REGIME CHANGE for {symbol}: {old_regime} → {new_regime}. "
            f"Trigger: {trigger_reason}"
        )

        try:
            embedding = self.encoder.encode(embed_text)[0]
            self.faiss.add_vectors(
                np.array([embedding]),
                [{
                    "type": "REGIME",
                    "symbol": symbol,
                    "old_regime": old_regime,
                    "new_regime": new_regime,
                    "trigger": trigger_reason[:200],
                }]
            )
            self.faiss.save()
        except Exception as e:
            logger.error("faiss_store_regime_failed", error=str(e))

    def store(self, text: str, memory_type: str = "ANALYSIS",
              metadata: Dict[str, Any] = None) -> None:
        """
        Generic store: encode text and add to FAISS with metadata.
        Used by AttributionEngine and any caller that needs to store free-form memory.
        """
        if not self.encoder or not self.faiss:
            return
        meta = metadata or {}
        try:
            embedding = self.encoder.encode(text)[0]
            self.faiss.add_vectors(
                np.array([embedding]),
                [{"type": memory_type, "text": text[:500], **meta}]
            )
            self.faiss.save()
        except Exception as e:
            logger.error("faiss_store_generic_failed", error=str(e))

    def store_news_sentiment_outcome(self, symbol: str, date: str,
                                      sentiment_score: float, return_1d: float,
                                      return_intraday: Optional[float] = None) -> None:
        """
        Path A Plan A: Store one news-sentiment → return outcome for RAG recall.
        Call from backfill or when we have (news date, sentiment, next-day return).
        """
        text = (
            f"NEWS_SENTIMENT {symbol} date {date} sentiment {sentiment_score:.2f} "
            f"return_1d {return_1d:.2f}%"
        )
        if return_intraday is not None:
            text += f" return_intraday {return_intraday:.2f}%"
        meta = {
            "symbol": symbol,
            "date": str(date),
            "sentiment_score": sentiment_score,
            "return_1d": return_1d,
        }
        if return_intraday is not None:
            meta["return_intraday"] = return_intraday
        self.store(text=text, memory_type="NEWS_SENTIMENT", metadata=meta)

    # ═══════════════════════════════════════
    # RECALL MEMORIES
    # ═══════════════════════════════════════

    def recall_similar(self, query: str, k: int = 5,
                       memory_type: str = None,
                       symbol: str = None) -> List[Dict]:
        """
        Find similar past situations using semantic search.
        
        Args:
            query: Natural language description of current situation
            k: Number of results to return
            memory_type: Filter by type (DEBATE, ANALYSIS, SIGNAL, etc.)
            symbol: Filter by symbol
            
        Returns:
            List of matching memories with distance scores
        """
        if not self.encoder or not self.faiss:
            return []

        try:
            query_vec = self.encoder.encode(query)[0]
            # Request more results so we can filter
            raw_results = self.faiss.search(query_vec, k=k * 3)

            results = []
            for distance, meta in raw_results:
                # Apply filters
                if memory_type and meta.get("type") != memory_type:
                    continue
                if symbol and meta.get("symbol") != symbol:
                    continue

                results.append({
                    "distance": distance,
                    "relevance": max(0, 1.0 - (distance / 100.0)),  # Normalized
                    **meta
                })

                if len(results) >= k:
                    break

            return results

        except Exception as e:
            logger.error("faiss_recall_failed", error=str(e))
            return []

    def recall_for_brain_prompt(self, symbol: str, regime: str,
                                current_context: str,
                                k: int = 5) -> str:
        """
        Build a recall context string for injection into AI prompts.
        Returns a formatted string ready to be injected into any brain prompt.
        """
        query = f"{symbol} {regime} {current_context}"
        memories = self.recall_similar(query, k=k)

        if not memories:
            return ""

        lines = ["=== MEMORY RECALL (similar past situations) ==="]
        for i, mem in enumerate(memories, 1):
            mem_type = mem.get("type", "UNKNOWN")
            relevance = mem.get("relevance", 0)

            if mem_type == "DEBATE":
                lines.append(
                    f"{i}. [DEBATE] {mem.get('topic', 'N/A')} → "
                    f"Verdict: {mem.get('verdict', 'N/A')} "
                    f"(relevance: {relevance:.0%})"
                )
            elif mem_type == "ANALYSIS":
                lines.append(
                    f"{i}. [ANALYSIS] {mem.get('conclusion', 'N/A')} "
                    f"(sentiment: {mem.get('sentiment', 0):+.2f}, "
                    f"relevance: {relevance:.0%})"
                )
            elif mem_type in ("TRADE_WIN", "TRADE_LOSS"):
                outcome = "✅ WIN" if mem_type == "TRADE_WIN" else "❌ LOSS"
                lines.append(
                    f"{i}. [{outcome}] {mem.get('direction', 'N/A')} on "
                    f"{mem.get('symbol', 'N/A')} — "
                    f"accuracy {mem.get('accuracy', 0):.0f}% "
                    f"(relevance: {relevance:.0%})"
                )
            elif mem_type == "REGIME":
                lines.append(
                    f"{i}. [REGIME] {mem.get('old_regime', '?')} → "
                    f"{mem.get('new_regime', '?')} — "
                    f"{mem.get('trigger', 'N/A')} "
                    f"(relevance: {relevance:.0%})"
                )
            else:
                lines.append(
                    f"{i}. [{mem_type}] {str(mem)[:150]} "
                    f"(relevance: {relevance:.0%})"
                )

        lines.append("=== END MEMORY RECALL ===")
        return "\n".join(lines)

    def get_debate_history(self, symbol: str = None,
                           limit: int = 20) -> List[Dict]:
        """
        Get structured debate history from DB.
        For the Council Historian — searchable debate records.
        """
        if not self.storage:
            return []

        try:
            return self.storage.get_council_debates(symbol=symbol, limit=limit)
        except Exception as e:
            logger.error("debate_history_failed", error=str(e))
            return []

    # ═══════════════════════════════════════
    # DEDUPLICATION
    # ═══════════════════════════════════════

    def _hash_debate(self, topic: str, symbol: str, regime: str) -> str:
        """Generate a deterministic hash for debate deduplication."""
        raw = f"{topic.strip().lower()}|{symbol}|{regime}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _is_duplicate(self, debate_hash: str) -> bool:
        """Check if an identical debate had EXCELLENT outcome in last 7 days."""
        if debate_hash not in self._debate_hashes:
            return False

        # Check DB for recent outcome
        if self.storage:
            try:
                recent = self.storage.get_debate_by_hash(debate_hash, days=7)
                if recent and recent.get("outcome") == "EXCELLENT":
                    return True
            except Exception:
                pass

        return False

    def _load_recent_debate_hashes(self) -> None:
        """Load debate hashes from last 7 days for fast dedup checks."""
        if not self.storage:
            return

        try:
            recent_debates = self.storage.get_council_debates(limit=200)
            for debate in recent_debates:
                h = debate.get("debate_hash")
                if h:
                    self._debate_hashes.add(h)
        except Exception:
            pass

    # ═══════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════

    def get_memory_stats(self) -> Dict[str, Any]:
        """Get memory statistics for monitoring."""
        total_vectors = self.faiss.index.ntotal if self.faiss else 0

        # Count by type
        type_counts = {}
        if self.faiss:
            for meta in self.faiss.metadata:
                t = meta.get("type", "UNKNOWN")
                type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "total_memories": total_vectors,
            "by_type": type_counts,
            "debate_hashes_cached": len(self._debate_hashes),
            "faiss_ready": self.faiss is not None,
            "encoder_ready": self.encoder is not None,
            "db_ready": self.storage is not None,
        }


# Module-level singleton (lazy init)
_council_memory = None

def get_council_memory() -> CouncilMemory:
    """Get or create the global CouncilMemory singleton."""
    global _council_memory
    if _council_memory is None:
        _council_memory = CouncilMemory()
    return _council_memory
