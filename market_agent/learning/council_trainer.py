"""
Phase 3.4: Council Trainer — RL Weight Updates from Debate Outcomes

After a debate verdict resolves (Boss Brain was right or wrong about price),
this module:
1. Identifies which brains were right/wrong in the debate
2. Adjusts brain weights: right → weight up, wrong → weight down
3. Per-regime attribution: updates health monitor weights for specific regimes
4. Stores training events for future FAISS recall

Reinforcement signal:
    reward = +0.1 if brain's position matched actual outcome
    penalty = -0.15 if brain's position was wrong
    weight_update = current_weight + (reward * learning_rate)
"""

import structlog
from typing import Dict, List, Any, Optional
from datetime import datetime

logger = structlog.get_logger()

# RL hyperparameters
REWARD_CORRECT = 0.1        # Weight increase for correct position
PENALTY_WRONG = -0.15       # Weight decrease for wrong position
LEARNING_RATE = 0.5         # How fast weights change (0.0-1.0)
MIN_WEIGHT = 0.2            # Floor — never zero out a brain
MAX_WEIGHT = 2.5            # Ceiling — prevent runaway weights
ABSTAIN_PENALTY = -0.02     # Small penalty for not participating


class CouncilTrainer:
    """
    RL trainer that adjusts brain weights based on debate outcomes.
    
    Flow:
    1. Debate happens → verdict stored in DB
    2. Price resolves → actual outcome known
    3. CouncilTrainer compares each brain's position vs actual
    4. Weights adjusted via RL formula
    5. Health monitor picks up new weights next cycle
    """
    
    def __init__(self, storage=None):
        self._storage = storage
        self._health_monitor = None
        self._council_memory = None
        self._training_log = []
    
    @property
    def storage(self):
        if self._storage is None:
            try:
                from market_agent.data.storage.postgres import PostgresStorage
                self._storage = PostgresStorage()
            except Exception:
                pass
        return self._storage
    
    @property
    def health_monitor(self):
        if self._health_monitor is None:
            try:
                from market_agent.brain.health_monitor import get_health_monitor
                self._health_monitor = get_health_monitor()
            except Exception:
                pass
        return self._health_monitor
    
    @property
    def council_memory(self):
        if self._council_memory is None:
            try:
                from market_agent.brain.council_memory import get_council_memory
                self._council_memory = get_council_memory()
            except Exception:
                pass
        return self._council_memory
    
    def resolve_debate(self, debate_id: int = None, symbol: str = None,
                        actual_direction: str = None) -> Dict[str, Any]:
        """
        Resolve a past debate against actual price outcome.
        
        Args:
            debate_id: Specific debate to resolve (optional)
            symbol: Symbol to resolve debates for
            actual_direction: Actual price direction ("BUY"/"SELL"/"HOLD")
            
        Returns:
            {resolved, rewards, weight_updates}
        """
        result = {
            "resolved": 0,
            "rewards": [],
            "weight_updates": {},
            "training_events": []
        }
        
        if not actual_direction:
            return result
        
        # Get unresolved debates for this symbol
        debates = self._get_unresolved_debates(symbol, debate_id)
        if not debates:
            return result
        
        for debate in debates:
            event = self._process_single_debate(debate, actual_direction)
            if event:
                result["resolved"] += 1
                result["rewards"].extend(event.get("rewards", []))
                result["training_events"].append(event)
                
                # Accumulate weight updates
                for brain, delta in event.get("weight_deltas", {}).items():
                    if brain not in result["weight_updates"]:
                        result["weight_updates"][brain] = 0.0
                    result["weight_updates"][brain] += delta
        
        # Apply accumulated weight updates to health monitor
        if result["weight_updates"] and self.health_monitor:
            self._apply_weight_updates(result["weight_updates"])
        
        logger.info("debates_resolved",
                     count=result["resolved"],
                     updates=len(result["weight_updates"]))
        
        return result
    
    def _process_single_debate(self, debate: Dict, 
                                 actual_direction: str) -> Optional[Dict]:
        """
        Process one debate: compare each brain's position to actual.
        Returns training event with rewards and weight deltas.
        """
        participants = debate.get("participants", [])
        if not participants:
            return None
        
        verdict = debate.get("verdict", "")
        symbol = debate.get("symbol", "Unknown")
        topic = debate.get("topic", "")
        
        rewards = []
        weight_deltas = {}
        correct_brains = []
        wrong_brains = []
        
        for brain_pos in participants:
            brain_name = brain_pos.get("brain", "Unknown")
            position = brain_pos.get("position", "HOLD")
            
            if position == "ABSTAINED":
                # Small penalty for not participating
                delta = ABSTAIN_PENALTY * LEARNING_RATE
                weight_deltas[brain_name] = delta
                rewards.append({
                    "brain": brain_name, "reward": delta,
                    "reason": "Abstained from debate"
                })
                continue
            
            # Compare position to actual
            is_correct = self._positions_match(position, actual_direction)
            
            if is_correct:
                delta = REWARD_CORRECT * LEARNING_RATE
                correct_brains.append(brain_name)
            else:
                delta = PENALTY_WRONG * LEARNING_RATE
                wrong_brains.append(brain_name)
            
            weight_deltas[brain_name] = delta
            rewards.append({
                "brain": brain_name,
                "reward": delta,
                "position": position,
                "actual": actual_direction,
                "correct": is_correct
            })
        
        # Check if Boss verdict was correct
        boss_correct = self._positions_match(
            self._extract_direction(verdict), actual_direction
        )
        
        # Store training event
        event = {
            "debate_id": debate.get("id"),
            "symbol": symbol,
            "topic": topic,
            "actual_direction": actual_direction,
            "boss_correct": boss_correct,
            "correct_brains": correct_brains,
            "wrong_brains": wrong_brains,
            "rewards": rewards,
            "weight_deltas": weight_deltas,
            "timestamp": datetime.now().isoformat()
        }
        
        self._training_log.append(event)
        
        # Store in FAISS for future recall
        self._store_training_memory(event)
        
        # Mark debate as resolved in DB
        self._mark_debate_resolved(debate, actual_direction, boss_correct)
        
        return event
    
    def _positions_match(self, position: str, actual: str) -> bool:
        """Check if a brain's position matched the actual outcome."""
        pos = position.upper()
        act = actual.upper()
        
        # Direct match
        if pos == act:
            return True
        
        # Synonym matching
        bullish = {"BUY", "BULLISH", "LONG"}
        bearish = {"SELL", "BEARISH", "SHORT"}
        
        if pos in bullish and act in bullish:
            return True
        if pos in bearish and act in bearish:
            return True
        
        return False
    
    def _extract_direction(self, verdict: str) -> str:
        """Extract BUY/SELL/HOLD from a verdict string."""
        if not verdict:
            return "HOLD"
        v = verdict.upper()
        if "BUY" in v or "BULLISH" in v:
            return "BUY"
        elif "SELL" in v or "BEARISH" in v:
            return "SELL"
        return "HOLD"
    
    def _apply_weight_updates(self, weight_deltas: Dict[str, float]):
        """Apply accumulated weight changes to health monitor."""
        if not self.health_monitor:
            return
        
        current_weights = self.health_monitor.get_brain_weights()
        
        for brain, delta in weight_deltas.items():
            old_weight = current_weights.get(brain, 1.0)
            new_weight = old_weight + delta
            new_weight = max(MIN_WEIGHT, min(MAX_WEIGHT, new_weight))
            
            self.health_monitor._weights[brain] = round(new_weight, 3)
            
            logger.info("brain_weight_updated",
                         brain=brain,
                         old=round(old_weight, 3),
                         new=round(new_weight, 3),
                         delta=round(delta, 3))
    
    def _get_unresolved_debates(self, symbol: str = None,
                                  debate_id: int = None) -> List[Dict]:
        """Get unresolved debates from DB."""
        if not self.storage:
            return []
        
        try:
            from sqlalchemy import text
            session = self.storage.Session()
            
            if debate_id:
                query = text("""
                    SELECT id, topic, symbol, verdict, participants_json
                    FROM council_debates
                    WHERE id = :id AND (outcome IS NULL OR outcome = 'PENDING')
                """)
                rows = session.execute(query, {"id": debate_id}).fetchall()
            elif symbol:
                query = text("""
                    SELECT id, topic, symbol, verdict, participants_json
                    FROM council_debates
                    WHERE symbol = :symbol AND (outcome IS NULL OR outcome = 'PENDING')
                    ORDER BY created_at DESC LIMIT 10
                """)
                rows = session.execute(query, {"symbol": symbol}).fetchall()
            else:
                query = text("""
                    SELECT id, topic, symbol, verdict, participants_json
                    FROM council_debates
                    WHERE outcome IS NULL OR outcome = 'PENDING'
                    ORDER BY created_at DESC LIMIT 10
                """)
                rows = session.execute(query).fetchall()
            
            session.close()
            
            import json
            debates = []
            for row in rows:
                participants = row[4]
                if isinstance(participants, str):
                    try:
                        participants = json.loads(participants)
                    except Exception:
                        participants = []
                
                debates.append({
                    "id": row[0],
                    "topic": row[1],
                    "symbol": row[2],
                    "verdict": row[3],
                    "participants": participants or []
                })
            
            return debates
            
        except Exception as e:
            logger.debug("get_unresolved_debates_failed", error=str(e)[:60])
            return []
    
    def _mark_debate_resolved(self, debate: Dict, actual: str, boss_correct: bool):
        """Mark a debate as resolved in DB."""
        if not self.storage:
            return
        
        try:
            from sqlalchemy import text
            session = self.storage.Session()
            
            outcome = "CORRECT" if boss_correct else "WRONG"
            
            session.execute(text("""
                UPDATE council_debates
                SET outcome = :outcome, resolved_at = NOW()
                WHERE id = :id
            """), {"outcome": outcome, "id": debate.get("id")})
            
            session.commit()
            session.close()
        except Exception as e:
            logger.debug("mark_resolved_failed", error=str(e)[:60])
    
    def _store_training_memory(self, event: Dict):
        """Store training event in FAISS for future context."""
        if not self.council_memory:
            return
        
        try:
            correct_str = ", ".join(event.get("correct_brains", []))
            wrong_str = ", ".join(event.get("wrong_brains", []))
            
            text = (
                f"Debate resolved on {event.get('symbol', 'Unknown')}: "
                f"Actual={event.get('actual_direction', 'Unknown')}. "
                f"Correct brains: [{correct_str}]. "
                f"Wrong brains: [{wrong_str}]. "
                f"Boss verdict {'CORRECT' if event.get('boss_correct') else 'WRONG'}."
            )
            
            self.council_memory.store(
                text=text,
                memory_type="DEBATE_RESOLVED",
                metadata={
                    "symbol": event.get("symbol"),
                    "actual": event.get("actual_direction"),
                    "correct": event.get("correct_brains"),
                    "wrong": event.get("wrong_brains"),
                }
            )
        except Exception:
            pass
    
    def auto_resolve_from_prices(self) -> Dict[str, Any]:
        """
        Called by scanner: check unresolved debates and resolve
        those where price has moved enough to determine outcome.
        """
        result = {"resolved": 0, "checked": 0}
        
        debates = self._get_unresolved_debates()
        if not debates:
            return result
        
        for debate in debates:
            symbol = debate.get("symbol", "")
            if not symbol:
                continue
            
            result["checked"] += 1
            
            # Get actual price direction from recent data
            actual_dir = self._get_actual_direction(symbol)
            if not actual_dir:
                continue
            
            event = self.resolve_debate(
                debate_id=debate.get("id"),
                symbol=symbol,
                actual_direction=actual_dir
            )
            result["resolved"] += event.get("resolved", 0)
        
        return result
    
    def _get_actual_direction(self, symbol: str) -> Optional[str]:
        """
        Determine actual price direction for a symbol from recent data.
        Uses signal_resolver's resolved predictions.
        """
        if not self.storage:
            return None
        
        try:
            from market_agent.learning.signal_resolver import SignalResolver
            resolver = SignalResolver(self.storage)
            
            stats = resolver.get_accuracy_stats(symbol=symbol, last_n=5)
            if not stats or stats.get("total", 0) == 0:
                return None
            
            # Use the most common resolved direction
            recent = stats.get("recent_directions", [])
            if recent:
                from collections import Counter
                most_common = Counter(recent).most_common(1)
                if most_common:
                    return most_common[0][0]
            
            # Fallback to overall direction from accuracy
            accuracy = stats.get("accuracy", 50)
            if accuracy > 60:
                return "BUY"
            elif accuracy < 40:
                return "SELL"
            
            return None
        except Exception:
            return None
    
    def get_training_summary(self) -> Dict[str, Any]:
        """Get summary of all training events this session."""
        if not self._training_log:
            return {"total_events": 0, "brain_rewards": {}}
        
        brain_rewards = {}
        boss_accuracy = {"correct": 0, "total": 0}
        
        for event in self._training_log:
            for reward in event.get("rewards", []):
                brain = reward.get("brain", "Unknown")
                if brain not in brain_rewards:
                    brain_rewards[brain] = {
                        "total_reward": 0, "correct": 0, "wrong": 0
                    }
                brain_rewards[brain]["total_reward"] += reward.get("reward", 0)
                if reward.get("correct"):
                    brain_rewards[brain]["correct"] += 1
                else:
                    brain_rewards[brain]["wrong"] += 1
            
            boss_accuracy["total"] += 1
            if event.get("boss_correct"):
                boss_accuracy["correct"] += 1
        
        return {
            "total_events": len(self._training_log),
            "brain_rewards": brain_rewards,
            "boss_accuracy": boss_accuracy,
        }


# Singleton
_trainer = None

def get_council_trainer(storage=None) -> CouncilTrainer:
    global _trainer
    if _trainer is None:
        _trainer = CouncilTrainer(storage=storage)
    return _trainer
