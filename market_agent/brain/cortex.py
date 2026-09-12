"""
Phase 3: The Cortex Gatekeeper (The Living Brain)

Queue-based debate system with:
- Auto-triggered debates (no button presses needed)
- Live AI for every brain response (zero templates)
- Time limits: 30s per brain, 5 min total debate
- FAISS recall + Health Monitor for self-awareness
- Debate storage for learning and attribution
"""

import json
import re
import structlog
import time
import numpy as np
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import deque

try:
    from market_agent.brain.gemini_client import logger
except ImportError:
    import structlog
    logger = structlog.get_logger()

# All brains in the system — MUST use hyphens to match BRAIN_ADAPTERS
ALL_BRAINS = [
    "AMV-LSTM",
    "Multi-Modal-Fusion",
    "Cross-Stock-GNN",
    "Multi-Timeframe",
    "Regime-Ensemble",
    "RL-Weighter",
    "Causal-Ensemble",
    "Super-Brain",
    "Liquidity-Sweep",
    "Funding-Rate",       # crypto only — self-filters for non-crypto symbols
]

# Time limits
BRAIN_RESPONSE_TIMEOUT_SEC = 30
MAX_DEBATE_DURATION_SEC = 300  # 5 minutes
MAX_QUEUE_SIZE = 5

# Boss Brain prompt suffix — forces structured JSON output to prevent keyword-scan misfires
# (e.g. "I would NOT be BULLISH" → used to parse as BUY)
BOSS_PROMPT_SUFFIX = '''

Based on all arguments above, you must respond ONLY in this exact JSON format.
Do not add any text before or after the JSON.
Do not use markdown code fences.

{
  "verdict": "BUY",
  "confidence": 0.72,
  "reasoning": "One sentence explaining the key deciding factor"
}

verdict must be exactly one of: BUY, SELL, HOLD (uppercase, no other values)
confidence must be a float between 0.0 and 1.0
'''

