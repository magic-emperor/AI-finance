"""
Pattern Memory — FAISS-based market memory for strategy retention.

Stores successful signal patterns and failure patterns.
Winning patterns boost confidence, losing patterns (5+ consecutive failures) trigger evolution.

Uses:
- FAISS for similarity search across historical patterns
- PostgreSQL for pattern metadata persistence
- Cross-brain pattern sharing: if one brain discovers a winning pattern, others can reference it

Flow:
  Signal generated -> resolved -> store_outcome(success/fail)
  New signal -> recall_similar() -> boost/reduce confidence based on pattern history
  Pattern fails 5+ times -> evolve_pattern() -> AI adjusts parameters
"""

import os
import json
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import structlog

logger = structlog.get_logger()

# FAISS import with fallback
try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False


class PatternMemory:
    """FAISS-based market memory for strategy pattern retention."""

    PATTERN_DIM = 16  # Feature vector dimension
    MAX_PATTERNS = 50000
    CONSECUTIVE_FAIL_THRESHOLD = 5  # Evolve after this many consecutive failures

    def __init__(self, persist_dir: str = None):
        self.persist_dir = persist_dir or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'data', 'pattern_memory'
        )
        os.makedirs(self.persist_dir, exist_ok=True)

        self.patterns: List[Dict] = []
        self.pattern_meta: Dict[str, Dict] = {}  # pattern_key -> {wins, losses, streak, params}

        # FAISS index
        if FAISS_AVAILABLE:
            self.index = faiss.IndexFlatIP(self.PATTERN_DIM)  # Inner product (cosine sim)
        else:
            self.index = None

        self._load()

    def _pattern_key(self, symbol: str, regime: str, strategy: str) -> str:
        """Generate a key for pattern grouping."""
        return f"{symbol}|{regime}|{strategy}"

    def _to_vector(self, signal: Dict) -> np.ndarray:
        """Convert signal dict to a fixed-size feature vector for FAISS."""
        price = signal.get('current_price', 0)
        entry = signal.get('entry_price', 0)
        sl = signal.get('stop_loss', 0)
        t1 = signal.get('target_1', 0)
        t2 = signal.get('target_2', 0)
        conf = signal.get('confidence', 0)
        if isinstance(conf, float) and conf <= 1:
            conf_pct = conf
        else:
            conf_pct = conf / 100.0

        features = signal.get('feature_importance', {})
        tf_weights = signal.get('timeframe_weights', {})

        vec = np.array([
            # Price-relative features (normalized)
            (entry - price) / price if price else 0,       # entry offset %
            (sl - price) / price if price else 0,           # SL distance %
            (t1 - price) / price if price else 0,           # T1 distance %
            (t2 - price) / price if price else 0,           # T2 distance %
            conf_pct,                                        # confidence
            1.0 if signal.get('direction') == 'BUY' else -1.0,  # direction
            # Feature importances
            features.get('RSI Signal', 0),
            features.get('Price Momentum', 0),
            features.get('ATR Volatility', 0),
            features.get('News Sentiment', 0),
            features.get('Liquidity', 0),
            # Timeframe weights
            tf_weights.get('1m', 0),
            tf_weights.get('15m', 0),
            tf_weights.get('1h', 0),
            tf_weights.get('1d', 0),
            # Risk
            signal.get('risk_percent', 0) / 10.0,
        ], dtype=np.float32)

        # Normalize to unit vector for cosine similarity
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.reshape(1, -1)

    def store_outcome(self, signal: Dict, result: str, accuracy: float = 0.0,
                      best_price: float = 0.0, strategy: str = "Intraday (Scalp)"):
        """
        Store signal outcome in pattern memory.

        Args:
            signal: The original signal dict
            result: 'T1_HIT', 'T2_HIT', 'SL_HIT', 'EXPIRED'
            accuracy: Gradient accuracy 0-100
            best_price: Best price reached during signal lifetime
            strategy: Strategy type used
        """
        symbol = signal.get('symbol', 'UNKNOWN')
        regime = signal.get('regime', 'UNKNOWN')
        pkey = self._pattern_key(symbol, regime, strategy)

        is_success = result in ('T1_HIT', 'T2_HIT')

        # Update pattern metadata
        if pkey not in self.pattern_meta:
            self.pattern_meta[pkey] = {
                'wins': 0, 'losses': 0, 'total': 0,
                'consecutive_fails': 0, 'consecutive_wins': 0,
                'best_accuracy': 0.0, 'avg_accuracy': 0.0,
                'last_result': None, 'last_updated': None,
                'evolved': False, 'evolution_history': []
            }

        meta = self.pattern_meta[pkey]
        meta['total'] += 1
        meta['last_result'] = result
        meta['last_updated'] = datetime.now().isoformat()
        meta['avg_accuracy'] = (meta['avg_accuracy'] * (meta['total'] - 1) + accuracy) / meta['total']

        if is_success:
            meta['wins'] += 1
            meta['consecutive_wins'] += 1
            meta['consecutive_fails'] = 0
            meta['best_accuracy'] = max(meta['best_accuracy'], accuracy)
        else:
            meta['losses'] += 1
            meta['consecutive_fails'] += 1
            meta['consecutive_wins'] = 0

        # Store in FAISS for similarity search
        vec = self._to_vector(signal)
        pattern_record = {
            'symbol': symbol,
            'direction': signal.get('direction'),
            'regime': regime,
            'strategy': strategy,
            'result': result,
            'accuracy': accuracy,
            'best_price': best_price,
            'confidence': signal.get('confidence'),
            'timestamp': datetime.now().isoformat(),
            'is_success': is_success,
            'pattern_key': pkey,
        }

        if self.index and FAISS_AVAILABLE:
            self.index.add(vec)
        self.patterns.append(pattern_record)

        # Persist
        self._save()

        return pkey

    def recall_similar(self, signal: Dict, k: int = 10) -> Dict:
        """
        Find similar past patterns and compute confidence adjustment.

        Returns:
            {
                'match_count': int,
                'success_rate': float,  # 0-1
                'confidence_boost': float,  # -0.2 to +0.2
                'proven_pattern': bool,  # True if 80%+ success over 5+ signals
                'similar_patterns': List[Dict],
            }
        """
        if not self.index or not FAISS_AVAILABLE or self.index.ntotal == 0:
            return {'match_count': 0, 'success_rate': 0, 'confidence_boost': 0,
                    'proven_pattern': False, 'similar_patterns': []}

        vec = self._to_vector(signal)
        k = min(k, self.index.ntotal)
        scores, indices = self.index.search(vec, k)

        similar = []
        successes = 0
        for i, idx in enumerate(indices[0]):
            if idx < len(self.patterns) and scores[0][i] > 0.7:  # similarity > 70%
                pat = self.patterns[idx]
                similar.append(pat)
                if pat.get('is_success'):
                    successes += 1

        total = len(similar) if similar else 1
        success_rate = successes / total

        # Confidence adjustment: +0.1 for proven patterns, -0.1 for failing patterns
        if total >= 5 and success_rate >= 0.8:
            boost = 0.15
            proven = True
        elif total >= 5 and success_rate <= 0.2:
            boost = -0.15
            proven = False
        elif success_rate > 0.5:
            boost = 0.05
            proven = False
        else:
            boost = -0.05
            proven = False

        return {
            'match_count': total,
            'success_rate': success_rate,
            'confidence_boost': boost,
            'proven_pattern': proven,
            'similar_patterns': similar[-5:],  # last 5
        }

    def check_pattern_health(self, symbol: str, regime: str, strategy: str) -> Dict:
        """Check if a pattern is healthy or needs evolution."""
        pkey = self._pattern_key(symbol, regime, strategy)
        meta = self.pattern_meta.get(pkey)
        if not meta:
            return {'status': 'NEW', 'needs_evolution': False}

        needs_evolution = meta['consecutive_fails'] >= self.CONSECUTIVE_FAIL_THRESHOLD
        win_rate = meta['wins'] / meta['total'] if meta['total'] > 0 else 0

        return {
            'status': 'PROVEN' if win_rate > 0.6 and meta['total'] >= 5 else
                      'FAILING' if needs_evolution else
                      'LEARNING' if meta['total'] < 5 else 'MIXED',
            'wins': meta['wins'],
            'losses': meta['losses'],
            'win_rate': win_rate,
            'consecutive_fails': meta['consecutive_fails'],
            'consecutive_wins': meta['consecutive_wins'],
            'avg_accuracy': meta['avg_accuracy'],
            'needs_evolution': needs_evolution,
        }

    def get_brain_discussion_summary(self) -> List[Dict]:
        """
        Generate a summary of which patterns each brain found successful.
        Used for cross-brain pattern sharing and council discussions.

        Returns list of dicts: [{pattern_key, win_rate, total, best_accuracy, status}]
        """
        summaries = []
        for pkey, meta in self.pattern_meta.items():
            if meta['total'] >= 3:  # Only report patterns with enough data
                win_rate = meta['wins'] / meta['total'] if meta['total'] > 0 else 0
                summaries.append({
                    'pattern_key': pkey,
                    'win_rate': win_rate,
                    'total': meta['total'],
                    'best_accuracy': meta['best_accuracy'],
                    'avg_accuracy': meta['avg_accuracy'],
                    'status': 'PROVEN' if win_rate > 0.7 else 'FAILING' if win_rate < 0.3 else 'MIXED',
                    'needs_evolution': meta['consecutive_fails'] >= self.CONSECUTIVE_FAIL_THRESHOLD,
                })
        summaries.sort(key=lambda x: x['win_rate'], reverse=True)
        return summaries

    def _save(self):
        """Persist pattern metadata to disk."""
        try:
            meta_path = os.path.join(self.persist_dir, 'pattern_meta.json')
            with open(meta_path, 'w') as f:
                json.dump(self.pattern_meta, f, indent=2, default=str)

            if self.index and FAISS_AVAILABLE and self.index.ntotal > 0:
                idx_path = os.path.join(self.persist_dir, 'patterns.faiss')
                faiss.write_index(self.index, idx_path)

            # Save pattern records (last 10000 only)
            rec_path = os.path.join(self.persist_dir, 'pattern_records.json')
            with open(rec_path, 'w') as f:
                json.dump(self.patterns[-10000:], f, indent=2, default=str)
        except Exception as e:
            logger.warning("pattern_memory_save_failed", error=str(e)[:100])

    def _load(self):
        """Load pattern metadata from disk."""
        try:
            meta_path = os.path.join(self.persist_dir, 'pattern_meta.json')
            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    self.pattern_meta = json.load(f)

            rec_path = os.path.join(self.persist_dir, 'pattern_records.json')
            if os.path.exists(rec_path):
                with open(rec_path) as f:
                    self.patterns = json.load(f)

            if FAISS_AVAILABLE:
                idx_path = os.path.join(self.persist_dir, 'patterns.faiss')
                if os.path.exists(idx_path):
                    self.index = faiss.read_index(idx_path)
        except Exception as e:
            logger.warning("pattern_memory_load_failed", error=str(e)[:100])


# Module-level singleton
pattern_memory = PatternMemory()