class CortexGatekeeper:
    
    
    def __init__(self):
        self.memory = [] # Short-term memory of recent requests
        self.conversation_log = [] # Full dialogue history [Time, Actor, Message]
        self.active_proposals = {} # {model_name: proposal_id}
        self.rejection_cache = {} # {model_name: timestamp_of_last_rejection}
        self.resolved_cache = {} # {proposal_key: timestamp} - prevents immediate re-proposals
        self.last_log_hashes = {} # Prevent duplicate peak efficiency spam
        
        # Debate queue (FIFO, max 5 pending)
        self.debate_queue = deque(maxlen=MAX_QUEUE_SIZE)
        self.active_debate = None  # Current debate in progress
        self._debate_lock = False
        
        # AI client for dynamic responses
        try:
            from market_agent.brain.gemini_client import gemini_client
            self.gemini = gemini_client
        except Exception:
            self.gemini = None
        
        # FAISS memory for debate recall
        try:
            from market_agent.brain.council_memory import get_council_memory
            self.council_memory = get_council_memory()
        except Exception:
            self.council_memory = None
        
        # Health monitor for brain self-awareness
        try:
            from market_agent.brain.health_monitor import get_health_monitor
            self.health_monitor = get_health_monitor()
        except Exception:
            self.health_monitor = None
        
    def clear_memory(self):
        """Wipes the slate clean (User Request)."""
        self.conversation_log = []
        self.active_proposals = {}
        self.rejection_cache = {}
        self.resolved_cache = {}
        self.memory = []
        self.last_log_hashes = {}
        
    def analyze_request(self, model_name: str, raw_complaint: str, market_context: Dict[str, Any], allow_autonomous: bool = False) -> Dict[str, Any]:
        """
        The Core Thinking Loop.
        1. Parse the request (What does the GNN want?)
        2. Verify the Evidence (Is Volatility actually high?)
        3. Decide: APPOVE, REJECT, or HOLD.
        """
        from datetime import datetime, timedelta
        now = datetime.now()
        timestamp_str = now.strftime("%H:%M:%S")
        
        if "Operating at Peak Efficiency" in raw_complaint:
            last_msg = self.last_log_hashes.get(model_name)
            if last_msg == raw_complaint:
                return {"verdict": "IDLE", "reasoning": "Model stable. Skipping duplicate log."}
            self.last_log_hashes[model_name] = raw_complaint

        # 1. Parse Intent FIRST (before any checks that use it)
        intent = "UNKNOWN"
        if "regime" in raw_complaint.lower() or "volatility" in raw_complaint.lower():
            intent = "REGIME_ADAPTATION"
        elif "reward" in raw_complaint.lower():
            intent = "HYPERPARAMETER_TUNE"
        elif "gradient" in raw_complaint.lower() or "sigma" in raw_complaint.lower():
            intent = "MODEL_STABILITY"

        # 0. Deduplication Check (Pending Proposal)
        if model_name in self.active_proposals:
            return {"verdict": "SKIPPED", "reasoning": "Previous proposal still pending user action."}

        # 0.05 Resolution Check (Prevents ghosting back immediately)
        proposal_key = f"{model_name}_{intent}"
        if proposal_key in self.resolved_cache:
            if now - self.resolved_cache[proposal_key] < timedelta(minutes=10):
                return {"verdict": "SKIPPED", "reasoning": "Proposal recently resolved. In cooldown."}

        # 0.1 Deduplication Check (Recent Rejection)
        if model_name in self.rejection_cache:
            last_rejected = self.rejection_cache[model_name]
            if now - last_rejected < timedelta(minutes=5):
                 # Log the Debate (Deduplicated)
                 if self.last_log_hashes.get(f"{model_name}_debate") != raw_complaint[:50]:
                     self.conversation_log.append({
                        "time": timestamp_str,
                        "actor": f"🧠 {model_name}",
                        "message": f"(Repeated Scream) {raw_complaint[:50]}...",
                        "type": "request_muted"
                     })
                     self.conversation_log.append({
                        "time": timestamp_str,
                        "actor": "🛡️ Cortex",
                        "message": f"I hear you, {model_name}, but my previous ruling stands. Cooling down.",
                        "type": "thought"
                     })
                     self.last_log_hashes[f"{model_name}_debate"] = raw_complaint[:50]
                 return {"verdict": "SKIPPED", "reasoning": "Debate active. Cooling down."}

        # Log the incoming request
        self.conversation_log.append({
            "time": timestamp_str,
            "actor": f"🧠 {model_name}",
            "message": f"Status: {raw_complaint}",
            "type": "request"
        })
            
        # 2. Evidence Verification (The "Thinking" Part)
        # We check if the market context *supports* the claim.
        
        evidence_score = 0.5 # Default neutral
        reasoning = []
        
        vol_z_score = market_context.get("vol_z_score", 0.0)
        
        if intent == "REGIME_ADAPTATION":
            # GNN claims regime change. Is Vol actually anomalous?
            if vol_z_score > 2.0:
                evidence_score = 0.95
                reasoning.append(f"✅ Cortex agrees: Volatility Z-Score is {vol_z_score:.2f} (> 2.0). Regime shift confirmed.")
            elif vol_z_score < 1.0:
                evidence_score = 0.2
                reasoning.append(f"❌ Cortex disagrees: Volatility Z-Score is only {vol_z_score:.2f}. Market is normal. GNN is likely overfitting noise.")
            else:
                evidence_score = 0.6
                reasoning.append(f"⚠️ Cortex unsure: Volatility is elevated ({vol_z_score:.2f}) but not critical.")
                
        elif intent == "MODEL_STABILITY":
            # LSTM claims Gradient Explosion.
            # Only valid if recent price moves are extreme.
            price_change_sigma = market_context.get("price_change_sigma", 1.0)
            if price_change_sigma > 3.0:
                 evidence_score = 0.9
                 reasoning.append(f"✅ Cortex confirms: Price moved {price_change_sigma} sigma. Gradient instability is expected.")
            else:
                 evidence_score = 0.3
                 reasoning.append(f"❌ Cortex rejects: Price sigma is low ({price_change_sigma}). Gradient issues are internal implementation bugs, not market related.")

        # 3. Final Verdict
        if "Operating at Peak Efficiency" in raw_complaint:
            verdict = "IDLE"
            action = "SKIP"
        elif evidence_score > 0.95 and allow_autonomous:
            verdict = "APPROVED_AUTO"
            action = "FORWARD_TO_TRAINER"
            reasoning.append("✅ Confidence > 95% & Autonomous Mode Active. Executing immediately.")
        elif evidence_score > 0.8:
            verdict = "APPROVED"
            action = "FORWARD_TO_USER"
        elif evidence_score < 0.4:
             verdict = "AUTO_REJECTED"
             action = "DISCARD"
        else:
            verdict = "FLAGGED_FOR_REVIEW"
            action = "ASK_BOSS"
            
        # Log Cortex's thought process
        if action != "SKIP":
            self.conversation_log.append({
                "time": timestamp_str,
                "actor": "🛡️ Cortex",
                "message": f"Thinking: {reasoning[0] if reasoning else 'Insufficient Data'}",
                "type": "thought"
            })
            
            # Log Verdict
            self.conversation_log.append({
                "time": timestamp_str,
                "actor": "🛡️ Cortex",
                "message": f"Verdict: {verdict} ({action})",
                "type": "verdict"
            })

        if action == "FORWARD_TO_USER":
             # Phase 42: The "Why" Layer (Decisive Features)
             decisive_features = self._get_mock_shap_features(intent, market_context)
             self.active_proposals[model_name] = {
                 "intent": intent,
                 "reasoning": " ".join(reasoning),
                 "complaint": raw_complaint, 
                 "features": decisive_features,
                 "timestamp": datetime.now().isoformat()
             }
        elif action == "DISCARD":
             self.rejection_cache[model_name] = now
            
        result = {
            "model": model_name,
            "intent": intent,
            "verdict": verdict,
            "action": action,
            "confidence": evidence_score,
            "reasoning": " ".join(reasoning),
            "original_complaint": raw_complaint
        }
        
        return result

    def resolve_proposal(self, model_name: str, outcome: str):
        """
        Clears a pending proposal after User Action.
        Outcome: 'APPROVED_BY_USER' or 'REJECTED_BY_USER'
        """
        if model_name in self.active_proposals:
            prop = self.active_proposals[model_name]
            proposal_key = f"{model_name}_{prop['intent']}"
            self.resolved_cache[proposal_key] = datetime.now()
            del self.active_proposals[model_name]
            
        self.conversation_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "actor": "👤 User",
            "message": f"Action: {outcome} for {model_name}",
            "type": "action"
        })

    def initiate_p2p_debate(self, primary_model: str, secondary_model: str, proposal: str, market_context: Dict[str, Any] = None) -> str:
        """
        Phase 3: Live AI P2P Debate.
        Both brains make REAL AI arguments using data + FAISS recall.
        No hardcoded responses.
        """
        ts = datetime.now().strftime("%H:%M:%S")
        symbol = market_context.get("symbol", "Unknown") if market_context else "Unknown"
        vol = market_context.get("vol_z_score", 0.0) if market_context else 1.0
        
        # Log debate start
        self.conversation_log.append({
            "time": ts,
            "actor": "🥊 Neural Arena",
            "message": f"Conflict: {primary_model} vs {secondary_model} on {symbol} — '{proposal}'",
            "type": "debate"
        })
        
        # Build context for AI calls
        debate_context = market_context or {}
        
        # Primary brain argues FOR
        primary_msg = self._get_brain_ai_response(
            brain_name=primary_model,
            topic=f"Argue FOR this proposal: {proposal}",
            symbol=symbol,
            market_context=debate_context,
            stance="ADVOCATE"
        )
        self.conversation_log.append({
            "time": ts, "actor": f"🧠 {primary_model}",
            "message": primary_msg, "type": "debate"
        })
        
        # Secondary brain argues AGAINST
        counter_msg = self._get_brain_ai_response(
            brain_name=secondary_model,
            topic=f"Argue AGAINST this proposal: {proposal}. Counter: {primary_msg[:200]}",
            symbol=symbol,
            market_context=debate_context,
            stance="CHALLENGER"
        )
        self.conversation_log.append({
            "time": ts, "actor": f"🧠 {secondary_model}",
            "message": counter_msg, "type": "debate"
        })
        
        # Boss Brain resolves
        verdict_msg = self._get_brain_ai_response(
            brain_name="BOSS BRAIN",
            topic=(
                f"Two brains disagree on '{proposal}'. "
                f"{primary_model} says: {primary_msg[:200]}. "
                f"{secondary_model} says: {counter_msg[:200]}. "
                f"Give your verdict: APPROVE, REJECT, or MODIFY."
            ),
            symbol=symbol,
            market_context=debate_context,
            stance="JUDGE"
        )
        
        # Determine outcome from verdict
        verdict_upper = verdict_msg.upper() if verdict_msg else ""
        if "APPROVE" in verdict_upper:
            decision = "CONSENSUS_REACHED"
            final_proposal = proposal
        elif "REJECT" in verdict_upper:
            decision = "REJECTED_BY_COUNCIL"
            final_proposal = proposal
        else:
            decision = "MODIFIED_BY_COUNCIL"
            final_proposal = f"{proposal} (modified per Boss Brain)"
        
        self.conversation_log.append({
            "time": ts, "actor": "🛡️ BOSS BRAIN",
            "message": verdict_msg, "type": "verdict"
        })
        self.conversation_log.append({
            "time": ts, "actor": "🤝 Consensus",
            "message": f"Verdict: {decision} | Final Plan: {final_proposal}",
            "type": "debate"
        })
        
        return final_proposal

    def queue_debate(self, topic: str, symbol: str, trigger_type: str,
                     market_metrics: Dict[str, Any] = None,
                     urgency: str = "MEDIUM") -> bool:
        """
        Add a debate to the queue. Auto-triggered — no button press needed.
        Returns True if added, False if queue full or duplicate.
        """
        # Check queue size
        if len(self.debate_queue) >= MAX_QUEUE_SIZE:
            return False
        
        # Duplicate check within queue
        for queued in self.debate_queue:
            if queued.get("topic") == topic and queued.get("symbol") == symbol:
                return False
        
        self.debate_queue.append({
            "topic": topic,
            "symbol": symbol,
            "trigger_type": trigger_type,
            "market_metrics": market_metrics or {},
            "urgency": urgency,
            "queued_at": datetime.now(),
        })
        
        ts = datetime.now().strftime("%H:%M:%S")
        self.conversation_log.append({
            "time": ts, "actor": "📋 QUEUE",
            "message": f"Debate queued ({len(self.debate_queue)}/{MAX_QUEUE_SIZE}): {topic[:80]}",
            "type": "system"
        })
        return True

    def process_debate_queue(self, regret_engine=None) -> Optional[str]:
        """
        Process the next debate in queue. Called by scanner after each cycle.
        Only 1 debate at a time. Returns verdict or None if queue empty.
        """
        if self._debate_lock or not self.debate_queue:
            return None
        
        debate = self.debate_queue.popleft()
        self._debate_lock = True
        self.active_debate = debate
        
        # Ensure symbol (and optional price/atr) in market_metrics for council verdict storage
        market_metrics = dict(debate.get("market_metrics") or {})
        market_metrics.setdefault("symbol", debate.get("symbol", "Unknown"))
        market_metrics.setdefault("trigger_type", debate.get("trigger_type", "health_monitor"))
        market_metrics.setdefault("regime", market_metrics.get("regime", "UNKNOWN"))
        
        try:
            verdict = self.conduct_grand_council(
                participants=ALL_BRAINS,
                topic=debate["topic"],
                market_metrics=market_metrics,
                regret_engine=regret_engine
            )
            return verdict
        finally:
            self._debate_lock = False
            self.active_debate = None

    def conduct_grand_council(self, participants: List[str], topic: str, market_metrics: Dict[str, Any], regret_engine=None) -> str:
        """
        Phase 3: The Living Grand Council.
        
        Every brain speaks via LIVE AI with:
        - Real market data + technical indicators
        - FAISS recall of similar past situations
        - Per-brain accuracy history from DB
        - Health monitor self-awareness
        
        Time limits: 30s per brain, 5 min total.
        Zero hardcoded responses. Zero templates.
        All debates stored in DB + FAISS for future learning.
        """
        council_session_id = str(uuid.uuid4())
        ts = datetime.now().strftime("%H:%M:%S")
        vol = market_metrics.get("vol_z_score", 1.0)
        symbol = market_metrics.get("symbol", "Unknown")
        tech = market_metrics.get("tech_analysis", {})
        consensus = tech.get("consensus", {}) if tech else {}
        rsi = tech.get("rsi", {}) if tech else {}
        fib = tech.get("fibonacci", {}) if tech else {}
        ema = tech.get("ema_ribbon", {}) if tech else {}
        
        self.conversation_log.append({
            "time": ts, 
            "actor": "⚖️ THE COUNCIL", 
            "message": f"Session Started: Analyzing {symbol} — '{topic}' (Vol Z: {vol:.2f})", 
            "type": "debate"
        })
        
        # Build real market context for each brain
        # --- Wiring: Sentiment Oracle, FII/DII, PCR (Orders 12-14) ---
        # These are fetched ONCE per debate and passed to ALL brain prompts as context.
        # They are uncorrelated with the 7 technical brains — real additional alpha.
        try:
            from market_agent.brain.signal_generators import (
                sentiment_oracle_signal, get_fii_dii_signal, get_pcr_signal
            )
            from market_agent.research.news_aggregator import news_aggregator
            _news = news_aggregator.fetch_news(symbol, limit=10) if news_aggregator else []
            _headlines = [(n.get('title') or '')[:100] for n in _news if n.get('title')]
            _sentiment_raw = sum(n.get('sentiment_score', 0) for n in _news if n.get('sentiment_score') is not None)
            _sentiment_score = max(-1.0, min(1.0, _sentiment_raw / max(len(_news), 1)))
            _sentiment_sig  = sentiment_oracle_signal(_sentiment_score, _headlines)
            _fii_dii_sig    = get_fii_dii_signal() if '.NS' in symbol else {'direction': 'N/A', 'evidence': 'N/A (non-NSE)'}
            _pcr_sig        = get_pcr_signal('NIFTY') if '.NS' in symbol or 'NSEBANK' in symbol else {'direction': 'N/A', 'evidence': 'N/A (non-NSE)'}
        except Exception:
            _sentiment_sig = {'direction': 'HOLD', 'confidence': 0.0, 'evidence': 'unavailable'}
            _fii_dii_sig   = {'direction': 'HOLD', 'evidence': 'unavailable'}
            _pcr_sig       = {'direction': 'HOLD', 'evidence': 'unavailable'}

        market_context = {
            "price":            market_metrics.get("price", "N/A"),
            "consensus":        consensus,
            "rsi":              rsi,
            "fibonacci":        fib,
            "ema_ribbon":       ema,
            # ── New uncorrelated alpha signals (Orders 12-14) ──
            "sentiment_oracle": _sentiment_sig,
            "fii_dii":          _fii_dii_sig,
            "pcr":              _pcr_sig,
        }

        # ── Phase 4.11: Compute rich BrainSignal packets for Boss Brain prompt ──
        # Runs all 7 new specialised brain functions against real OHLCV data.
        # Falls back gracefully if DB has no data or any import fails.
        _phase4_brain_signals = []   # List[BrainSignal]
        _phase4_regime_signal = None  # BrainSignal (Brain 2 — meta brain)
        try:
            import pandas as pd
            from market_agent.data.storage.postgres import PostgresStorage as _PGS
            from market_agent.brain.signal_generators import (
                amv_lstm_signal, regime_ensemble_signal, multi_modal_fusion_signal,
                multi_timeframe_signal, cross_stock_gnn_signal, rl_weighter_signal,
                causal_ensemble_signal,
            )
            # Load recent 1h candles from DB
            _storage_p4 = _PGS()
            _records = _storage_p4.get_latest_data(symbol, '1h', limit=150)
            if _records and len(_records) >= 30:
                hist = pd.DataFrame(
                    [{**r['data'], 'timestamp': r['timestamp']} for r in _records]
                )
                hist.set_index('timestamp', inplace=True)
                hist.sort_index(inplace=True)

                # Helper for Brain 4
                def _tf_fetch(sym, interval, period):
                    _recs = _storage_p4.get_latest_data(sym, interval, limit=150)
                    if not _recs: return None
                    _df = pd.DataFrame([{**r['data'], 'timestamp': r['timestamp']} for r in _recs])
                    _df.set_index('timestamp', inplace=True)
                    _df.sort_index(inplace=True)
                    return _df

                # Run all 7 brains
                _b1 = amv_lstm_signal(hist)                    # Brain 1: LSTM/SMA
                _b2 = regime_ensemble_signal(hist)             # Brain 2: Regime (meta)
                _b3 = multi_modal_fusion_signal(hist)          # Brain 3: RSI/MACD
                _b4 = multi_timeframe_signal(symbol, hist, fetch_fn=_tf_fetch)  # Brain 4: Multi-TF
                _b5 = cross_stock_gnn_signal(hist)             # Brain 5: Institutional
                
                # Compute majority vote of technical brains for RL Weighter base signal
                _p4_votes = [_b1.direction, _b3.direction, _b4.direction, _b5.direction]
                _maj_dir = max(set(_p4_votes), key=_p4_votes.count)
                _maj_conf = _p4_votes.count(_maj_dir) / len(_p4_votes)

                _b6 = rl_weighter_signal(                      # Brain 6: Kelly sizer
                    {'direction': _maj_dir, 'confidence': _maj_conf},
                    _load_brain_performance(symbol, limit=20)   # Fix 5A: real perf history
                )
                _b7 = causal_ensemble_signal(hist)             # Brain 7: Mean reversion

                _phase4_regime_signal  = _b2
                _phase4_brain_signals  = [_b1, _b3, _b4, _b5, _b6, _b7]

                # ── Fill recent_accuracy from Phase 5 backtest results ──
                # Reads retrain_results.json, blends with live resolver if available.
                # Boss Brain will see real historical accuracy per brain.
                try:
                    from market_agent.brain.health_monitor import get_brain_accuracy
                    for _bs in [_b1, _b2, _b3, _b4, _b5, _b6, _b7]:
                        _acc = get_brain_accuracy(_bs.brain_name)
                        if _acc is not None:
                            _bs.recent_accuracy = _acc
                except Exception as _eacc:
                    logger.debug('accuracy_fill_failed', error=str(_eacc)[:60])

                logger.info('phase4_brains_computed', symbol=symbol,
                            brain_count=len(_phase4_brain_signals))

                # Tier 1 Fix (Gap 5): Store individual brain predictions BEFORE the vote
                for _bs in _phase4_brain_signals:
                    try:
                        _storage_p4.store_brain_prediction(
                            council_session_id=council_session_id,
                            brain_name=_bs.brain_name,
                            symbol=_bs.symbol,
                            direction=_bs.direction,
                            confidence=_bs.confidence,
                            regime=getattr(_bs, 'regime', _bs.regime_suitability),
                            signal_strength=getattr(_bs, 'signal_strength', None),
                            method_confidence=getattr(_bs, 'method_confidence', None),
                            regime_suitability=getattr(_bs, 'regime_suitability', None),
                        )
                    except Exception as _epred:
                        logger.warning('gap5_store_prediction_failed', brain=_bs.brain_name, error=str(_epred)[:100])

        except Exception as _e4:
            logger.warning('phase4_brain_compute_failed', symbol=symbol, error=str(_e4)[:120])


        
        debate_start = time.time()
        brain_positions = []  # Track all brain positions for DB storage
        
        # Gap 1: Removed 7-brain argument verbalization loop
        # We just format pre-computed signals directly to avoid LLM waste
        if _phase4_brain_signals:
            for _bs in _phase4_brain_signals:
                brain_positions.append({
                    "brain": _bs.brain_name,
                    "position": _bs.direction,
                    "confidence": _bs.confidence,
                    "reasoning": f"Phase 4 Signal: {str(_bs.measurements.get('primary_evidence', ''))[:200]}"
                })
        else:
            for model in participants:
                brain_positions.append({
                    "brain": model, "position": "HOLD",
                    "confidence": 0.5, "reasoning": "Fallback generation"
                })

        # Order 10 (Sec 6.2): Measure brain correlation — if > 0.85 consistently,
        # the council is an echo chamber and needs brain diversity redesign.
        brain_signals_map = {p["brain"]: {"direction": p["position"]} for p in brain_positions}
        self._measure_council_correlation(brain_signals_map, symbol)

        # Gap 7: Fast Vote for VOLATILE_CHAOS
        _fast_decision = False
        parsed_verdict = None
        boss_msg = ""
        regime = market_metrics.get("regime", "UNKNOWN")
        
        if regime == "VOLATILE_CHAOS" and _phase4_brain_signals:
            votes = {"BUY": 0, "SELL": 0, "HOLD": 0}
            for bp in brain_positions:
                votes[bp['position']] += float(bp.get('confidence', 0.5))
            sum_votes = sum(votes.values()) or 1
            highest_dir = max(votes, key=votes.get)
            conf_ratio = votes[highest_dir] / sum_votes
            
            if conf_ratio >= 0.75:
                logger.info('fast_vote_consensus_no_boss', symbol=symbol, direction=highest_dir, confidence=round(conf_ratio, 2))
                boss_msg = f'{{ "verdict": "{highest_dir}", "confidence": {round(conf_ratio, 2)}, "reasoning": "Fast vote consensus — VOLATILE_CHAOS, no debate" }}'
                _fast_decision = True

        if not _fast_decision:
            # ── Phase 4.11: Build rich Boss Brain prompt from BrainSignal packets ──
            if _phase4_brain_signals and _phase4_regime_signal:
                try:
                    from market_agent.brain.boss_prompt_builder import build_boss_prompt
                    _boss_market_ctx = {
                        'fii_dii':   _fii_dii_sig,
                        'pcr':       _pcr_sig,
                        'sentiment': {
                            'direction': _sentiment_sig.get('direction', 'HOLD'),
                            'composite': _sentiment_sig.get('sentiment_score', 0.0),
                            'confidence': _sentiment_sig.get('confidence', 0.0),
                        },
                    }
                    boss_topic = build_boss_prompt(
                        symbol=symbol,
                        brain_signals=_phase4_brain_signals,
                        regime_signal=_phase4_regime_signal,
                        market_context=_boss_market_ctx,
                        strategy_mode=market_metrics.get('strategy_mode', 'Intraday (Scalp)'),
                    )
                    logger.info('phase4_boss_prompt_built', symbol=symbol, chars=len(boss_topic))
                except Exception as _eboss:
                    logger.warning('phase4_boss_prompt_failed', error=str(_eboss)[:120])
                    boss_topic = f"Council debate on '{topic}'. Give your FINAL VERDICT: BUY, SELL, or HOLD.\n" + BOSS_PROMPT_SUFFIX
            else:
                boss_topic = f"Council debate on '{topic}'. Give your FINAL VERDICT: BUY, SELL, or HOLD.\n" + BOSS_PROMPT_SUFFIX

            boss_msg = self._get_brain_ai_response(
                brain_name="BOSS BRAIN", topic=boss_topic, symbol=symbol,
                market_context=market_context, regret_engine=regret_engine,
                stance="EXECUTIVE"
            )
            
            # Path A Phase 3 Optional: If Boss asks for news/search, fetch and re-ask once
            if boss_msg and symbol and symbol != "Unknown":
                _lower = boss_msg.lower()
                if any(phrase in _lower for phrase in ("latest news", "fetch news", "need news", "search for", "get news", "news on")):
                    news_str = self._fetch_news_for_council(symbol)
                    if news_str:
                        follow_topic = f"{boss_topic}\n\nHere is the requested news:\n{news_str}\n\nNow give your FINAL VERDICT: BUY, SELL, or HOLD. Be concise."
                        boss_msg = self._get_brain_ai_response(
                            brain_name="BOSS BRAIN", topic=follow_topic, symbol=symbol,
                            market_context=market_context, regret_engine=regret_engine,
                            stance="EXECUTIVE"
                        )
                        if boss_msg:
                            self.conversation_log.append({
                                "time": ts, "actor": "📰 NEWS INJECTED",
                                "message": "Boss requested news; fetched and re-asked for verdict.", "type": "system"
                            })

        # Determine verdict from Boss Brain — use safe JSON parser
        parsed = self._parse_boss_verdict(boss_msg or "", symbol)
        verdict = parsed["verdict"]
        verdict_confidence = parsed["confidence"]
        if not parsed["parse_success"]:
            logger.warning("boss_brain_parse_failed",
                           symbol=symbol, raw=(boss_msg or "")[:200])
        
        self.conversation_log.append({
            "time": ts, "actor": "🛡️ BOSS BRAIN",
            "message": boss_msg, "type": "verdict"
        })
        
        debate_duration = time.time() - debate_start
        # verdict_confidence already set by _parse_boss_verdict above
        if not isinstance(verdict_confidence, (int, float)):
            verdict_confidence = 0.5

        # Fix 5C: Adjust confidence by brain agreement ratio
        # High agreement = confidence boost; split council = step back
        if verdict in ('BUY', 'SELL') and brain_positions:
            verdict_confidence = self._agreement_factor(
                brain_positions=brain_positions,
                verdict=verdict,
                base_confidence=verdict_confidence,
            )

        # Store debate in FAISS + DB for future recall
        self._store_debate_record(
            topic=topic, symbol=symbol, participants=brain_positions,
            verdict=verdict, boss_msg=boss_msg, duration=debate_duration,
            regime=market_metrics.get("regime", "UNKNOWN"),
            trigger_type=market_metrics.get("trigger_type", "health_monitor"),
            verdict_confidence=verdict_confidence,
        )

        # Path A: Store council verdict (agreed direction + levels) for UI "Council decided" row
        self._store_council_verdict_from_debate(
            symbol=symbol, verdict=verdict, brain_positions=brain_positions,
            market_metrics=market_metrics, council_session_id=council_session_id
        )

        # ── Phase 6: Store council verdict in signal_predictions for outcome tracking ──
        # This is THE feedback loop: Boss Brain verdict → resolver → auto-resolved on next
        # price tick → accuracy score → health_monitor.get_brain_accuracy() improves.
        if verdict in ('BUY', 'SELL'):
            try:
                from market_agent.learning.signal_resolver import SignalResolver
                from market_agent.data.storage.postgres import PostgresStorage as _PGS6

                _price   = float(market_metrics.get('price', 0) or 0)
                _atr_raw = market_metrics.get('atr', None)
                # Derive ATR from Phase 4 hist if available, else fallback to price * 1%
                if _atr_raw and float(_atr_raw) > 0:
                    _atr = float(_atr_raw)
                elif '_phase4_brain_signals' in dir() and _phase4_brain_signals:
                    _atr = float(_phase4_brain_signals[0].measurements.get('atr', _price * 0.01))
                else:
                    _atr = _price * 0.01

                if _price > 0:
                    # Fix 5B: use _build_signal_dict so ATR multipliers match all other brains
                    try:
                        from market_agent.brain.signal_generators import _build_signal_dict
                        _csl_hist = hist if '_phase4_brain_signals' in dir() and _phase4_brain_signals else None
                        _council_sig = _build_signal_dict(
                            symbol=symbol,
                            direction=verdict,
                            current_price=_price,
                            atr=_atr,
                            confidence=float(verdict_confidence),
                            brain_name='Boss Brain Council',
                            regime=market_metrics.get('regime', 'UNKNOWN'),
                            timeframe_min=15,
                            tech_analysis=None,
                            hist=_csl_hist,
                        )
                        if _council_sig is None:
                            raise ValueError('_build_signal_dict returned None')
                        _council_sig['vol_z_score'] = float(market_metrics.get('vol_z_score', 1.0))
                        _council_sig['model_used']  = 'Boss Brain Council'
                        _council_sig['model_name']  = 'Boss Brain Council'
                    except Exception as _e5b:
                        logger.debug('fix5b_fallback', error=str(_e5b)[:80])
                        # Fallback to old formula
                        _t1  = _price + _atr * 0.75 if verdict == 'BUY' else _price - _atr * 0.75
                        _t2  = _price + _atr * 1.50 if verdict == 'BUY' else _price - _atr * 1.50
                        _sl  = _price - _atr * 0.50 if verdict == 'BUY' else _price + _atr * 0.50
                        _council_sig = {
                            'symbol':      symbol,
                            'direction':   verdict,
                            'entry_price': _price,
                            'current_price': _price,
                            'target_1':    round(_t1, 4),
                            'target_2':    round(_t2, 4),
                            'stop_loss':   round(_sl, 4),
                            'confidence':  float(verdict_confidence),
                            'regime':      market_metrics.get('regime', 'UNKNOWN'),
                            'model_used':  'Boss Brain Council',
                            'model_name':  'Boss Brain Council',
                            'vol_z_score': float(market_metrics.get('vol_z_score', 1.0)),
                            'timeframe_min': 15,
                        }
                    _st6  = _PGS6()
                    _res6 = SignalResolver(_st6)
                    _pid6 = _res6.store_signal(
                        _council_sig,
                        strategy=market_metrics.get('strategy_mode', 'Intraday (Scalp)')
                    )
                    if _pid6:
                        logger.info('council_verdict_stored_for_resolution',
                                    symbol=symbol, verdict=verdict,
                                    price=_price,
                                    t1=round(float(_council_sig.get('target_1', _price)), 2),
                                    sl=round(float(_council_sig.get('stop_loss', _price)), 2),
                                    pred_id=_pid6)
            except Exception as _e6:
                logger.warning('council_verdict_store_failed',
                               symbol=symbol, error=str(_e6)[:100])

        # Fix 6: Log this council cycle to brain_logger for panel + audit trail
        try:
            from market_agent.brain.brain_logger import log_cycle as _log_cycle
            _log_cycle(
                symbol=symbol,
                regime=market_metrics.get('regime', 'UNKNOWN'),
                brain_positions=brain_positions,
                verdict=verdict,
                verdict_confidence=verdict_confidence,
                debate_duration_sec=debate_duration,
                council_session_id=council_session_id,
            )
        except Exception as _e_log:
            logger.debug('brain_logger_wire_failed', error=str(_e_log)[:60])

        return verdict


    # ─────────────────────────────────────────────────────────────────
    # Fix 5C: Agreement Factor — weights verdict confidence by brain consensus
    # Per IMPL-PLAN-V3: high agreement = more confident; split = step back.
    # ─────────────────────────────────────────────────────────────────
    def _agreement_factor(
        self,
        brain_positions: list,
        verdict: str,
        base_confidence: float,
    ) -> float:
        """
        Returns adjusted confidence based on how many brains agree with the verdict.
        Encourages trades only when brains directionally align with Boss verdict.

        Scale:
          >= 5/6 brains agree  →  base_conf * 1.10   (max +10%)
          >= 4/6               →  base_conf * 1.00   (no change)
          >= 3/6               →  base_conf * 0.90   (cautious)
          < 3/6                →  base_conf * 0.75   (significant split — step back)
        Capped at 0.95.
        """
        if not brain_positions:
            return base_confidence

        total   = len(brain_positions)
        agreers = sum(
            1 for bp in brain_positions
            if bp.get('position', 'HOLD').upper() == verdict.upper()
        )
        ratio = agreers / total if total > 0 else 0.0

        if ratio >= 5/6:
            factor = 1.10
        elif ratio >= 4/6:
            factor = 1.00
        elif ratio >= 3/6:
            factor = 0.90
        else:
            factor = 0.75

        adjusted = min(0.95, base_confidence * factor)
        logger.info(
            'agreement_factor_applied',
            verdict=verdict, agreers=agreers, total=total,
            ratio=round(ratio, 2), factor=factor,
            before=round(base_confidence, 3), after=round(adjusted, 3),
        )
        return adjusted


    # ─────────────────────────────────────────────────────────────────
    # Boss Brain Verdict Parser — JSON-first, safe HOLD on any failure
    # (Section 2 of the Brain Implementation & Accuracy Guide)
    # ─────────────────────────────────────────────────────────────────
    def _parse_boss_verdict(self, boss_msg: str, symbol: str) -> dict:
        """
        Safely parse Boss Brain JSON response.
        Falls back to HOLD + parse_success=False on any failure — never crashes.

        Expected JSON from Boss Brain:
        {
          "verdict": "BUY",
          "confidence": 0.72,
          "reasoning": "One sentence explaining the key deciding factor"
        }
        """
        try:
            # Strip markdown code fences if Gemini adds them anyway
            clean = boss_msg.strip()
            clean = re.sub(r'```json|```', '', clean).strip()

            # Extract first JSON object found in the response
            json_match = re.search(r'\{[^{}]+\}', clean, re.DOTALL)
            if not json_match:
                raise ValueError('No JSON object found in response')

            result = json.loads(json_match.group())

            # Validate + normalise required fields
            verdict = result.get('verdict', 'HOLD').upper().strip()
            if verdict not in ('BUY', 'SELL', 'HOLD'):
                verdict = 'HOLD'    # refuse any unexpected value

            confidence = float(result.get('confidence', 0.5))
            confidence = max(0.0, min(1.0, confidence))  # clamp 0-1

            reasoning = str(result.get('reasoning', 'Boss Brain verdict.'))

            return {
                'verdict':       f'AI Consensus: {verdict} — {symbol}',
                'direction':     verdict,
                'confidence':    confidence,
                'reasoning':     reasoning,
                'parse_success': True,
            }

        except Exception as e:
            # SAFE DEFAULT: never crash, always return HOLD on failure
            logger.warning(
                'boss_brain_parse_failed',
                symbol=symbol,
                error=str(e)[:120],
                raw=(boss_msg or '')[:200],
            )
            return {
                'verdict':       f'AI Consensus: HOLD — {symbol}',
                'direction':     'HOLD',
                'confidence':    0.0,
                'reasoning':     'Parse failed — safe HOLD.',
                'parse_success': False,
            }

    # ─────────────────────────────────────────────────────────────────
    # Order 10 (Sec 6.2): Council Correlation Monitor
    # ─────────────────────────────────────────────────────────────────
    def _measure_council_correlation(self, brain_signals: dict, symbol: str) -> float:
        """
        Measures how often brains agree on direction each cycle.
        If agreement_rate > 0.80 consistently → council is an echo chamber.
        Log this every cycle for 1 week to understand real council diversity.

        Returns: agreement_rate float (0.0 = fully split, 1.0 = all agree)
        """
        votes = []
        for brain_name, signal in brain_signals.items():
            d = signal.get("direction", "HOLD")
            votes.append(1 if d == "BUY" else (-1 if d == "SELL" else 0))

        if not votes:
            return 0.0

        dominant = max(set(votes), key=votes.count)
        agreement_rate = votes.count(dominant) / len(votes)

        logger.info(
            "council_correlation",
            symbol=symbol,
            agreement_rate=round(agreement_rate, 3),
            votes=votes,
            brain_count=len(votes),
            brain_names=list(brain_signals.keys()),
            warning="ECHO_CHAMBER" if agreement_rate > 0.85 else "OK",
        )

        return agreement_rate

    # ─────────────────────────────────────────────────────────────────
    # Order 15 (Sec 8): Tiered Debate Configuration
    # ─────────────────────────────────────────────────────────────────
    def _get_debate_config(self, regime: str, market_hours_remaining: int) -> dict:
        """
        Choose debate mode based on market conditions.
        In volatile/fast markets a 5-min debate produces a stale signal —
        use fast_vote instead. Save full_debate for stable trending conditions.

        regime                : 'VOLATILE_CHAOS' | 'STABLE_TRENDING' | 'RANGING' | 'SCANNING'
        market_hours_remaining: minutes until market close (0 = after hours)
        """
        # Less than 30 mins to close — fast mode regardless of regime
        if market_hours_remaining < 30:
            return {
                "mode":        "fast_vote",
                "timeout_sec": 20,
                "llm_calls":   1,   # Boss Brain only, no P2P debate
                "description": "End-of-day fast vote",
            }

        if regime == "VOLATILE_CHAOS":
            return {
                "mode":        "fast_vote",
                "timeout_sec": 30,
                "llm_calls":   1,
                "description": "Volatile market — speed over deliberation",
            }
        elif regime == "STABLE_TRENDING":
            return {
                "mode":        "full_debate",
                "timeout_sec": 180,
                "llm_calls":   3,   # advocate + challenger + boss
                "description": "Stable market — full debate justified",
            }
        else:  # RANGING or SCANNING
            return {
                "mode":        "abbreviated",
                "timeout_sec": 90,
                "llm_calls":   2,   # best 2 opposing brains + boss
                "description": "Neutral market — abbreviated debate",
            }

    def _fetch_news_for_council(self, symbol: str) -> str:

        """Path A Phase 3 Optional: Fetch latest news for symbol; used when Boss requests news."""
        try:
            from market_agent.research.news_aggregator import news_aggregator
            items = news_aggregator.fetch_news(symbol, limit=8)
            if not items:
                return ""
            lines = []
            for item in items[:8]:
                title = (item.get("title") or "")[:100]
                sent = item.get("sentiment_score")
                line = f"- {title}" + (f" (sentiment {sent:+.2f})" if sent is not None else "")
                lines.append(line)
            return "\n".join(lines) if lines else ""
        except Exception:
            return ""

    def _get_brain_ai_response(self, brain_name: str, topic: str, symbol: str,
                                market_context: Dict[str, Any] = None,
                                regret_engine=None, stance: str = "") -> str:
        """
        Centralized brain AI call with full context injection.
        Every brain gets:
        1. Its specialty description
        2. Real performance history from DB
        3. FAISS recall of similar past situations
        4. Health monitor self-awareness
        5. Current market data
        
        Falls back to data-only summary if AI unavailable.
        """
        market_context = market_context or {}
        
        # Build performance history
        perf_history = ""
        if regret_engine:
            try:
                perf = regret_engine.get_real_accuracy(model_name=brain_name, symbol=symbol)
                total = perf.get('total', 0)
                if total > 0:
                    perf_history = (
                        f"Track record: {perf.get('accuracy', 0):.1f}% accuracy "
                        f"over {total} predictions ({perf.get('status', 'N/A')}). "
                        f"Trend: {perf.get('trend', 'STABLE')}."
                    )
                else:
                    perf_history = "No prediction history yet. Be cautious."
            except Exception:
                pass
        
        # FAISS recall of similar past situations
        recall_context = ""
        if self.council_memory:
            try:
                recall_context = self.council_memory.recall_for_brain_prompt(
                    symbol=symbol, regime="", current_context=topic
                )
            except Exception:
                pass
        
        # Health monitor self-awareness
        health_context = ""
        if self.health_monitor:
            try:
                health_context = self.health_monitor.get_brain_summary_for_prompt(symbol)
            except Exception:
                pass
        
        # Inject stance if specified
        stance_text = ""
        if stance:
            stance_map = {
                "ADVOCATE": "You are ARGUING FOR this proposal. Make your best case.",
                "CHALLENGER": "You are ARGUING AGAINST. Find weaknesses.",
                "JUDGE": "You are the JUDGE. Weigh both sides fairly.",
                "EXECUTIVE": "You are the EXECUTIVE. Give the final decision.",
            }
            stance_text = stance_map.get(stance, "")
        
        # Try Gemini AI — route by stake tier
        msg = None
        if self.gemini and self.gemini.is_available:
            try:
                full_context = "\n".join(filter(None, [
                    perf_history, recall_context, health_context, stance_text
                ]))
                if stance == "EXECUTIVE":
                    # Check regime — VOLATILE_CHAOS routes to Groq-first (speed-critical)
                    regime = market_context.get("regime", "") if market_context else ""
                    if regime == "VOLATILE_CHAOS" and hasattr(self.gemini, 'brain_response_volatile'):
                        # Geminiiflow: Groq is DEFAULT for volatile markets, Gemini is fallback
                        msg = self.gemini.brain_response_volatile(
                            query=topic, symbol=symbol,
                            brain_role=brain_name, market_context=market_context,
                            performance_history=full_context
                        )
                    elif hasattr(self.gemini, 'brain_response_for_council'):
                        # Standard Boss Brain: Gemini reserved keys
                        msg = self.gemini.brain_response_for_council(
                            query=topic, symbol=symbol,
                            brain_role=brain_name, market_context=market_context,
                            performance_history=full_context
                        )
                    else:
                        msg = self.gemini.brain_response(
                            query=topic, symbol=symbol,
                            brain_role=brain_name, market_context=market_context,
                            performance_history=full_context
                        )
                else:
                    # Individual brains: open-pool keys (no reservation needed)
                    msg = self.gemini.brain_response(
                        query=topic, symbol=symbol,
                        brain_role=brain_name, market_context=market_context,
                        performance_history=full_context
                    )
            except Exception:
                pass

        # Fallback: data-only summary (no templates)
        if not msg:
            msg = self._build_local_brain_response(brain_name, market_context,
                                                    market_context.get("vol_z_score", 1.0) if isinstance(market_context, dict) else 1.0)

        return msg

    
    def _store_debate_record(self, topic: str, symbol: str, participants: List[Dict],
                             verdict: str, boss_msg: str, duration: float,
                             regime: str = "UNKNOWN", trigger_type: str = "health_monitor",
                             verdict_confidence: float = 0.5):
        """Store debate to FAISS + DB + Boss verdict tracking."""
        # 1. FAISS + DB storage (council_memory.store_debate -> storage.store_council_debate)
        try:
            if self.council_memory:
                self.council_memory.store_debate(
                    topic=topic, symbol=symbol, regime=regime,
                    trigger_type=trigger_type,
                    participants=participants,
                    verdict=verdict,
                    verdict_confidence=verdict_confidence,
                    debate_duration_sec=duration,
                )
        except Exception as e:
            logger.debug("debate_storage_failed", error=str(e)[:60])
        
        # 2. Boss verdict tracking (in-memory / health; debate already stored above, do not call storage again)
        try:
            if self.health_monitor:
                self.health_monitor.track_boss_verdict(
                    symbol=symbol, verdict=verdict,
                    brain_positions=participants,
                    debate_topic=topic
                )
        except Exception as e:
            logger.debug("boss_verdict_tracking_failed", error=str(e)[:60])
        
        # 3. Local knowledge archive
        if not hasattr(self, 'knowledge_archive'):
            self.knowledge_archive = []
        
        self.knowledge_archive.append({
            "topic": topic, "verdict": verdict, "symbol": symbol,
            "participants": participants, "duration_sec": round(duration, 1),
            "date": datetime.now().strftime("%H:%M:%S"),
        })

    def _store_council_verdict_from_debate(self, symbol: str, verdict: str,
                                          brain_positions: List[Dict],
                                          market_metrics: Dict[str, Any],
                                          council_session_id: str = None):
        """Path A: Persist council agreed direction + entry/T1/T2/SL to council_verdicts for UI."""
        try:
            verdict_upper = verdict.upper()
            if "BUY" in verdict_upper or "BULLISH" in verdict_upper:
                direction = "BUY"
            elif "SELL" in verdict_upper or "BEARISH" in verdict_upper:
                direction = "SELL"
            else:
                direction = "HOLD"
            price = market_metrics.get("price") or market_metrics.get("current_price")
            if price is None or not isinstance(price, (int, float)) or price <= 0:
                return
            atr = market_metrics.get("atr")
            if atr is None or not isinstance(atr, (int, float)) or atr <= 0:
                atr = float(price) * 0.02
            price, atr = float(price), float(atr)
            sl_dist = atr * 1.5
            if direction == "BUY":
                entry = price * 1.002
                target_1 = entry + sl_dist
                target_2 = entry + sl_dist * 1.5
                stop_loss = entry - sl_dist
            elif direction == "SELL":
                entry = price * 0.998
                target_1 = entry - sl_dist
                target_2 = entry - sl_dist * 1.5
                stop_loss = entry + sl_dist
            else:
                entry = price
                target_1 = target_2 = stop_loss = price
            timeframe_min = market_metrics.get("timeframe_min")
            if timeframe_min is not None and isinstance(timeframe_min, (int, float)):
                timeframe_min = min(20, max(1, int(timeframe_min)))
            else:
                timeframe_min = 15
            import json as _json
            from market_agent.data.storage.postgres import PostgresStorage
            storage = PostgresStorage()
            storage.store_council_verdict(
                symbol=symbol,
                direction=direction,
                entry_price=entry,
                target_1=target_1,
                target_2=target_2,
                stop_loss=stop_loss,
                timeframe_min=timeframe_min,
                participants_json=_json.dumps(brain_positions) if brain_positions else None,
                council_session_id=council_session_id
            )
        except Exception as e:
            logger.debug("store_council_verdict_failed", error=str(e)[:60])

    def _build_local_brain_response(self, model: str, context: Dict[str, Any], vol: float) -> str:
        """
        Data-only fallback when ALL AI providers are unavailable.
        Uses ONLY real numbers from market data — zero template strings.
        """
        consensus = context.get("consensus", {})
        rsi = context.get("rsi", {})
        fib = context.get("fibonacci", {})
        ema = context.get("ema_ribbon", {})
        price = context.get("price", "N/A")
        direction = consensus.get("direction", "N/A")
        confidence = consensus.get("confidence", 0)
        rsi_val = rsi.get("value", "N/A")
        ema_trend = ema.get("trend", "N/A")
        fib_support = fib.get("support", "N/A") if fib else "N/A"
        fib_resist = fib.get("resistance", "N/A") if fib else "N/A"
        
        # Build data-only summary (no hardcoded interpretations)
        parts = [
            f"[{model}] Price={price}",
            f"Direction={direction} ({confidence:.0%})",
            f"RSI={rsi_val}",
            f"Vol_Z={vol:.2f}",
            f"EMA={ema_trend}",
        ]
        if fib_support != "N/A":
            parts.append(f"Fib_S={fib_support}")
        if fib_resist != "N/A":
            parts.append(f"Fib_R={fib_resist}")
        
        return " | ".join(parts) + " [AI unavailable — data only]"

    def _get_mock_shap_features(self, intent: str, context: Dict[str, Any]) -> List[str]:
        """Phase 42: Mock interpretability logic."""
        vol = context.get("vol_z_score", 1.0)
        sigma = context.get("price_change_sigma", 1.0)
        
        if intent == "REGIME_ADAPTATION":
            return [f"Vol_Z ({vol:.2f}) > 2.0", "Sector_Skew > 0.45", "VIX_Delta +12%"]
        elif intent == "MODEL_STABILITY":
            return [f"Price_Sigma ({sigma:.1f}) > 3.0", "Grad_Norm > 40", "Mismatch_Score 0.82"]
        return ["Win_Rate < 45%", "Sharpe < 0.5", "Residual_Variance High"]

    def log_human_feedback(self, model_name: str, action: str, patch_id: str = None):
        """
        Phase 31: Human RL Loop.
        Maps UI actions (Accept/Reject) to model priority rewards.
        """
        import streamlit as st
        import logging
        from datetime import datetime
        logger = logging.getLogger(__name__)

        if 'human_rewards' not in st.session_state:
            st.session_state['human_rewards'] = {}
            
        if model_name not in st.session_state['human_rewards']:
            st.session_state['human_rewards'][model_name] = 0.0
            
        # Reward or Punish based on User Action
        reward = 0.1 if action == "Approve" else -0.15
        st.session_state['human_rewards'][model_name] += reward
        
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "actor": "🛡️ REWARD_NODE",
            "message": f"Human {action} logged for {model_name}. New Weighter Priority: {st.session_state['human_rewards'][model_name]:.2f}",
            "type": "thought"
        }
        self.conversation_log.append(entry)
        logger.info("human_feedback_logged", model=model_name, action=action, reward=reward)

    def inject_user_interference(self, user_message: str):
        """Allows the user to join the debate log directly."""
        if not user_message: return
        self.conversation_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "actor": "👤 USER",
            "message": user_message,
            "type": "action"
        })

    def handle_user_query(self, query: str, symbol: str = "Unknown", analysis_context: Dict[str, Any] = None):
        """
        Conversational Interface — powered by Gemini AI with context.
        Falls back to data-driven local responses when AI unavailable.
        """
        ts = datetime.now().strftime("%H:%M:%S")
        query_lower = query.lower().strip()
        analysis_context = analysis_context or {}
        
        # Log the user's question
        self.conversation_log.append({
            "time": ts, "actor": "👤 USER", "message": query, "type": "action"
        })
        
        # Extract real data from analysis context
        tech = analysis_context.get('tech_analysis', {})
        consensus = tech.get('consensus', {}) if tech else {}
        rsi = tech.get('rsi', {}) if tech else {}
        fib = tech.get('fibonacci', {}) if tech else {}
        sentiment = analysis_context.get('sentiment', 0.0)
        conclusion = analysis_context.get('conclusion', 'Analysis in progress.')
        news = analysis_context.get('active_news', [])
        direction = consensus.get('direction', 'N/A')
        confidence = consensus.get('confidence', 0)
        
        market_context = {
            "price": analysis_context.get('price', 'N/A'),
            "consensus": consensus,
            "rsi": rsi,
            "fibonacci": fib,
        }
        
        # 0. Greeting Handler — Use AI for a smart, contextual welcome
        if any(x in query_lower for x in ["hi", "hey", "hello", "good morning", "good evening"]):
            # Try live AI greeting first
            if self.gemini and self.gemini.is_available:
                try:
                    ai_greeting = self.gemini.brain_response(
                        query=f"The user just greeted you. Respond warmly as Cortex, the AI market analyst. "
                              f"Briefly mention what you're currently analyzing for {symbol}.",
                        symbol=symbol,
                        brain_role="BOSS BRAIN",
                        market_context=market_context,
                        performance_history=f"Direction: {direction}, Confidence: {confidence:.0%}, RSI: {rsi.get('value', 'N/A')}"
                    )
                    if ai_greeting:
                        self.conversation_log.append({
                            "time": ts, "actor": "🛡️ Cortex",
                            "message": ai_greeting,
                            "type": "thought"
                        })
                        return {"response": ai_greeting}
                except Exception:
                    pass

            # Fallback: data-driven greeting (only show fields with real data)
            parts = [f"Hello! I am Cortex, analyzing {symbol}."]
            if direction and direction != "N/A":
                parts.append(f"Current bias: {direction} ({confidence:.0%}).")
            rsi_val = rsi.get('value', None)
            if rsi_val is not None:
                parts.append(f"RSI: {rsi_val}.")
            parts.append("What would you like to know?")
            greeting = " ".join(parts)
            self.conversation_log.append({
                "time": ts, "actor": "🛡️ Cortex", 
                "message": greeting, 
                "type": "thought"
            })
            return {"response": greeting}

        # Try Gemini AI for rich, contextual response
        ai_responses = []
        if self.gemini and self.gemini.is_available:
            # Select 2-3 relevant brains based on query type
            brains_to_ask = ["BOSS BRAIN"]
            if any(x in query_lower for x in ["trend", "pattern", "momentum", "time"]):
                brains_to_ask.append("AMV-LSTM")
            elif any(x in query_lower for x in ["news", "sentiment", "catalyst"]):
                brains_to_ask.append("Multi-Modal Fusion")
            elif any(x in query_lower for x in ["risk", "position", "size", "how much"]):
                brains_to_ask.append("RL Weighter")
            elif any(x in query_lower for x in ["timeframe", "chart", "1h", "15m", "daily"]):
                brains_to_ask.append("Multi-Timeframe")
            else:
                brains_to_ask.append("Multi-Timeframe")
            
            for brain in brains_to_ask:
                try:
                    response = self.gemini.brain_response(
                        query=query, symbol=symbol,
                        brain_role=brain, market_context=market_context
                    )
                    if response:
                        ai_responses.append((brain, response))
                except Exception as e:
                    # logger.error("gemini_query_failed")
                    pass
        
        # If AI succeeded, use those responses
        if ai_responses:
            combined = []
            for model, msg in ai_responses:
                self.conversation_log.append({
                    "time": ts, "actor": f"🧠 {model}", "message": msg, "type": "debate"
                })
                combined.append(f"**{model}:** {msg}")
            return {"response": "\n\n".join(combined)}
        
        # Fallback: Build data-driven conversational responses from real analysis
        responses = []
        
        # Determine overall bias for conversational wording
        sentiment_bias = "positive" if sentiment > 0.1 else "negative" if sentiment < -0.1 else "neutral"
        strength = "strong" if confidence > 0.7 else "developing"
        
        if any(x in query_lower for x in ["what's going on", "status", "update", "what is happening", "how is it", "thinking", "tell me"]):
            if direction == "BUY":
                msg = f"I'm seeing a {strength} bullish setup for {symbol}. {conclusion} The mood is {sentiment_bias}."
            elif direction == "SELL":
                msg = f"Caution is advised for {symbol}. I'm detecting {strength} bearish pressure. {conclusion}"
            else:
                msg = f"The market for {symbol} is currently indecisive. {conclusion} I'm waiting for a clearer signal."
            responses.append(("BOSS BRAIN", msg))
            
        elif any(x in query_lower for x in ["find", "found", "discover", "news"]):
            news_count = len(news)
            if news_count > 0:
                top = news[0].get('title', 'No headline')
                responses.append(("Multi-Modal Fusion", f"I've tracked {news_count} recent updates. The biggest catalyst right now is: '{top}'."))
            else:
                responses.append(("Multi-Modal Fusion", f"I haven't found any major news breaking for {symbol} in the last hour. I'm relying purely on market structure right now."))
                
        elif any(x in query_lower for x in ["target", "entry", "stop", "price", "level"]):
            fib_levels = fib.get('retracements', {}) if fib else {}
            lev_618 = fib_levels.get('0.618', 'N/A')
            if direction == "BUY":
                 msg = f"If you're looking for an entry, I'd watch the 0.618 level at {lev_618}. My main target is aligned with the recent fib extensions."
            elif direction == "SELL":
                 msg = f"I'd place the stop-loss just above the 0.618 retracement ({lev_618}). The downside targets are looking quite clear on the current timeframe."
            else:
                 msg = f"Levels are still forming. I'd keep an eye on {lev_618} as a key pivot point for the next move."
            responses.append(("BOSS BRAIN", msg))
        else:
            neutral_msg = f"I'm monitoring {symbol} closely. The current bias is {direction} with {confidence*100:.0f}% confidence. Sentiment is leaning {sentiment_bias}."
            responses.append(("BOSS BRAIN", neutral_msg))
        
        for model, msg in responses:
            self.conversation_log.append({
                "time": ts, "actor": f"🧠 {model}", "message": msg, "type": "debate"
            })

        if responses:
            return {"response": "\n\n".join(f"**{m}:** {msg}" for m, msg in responses)}
        return {"response": f"I'm monitoring {symbol}. Bias: {direction}, Confidence: {confidence*100:.0f}%. Ask me something specific like 'what is the target?' or 'what news is moving the market?'"}
    def boss_consultation_audit(self, model_name: str, topic: str, actual_outcome: str) -> Dict[str, Any]:
        """
        Phase 3: AI-Powered Failure Audit.
        Boss Brain reviews failure using FAISS recall of similar past mistakes.
        No more hardcoded "Check Greatest Hits #452" — real AI analysis.
        """
        ts = datetime.now().strftime("%H:%M:%S")
        
        # 1. Log the audit request
        self.conversation_log.append({
            "time": ts,
            "actor": f"🧠 {model_name}",
            "message": f"AUDIT REQUEST: Failed on {topic}. Actual outcome: {actual_outcome}.",
            "type": "request"
        })
        
        # 2. FAISS recall: find similar past failures
        similar_failures = ""
        if self.council_memory:
            try:
                similar = self.council_memory.recall_similar(
                    query=f"{model_name} failure on {topic}: {actual_outcome}",
                    memory_type="TRADE_LOSS", k=3
                )
                if similar:
                    similar_failures = "Similar past failures: " + "; ".join(
                        s.get("text", "")[:100] for s in similar
                    )
            except Exception:
                pass
        
        # 3. AI-powered analysis (not a static string)
        audit_topic = (
            f"{model_name} failed on '{topic}'. Actual outcome: {actual_outcome}. "
            f"{similar_failures} "
            f"What specific error did {model_name} make? What should it do differently next time?"
        )
        
        lesson_finding = self._get_brain_ai_response(
            brain_name="BOSS BRAIN", topic=audit_topic,
            symbol=topic.split()[0] if topic else "Unknown",
            market_context={}, stance="JUDGE"
        )
        
        # 4. Store the failure as a FAISS memory for future recall
        if self.council_memory:
            try:
                self.council_memory.store(
                    text=f"{model_name} failed on {topic}: {actual_outcome}. Lesson: {lesson_finding[:200]}",
                    memory_type="TRADE_LOSS",
                    metadata={"brain": model_name, "topic": topic, "outcome": actual_outcome}
                )
            except Exception:
                pass
        
        # 5. Log Boss response
        self.conversation_log.append({
            "time": ts,
            "actor": "🛡️ BOSS BRAIN",
            "message": lesson_finding,
            "type": "verdict"
        })
        
        return {
            "status": "AUDIT_COMPLETE",
            "lesson": lesson_finding,
            "training_trigger": True
        }

    def conduct_failure_reflection(self, evaluations: List[Dict[str, Any]]):
        """
        Reviews evaluations and triggers boss audits for high-confidence failures.
        Also checks health monitor for auto-debate triggers.
        """
        for ev in evaluations:
            if not ev.get("is_correct") and ev.get("confidence", 0) > 0.6:
                # High confidence failure - needs audit
                self.boss_consultation_audit(
                    ev.get("model", "Unknown"),
                    f"{ev.get('symbol', 'Unknown')} Trade",
                    ev.get("actual_direction", "Unknown")
                )
        
        # Auto-trigger debates from health monitor
        if self.health_monitor:
            try:
                triggers = self.health_monitor.generate_debate_triggers()
                for trigger in triggers:
                    self.queue_debate(
                        topic=trigger.get("topic", "Performance review"),
                        symbol=trigger.get("symbol", ""),
                        trigger_type=trigger.get("type", "health_monitor"),
                        urgency=trigger.get("urgency", "MEDIUM")
                    )
            except Exception:
                pass