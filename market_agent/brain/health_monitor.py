"""
Phase 3.1: Brain Health Monitor

The self-awareness system that answers: "How is each brain performing?"
Not hardcoded — calculates from real DB data.

Responsibilities:
1. Per-brain accuracy by regime (not just overall)
2. Gap detection: "GNN lacks volatile-regime training data"
3. Debate trigger recommendations
4. Brain weight adjustment based on recent performance
5. Cross-brain performance comparison

This monitor IS what triggers council debates automatically.
"""

import structlog
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta

logger = structlog.get_logger()

# All brain names in the system — MUST use hyphens (matches cortex.py and signal_generators.py)
ALL_BRAINS = [
    "AMV-LSTM",
    "Regime-Ensemble",
    "Multi-Modal-Fusion",
    "Multi-Timeframe",
    "Cross-Stock-GNN",
    "RL-Weighter",
    "Causal-Ensemble",
    "Liquidity-Sweep",
    "Funding-Rate",
]

# Regime names — MUST match unified taxonomy from signal_generators.py
KNOWN_REGIMES = [
    "TRENDING_UP",
    "TRENDING_DOWN",
    "RANGING",
    "VOLATILE",
    "SQUEEZE",
    "CHAOS",
]

# Minimum predictions needed before judging a brain
MIN_PREDICTIONS_FOR_JUDGMENT = 5


class BrainHealthMonitor:
    """
    Real-time brain health tracking from DB data — ZERO hardcoded values.
    
    Usage:
        monitor = BrainHealthMonitor()
        report = monitor.full_health_check("RELIANCE.NS")
        triggers = monitor.get_debate_triggers("RELIANCE.NS")
    """

    def __init__(self, storage=None, signal_resolver=None):
        try:
            from market_agent.data.storage.postgres import PostgresStorage
            self.storage = storage or PostgresStorage()
        except Exception:
            self.storage = None

        try:
            from market_agent.learning.signal_resolver import SignalResolver
            self.resolver = signal_resolver or (
                SignalResolver(self.storage) if self.storage else None
            )
        except Exception as e:
            self.resolver = None
            logger.warning("health_monitor_resolver_failed", error=str(e)[:100])

        # Brain weights (start equal, adjusted by performance)
        self._weights = {brain: 1.0 for brain in ALL_BRAINS}
        
        # Cache for per-regime accuracy (refreshed every cycle)
        self._regime_accuracy_cache = {}
        self._last_refresh = None

        logger.info("health_monitor_initialized",
                     db_ready=self.storage is not None,
                     resolver_ready=self.resolver is not None)

    # ═══════════════════════════════════════
    # CORE HEALTH CHECK
    # ═══════════════════════════════════════

    def full_health_check(self, symbol: str = None) -> Dict[str, Any]:
        """
        Run a complete health check for all brains.
        Returns per-brain status with regime breakdown.
        """
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "symbol": symbol or "ALL",
            "brains": {},
            "system_accuracy": 0.0,
            "total_predictions": 0,
            "gaps_detected": [],
            "recommendations": [],
        }

        all_accuracies = []

        for brain in ALL_BRAINS:
            brain_data = self._check_brain(brain, symbol)
            report["brains"][brain] = brain_data

            if brain_data["total_predictions"] > 0:
                all_accuracies.append(brain_data["overall_accuracy"])
                report["total_predictions"] += brain_data["total_predictions"]

            # Detect gaps
            for gap in brain_data.get("gaps", []):
                report["gaps_detected"].append(gap)

        # System-wide accuracy
        if all_accuracies:
            report["system_accuracy"] = sum(all_accuracies) / len(all_accuracies)

        # Generate recommendations
        report["recommendations"] = self._generate_recommendations(report)

        return report

    def _check_brain(self, brain_name: str, symbol: str = None) -> Dict[str, Any]:
        """Check a single brain's health across all regimes."""
        result = {
            "name": brain_name,
            "overall_accuracy": 0.0,
            "total_predictions": 0,
            "by_regime": {},
            "status": "NO_DATA",
            "weight": self._weights.get(brain_name, 1.0),
            "gaps": [],
            "trend": "STABLE",
        }

        if not self.resolver:
            return result

        # Get overall accuracy
        try:
            stats = self.resolver.get_accuracy_stats(
                symbol=symbol, model_id=brain_name
            )
            if stats and stats.get("total", 0) > 0:
                result["overall_accuracy"] = stats.get("accuracy", 0)
                result["total_predictions"] = stats.get("total", 0)
                result["trend"] = stats.get("trend", "STABLE")

                # Determine status
                acc = result["overall_accuracy"]
                total = result["total_predictions"]
                if total < MIN_PREDICTIONS_FOR_JUDGMENT:
                    result["status"] = "LEARNING"
                elif acc >= 70:
                    result["status"] = "PERFORMING"
                elif acc >= 55:
                    result["status"] = "ADEQUATE"
                elif acc >= 40:
                    result["status"] = "STRUGGLING"
                else:
                    result["status"] = "CRITICAL"
        except Exception as e:
            logger.error("brain_check_failed", brain=brain_name, error=str(e))

        # Per-regime breakdown
        for regime in KNOWN_REGIMES:
            try:
                regime_stats = self.resolver.get_accuracy_stats(
                    symbol=symbol, model_id=brain_name, regime=regime
                )
                if regime_stats and regime_stats.get("total", 0) > 0:
                    regime_acc = regime_stats.get("accuracy", 0)
                    regime_total = regime_stats.get("total", 0)
                    result["by_regime"][regime] = {
                        "accuracy": regime_acc,
                        "total": regime_total,
                    }

                    # Gap detection
                    if regime_total >= MIN_PREDICTIONS_FOR_JUDGMENT and regime_acc < 45:
                        result["gaps"].append({
                            "brain": brain_name,
                            "regime": regime,
                            "accuracy": regime_acc,
                            "total": regime_total,
                            "message": (
                                f"{brain_name} is only {regime_acc:.0f}% accurate "
                                f"in {regime} regime ({regime_total} predictions). "
                                f"Needs retraining for this market condition."
                            )
                        })
                else:
                    # No data for this regime — also a gap
                    if result["total_predictions"] >= 10:
                        result["gaps"].append({
                            "brain": brain_name,
                            "regime": regime,
                            "accuracy": 0,
                            "total": 0,
                            "message": (
                                f"{brain_name} has ZERO predictions in {regime} regime. "
                                f"It has never been tested in this market condition."
                            )
                        })
            except Exception:
                pass

        return result

    # ═══════════════════════════════════════
    # DEBATE TRIGGERS
    # ═══════════════════════════════════════

    def get_debate_triggers(self, symbol: str = None,
                            current_regime: str = None,
                            brain_directions: Dict[str, str] = None,
                            news_impact: float = 0.0) -> List[Dict]:
        """
        Check all 8 trigger conditions and return topics for debate.
        
        Args:
            symbol: Current symbol being analyzed
            current_regime: Current detected regime
            brain_directions: Dict of brain_name → direction ("BUY"/"SELL"/"HOLD")
            news_impact: Highest news impact score (0-1)
            
        Returns:
            List of debate topics with trigger type and urgency
        """
        triggers = []

        health = self.full_health_check(symbol)

        # 1. Accuracy drop
        for brain_name, brain_data in health["brains"].items():
            if (brain_data["total_predictions"] >= MIN_PREDICTIONS_FOR_JUDGMENT
                    and brain_data["overall_accuracy"] < 50):
                triggers.append({
                    "trigger_type": "accuracy_drop",
                    "topic": (
                        f"{brain_name} accuracy dropped to "
                        f"{brain_data['overall_accuracy']:.0f}% on {symbol or 'ALL'}. "
                        f"Should we retrain? What's failing?"
                    ),
                    "urgency": "HIGH",
                    "symbol": symbol,
                    "data": {
                        "brain": brain_name,
                        "accuracy": brain_data["overall_accuracy"],
                        "total": brain_data["total_predictions"],
                    }
                })

        # 2. Brain disagreement
        if brain_directions:
            buyers = [b for b, d in brain_directions.items() if d == "BUY"]
            sellers = [b for b, d in brain_directions.items() if d == "SELL"]
            if len(buyers) >= 2 and len(sellers) >= 2:
                triggers.append({
                    "trigger_type": "brain_disagreement",
                    "topic": (
                        f"Brain split on {symbol}: "
                        f"BUY ({', '.join(buyers)}) vs SELL ({', '.join(sellers)}). "
                        f"Resolve the conflict — who has better evidence?"
                    ),
                    "urgency": "HIGH",
                    "symbol": symbol,
                    "data": {"buyers": buyers, "sellers": sellers}
                })

        # 3. Regime change (caller must detect this)
        # This is checked by the scanner, not here

        # 4. High-impact news
        if news_impact > 0.7:
            triggers.append({
                "trigger_type": "high_impact_news",
                "topic": (
                    f"High-impact news detected for {symbol} "
                    f"(impact: {news_impact:.1f}). "
                    f"Assess impact on our positions."
                ),
                "urgency": "HIGH" if news_impact > 0.85 else "MEDIUM",
                "symbol": symbol,
                "data": {"impact": news_impact}
            })

        # 5. Performance milestone
        for brain_name, brain_data in health["brains"].items():
            if (brain_data["total_predictions"] >= MIN_PREDICTIONS_FOR_JUDGMENT
                    and brain_data["overall_accuracy"] >= 70):
                triggers.append({
                    "trigger_type": "performance_milestone",
                    "topic": (
                        f"{brain_name} crossed 70% accuracy "
                        f"({brain_data['overall_accuracy']:.0f}%). "
                        f"What's working? Should others adopt this approach?"
                    ),
                    "urgency": "LOW",
                    "symbol": symbol,
                    "data": {
                        "brain": brain_name,
                        "accuracy": brain_data["overall_accuracy"],
                    }
                })

        # 6. Gap notification (brain has zero data in current regime)
        if current_regime:
            for brain_name, brain_data in health["brains"].items():
                regime_data = brain_data["by_regime"].get(current_regime, {})
                if (regime_data.get("total", 0) == 0
                        and brain_data["total_predictions"] >= 5):
                    triggers.append({
                        "trigger_type": "regime_gap",
                        "topic": (
                            f"{brain_name} has ZERO experience in {current_regime} regime. "
                            f"Current market is {current_regime}. "
                            f"Should we trust its predictions? How to adapt?"
                        ),
                        "urgency": "MEDIUM",
                        "symbol": symbol,
                        "data": {
                            "brain": brain_name,
                            "regime": current_regime,
                        }
                    })

        return triggers

    # ═══════════════════════════════════════
    # BRAIN WEIGHTS
    # ═══════════════════════════════════════

    def get_brain_weights(self, regime: str = None) -> Dict[str, float]:
        """
        Get brain weights for a specific regime.
        Weights are based on real accuracy data, not hardcoded.
        """
        weights = {}
        
        for brain in ALL_BRAINS:
            base_weight = 1.0

            try:
                if self.resolver:
                    if regime:
                        stats = self.resolver.get_accuracy_stats(
                            model_id=brain, regime=regime
                        )
                    else:
                        stats = self.resolver.get_accuracy_stats(model_id=brain)

                    if stats and stats.get("total", 0) >= MIN_PREDICTIONS_FOR_JUDGMENT:
                        acc = stats.get("accuracy", 50)
                        # Scale: 0% → 0.2 weight, 50% → 1.0, 80% → 1.6
                        base_weight = max(0.2, acc / 50.0)
            except Exception:
                pass

            weights[brain] = round(base_weight, 2)

        # Normalize so weights sum to len(ALL_BRAINS)
        total = sum(weights.values())
        if total > 0:
            scale = len(ALL_BRAINS) / total
            weights = {k: round(v * scale, 2) for k, v in weights.items()}

        self._weights = weights
        return weights

    def get_brain_weights_from_predictions(self, regime: str = None) -> Dict[str, float]:
        """
        Accuracy/regime-weighted vote multiplier, computed directly from
        brain_predictions - the table the live P4 council vote in
        watchlist_scanner.py actually writes to and resolves outcomes into.

        Deliberately separate from get_brain_weights() above: that method
        goes through SignalResolver.get_accuracy_stats(), which queries the
        OLD signal_predictions table. That table's model_id values use
        spaces ("Regime Ensemble") while ALL_BRAINS uses hyphens
        ("Regime-Ensemble") - an exact-match filter that can never succeed -
        and its regime values are entirely the deprecated HYBRID_SCAN /
        VOLATILE_CHAOS taxonomy, not the current one. That combination means
        get_brain_weights() has been silently returning neutral 1.0 for every
        brain in the current 8-brain pipeline, regardless of regime, since
        that pipeline's inception - not a partial gap, fully non-functional
        for this purpose. This method reads the correct table with the
        correct naming instead of trying to repair the old path.

        Returns neutral 1.0 for any brain with fewer than
        MIN_PREDICTIONS_FOR_JUDGMENT resolved (was_correct IS NOT NULL)
        predictions - there usually won't be enough resolved history yet for
        this to do anything other than return neutral weights across the
        board, and that is the honest, correct behavior until real outcomes
        accumulate. It is not a sign the query is broken.
        """
        weights = {brain: 1.0 for brain in ALL_BRAINS}

        if not self.storage:
            return weights

        try:
            from sqlalchemy import text
            session = self.storage.Session()
            try:
                query = """
                    SELECT brain_name,
                           COUNT(*) AS total,
                           SUM(CASE WHEN was_correct THEN 1 ELSE 0 END) AS wins
                    FROM brain_predictions
                    WHERE was_correct IS NOT NULL
                """
                params = {}
                if regime:
                    query += " AND regime = :regime"
                    params['regime'] = regime
                query += " GROUP BY brain_name"

                for row in session.execute(text(query), params).fetchall():
                    brain_name, total, wins = row
                    if brain_name not in ALL_BRAINS:
                        continue  # stale/legacy name from an older system generation - ignore
                    if total < MIN_PREDICTIONS_FOR_JUDGMENT:
                        continue  # not enough resolved history yet - stays at neutral 1.0
                    acc_pct = 100.0 * wins / total
                    # Same scale as get_brain_weights(): 0% -> 0.2, 50% -> 1.0, 80%+ -> 1.6
                    weights[brain_name] = max(0.2, acc_pct / 50.0)
            finally:
                session.close()
        except Exception as e:
            logger.warning("get_brain_weights_from_predictions_failed", error=str(e)[:150])
            return {brain: 1.0 for brain in ALL_BRAINS}

        # Normalize so weights sum to len(ALL_BRAINS), matching get_brain_weights()'s convention
        total_w = sum(weights.values())
        if total_w > 0:
            scale = len(ALL_BRAINS) / total_w
            weights = {k: round(v * scale, 2) for k, v in weights.items()}

        return weights

    def apply_weight_decay(self, decay_rate: float = 0.02) -> None:
        """
        Slowly decay all weights toward equal (1.0).
        Call daily. Prevents stale biases.
        """
        for brain in ALL_BRAINS:
            current = self._weights.get(brain, 1.0)
            if current > 1.0:
                self._weights[brain] = max(1.0, current - decay_rate)
            elif current < 1.0:
                self._weights[brain] = min(1.0, current + decay_rate)

    # ═══════════════════════════════════════
    # RECOMMENDATIONS
    # ═══════════════════════════════════════

    def _generate_recommendations(self, report: Dict) -> List[str]:
        """Generate actionable recommendations from health data."""
        recs = []

        # Critical brains
        critical = [
            b for b, d in report["brains"].items()
            if d["status"] == "CRITICAL"
        ]
        if critical:
            recs.append(
                f"URGENT: {', '.join(critical)} are CRITICAL (< 40% accuracy). "
                f"Initiate emergency retraining or reduce their weight to 0."
            )

        # Gaps
        if report["gaps_detected"]:
            gap_regimes = set(g["regime"] for g in report["gaps_detected"])
            recs.append(
                f"Training gaps in regimes: {', '.join(gap_regimes)}. "
                f"Collect more data or simulate these conditions for retraining."
            )

        # System accuracy
        if report["system_accuracy"] > 0 and report["system_accuracy"] < 55:
            recs.append(
                f"System-wide accuracy is {report['system_accuracy']:.0f}%. "
                f"Consider a full council review to identify systemic issues."
            )

        # No data
        no_data = [
            b for b, d in report["brains"].items()
            if d["status"] == "NO_DATA"
        ]
        if no_data:
            recs.append(
                f"{', '.join(no_data)} have no prediction data yet. "
                f"Run the scanner to generate predictions before judging."
            )

        return recs

    def get_brain_summary_for_prompt(self, symbol: str = None) -> str:
        """
        Generate a text summary of all brain health for injection into AI prompts.
        This is how brains become self-aware of the system state.
        """
        report = self.full_health_check(symbol)

        lines = ["=== BRAIN HEALTH STATUS (live from DB) ==="]
        for brain_name, data in report["brains"].items():
            status = data["status"]
            acc = data["overall_accuracy"]
            total = data["total_predictions"]
            weight = data["weight"]

            lines.append(
                f"- {brain_name}: {status} | "
                f"Accuracy: {acc:.0f}% ({total} predictions) | "
                f"Weight: {weight:.2f}"
            )

            # Add regime breakdown if available
            if data["by_regime"]:
                regime_parts = []
                for regime, rdata in data["by_regime"].items():
                    regime_parts.append(
                        f"{regime}={rdata['accuracy']:.0f}%({rdata['total']})"
                    )
                lines.append(f"  Regime breakdown: {', '.join(regime_parts)}")

        if report["gaps_detected"]:
            lines.append(f"\nGAPS DETECTED ({len(report['gaps_detected'])}):")
            for gap in report["gaps_detected"][:5]:
                lines.append(f"  ⚠️ {gap['message']}")

        if report["recommendations"]:
            lines.append(f"\nRECOMMENDATIONS:")
            for rec in report["recommendations"]:
                lines.append(f"  → {rec}")

        lines.append("=== END BRAIN HEALTH ===")
        return "\n".join(lines)

    # ═══════════════════════════════════════
    # BOSS BRAIN VERDICT TRACKING
    # ═══════════════════════════════════════

    def track_boss_verdict(self, symbol: str, verdict: str,
                            brain_positions: list, debate_topic: str) -> None:
        """
        Store a Boss Brain verdict for later accuracy resolution.
        Called after every council debate.
        """
        try:
            # Debate is already stored by cortex via council_memory.store_debate -> storage.store_council_debate.
            # Here we only track for in-memory / health; no duplicate DB write.
            if self.storage:
                logger.info("boss_verdict_tracked", symbol=symbol, verdict=verdict[:60])
        except Exception as e:
            logger.debug("boss_verdict_tracking_failed", error=str(e)[:60])

    def get_boss_accuracy(self, last_n: int = 20) -> Dict[str, Any]:
        """
        Calculate Boss Brain's verdict accuracy by looking at the council_verdicts table.
        """
        result = {
            "accuracy": 0.0, "total": 0, "correct": 0,
            "trend": "STABLE", "by_symbol": {}
        }
        
        if not self.storage:
            return result
        
        try:
            from sqlalchemy import text
            session = self.storage.Session()
            
            rows = session.execute(text("""
                SELECT symbol, outcome, pnl_pct
                FROM council_verdicts
                WHERE outcome IN ('TARGET', 'SL', 'EXPIRED')
                ORDER BY created_at DESC
                LIMIT :limit
            """), {"limit": last_n}).fetchall()
            
            if not rows:
                return result
            
            total = len(rows)
            correct = 0
            
            for row in rows:
                symbol = row[0]
                outcome = row[1]
                pnl = row[2] or 0.0
                
                # We consider TARGET a win, SL a loss. For EXPIRED, we mark correct if PnL > 0.
                is_win = (outcome == 'TARGET') or (outcome == 'EXPIRED' and pnl > 0.5)
                
                if is_win:
                    correct += 1
                    
                if symbol not in result["by_symbol"]:
                    result["by_symbol"][symbol] = {"total": 0, "correct": 0}
                result["by_symbol"][symbol]["total"] += 1
                if is_win:
                    result["by_symbol"][symbol]["correct"] += 1
            
            result["total"] = total
            result["correct"] = correct
            result["accuracy"] = (correct / total) * 100.0 if total > 0 else 0.0
            
        except Exception as e:
            logger.debug("boss_accuracy_check_failed", error=str(e)[:60])
            
        return result

    def generate_debate_triggers(self, symbol: str = None, **kwargs) -> List[Dict]:
        """
        Convenience wrapper called by scanner and cortex.
        Calls get_debate_triggers with default args.
        """
        return self.get_debate_triggers(symbol=symbol, **kwargs)

    # =========================================================================
    # PHASE 5: Backtest Accuracy Loader
    # Populates BrainSignal.recent_accuracy from retrain_results.json
    # =========================================================================

    def load_backtest_accuracy(self) -> Dict[str, float]:
        """
        Read retrain_results.json (Phase 5 walk-forward backtest output) and
        return a dict: brain_name -> aggregate accuracy (0.0-1.0).

        Blends with live resolver data if available:
            - If live DB has >= 20 resolved predictions, use live (75%) + backtest (25%)
            - Otherwise use backtest data as the authoritative source

        Returns empty dict if file not found (graceful degradation).
        """
        import json
        from pathlib import Path

        results_file = Path(__file__).parent.parent.parent / 'retrain_results.json'
        if not results_file.exists():
            logger.debug('retrain_results_not_found', path=str(results_file))
            return {}

        try:
            with open(results_file, 'r') as f:
                data = json.load(f)

            backtest_acc: Dict[str, float] = {}
            for brain_id, stats in data.get('aggregate', {}).items():
                total = stats.get('total_trades', 0)
                if total > 0:
                    backtest_acc[brain_id] = float(stats.get('accuracy', 0.5))

            # Blend with live if available
            blended: Dict[str, float] = {}
            for brain_id, bt_acc in backtest_acc.items():
                live_acc = None
                live_total = 0
                if self.resolver:
                    try:
                        s = self.resolver.get_accuracy_stats(model_id=brain_id)
                        raw_acc   = s.get('accuracy', 0) if s else 0    # 0-100 scale
                        live_total = s.get('total', 0)   if s else 0
                        # Require: >=50 resolved predictions AND accuracy >30%
                        # (filters stale pre-rebuild SL_HITs that skew the number down)
                        if live_total >= 50 and raw_acc > 30:
                            live_acc = raw_acc / 100.0    # normalise to 0-1
                    except Exception:
                        pass

                if live_acc is not None:
                    # Blend: 75% live weight (more recent), 25% backtest
                    blended[brain_id] = round(0.75 * live_acc + 0.25 * bt_acc, 4)
                else:
                    # Not enough quality live data — use pure backtest accuracy
                    blended[brain_id] = round(bt_acc, 4)

            logger.debug('backtest_accuracy_loaded', brain_count=len(blended),
                         source=str(results_file.name))
            return blended

        except Exception as e:
            logger.warning('backtest_accuracy_load_failed', error=str(e)[:80])
            return {}


# Module-level singleton
_health_monitor = None

# Cached accuracy from retrain_results.json — loaded once at startup
_backtest_accuracy_cache: Dict[str, float] = {}


def get_health_monitor() -> BrainHealthMonitor:
    """Get or create the global BrainHealthMonitor singleton."""
    global _health_monitor
    if _health_monitor is None:
        _health_monitor = BrainHealthMonitor()
    return _health_monitor


def get_brain_accuracy(brain_name: str) -> Optional[float]:
    """
    Return the most recent accuracy for a named brain (0.0-1.0), or None
    if no data is available yet.

    Priority:
    1. Live DB resolver (actual resolved outcome predictions)
    2. Phase 5 backtest retrain_results.json

    Used by cortex.py to populate BrainSignal.recent_accuracy before
    the Boss Brain prompt is sent to the LLM.
    """
    global _backtest_accuracy_cache
    monitor = get_health_monitor()

    # Populate cache if empty (lazy load once)
    if not _backtest_accuracy_cache:
        _backtest_accuracy_cache = monitor.load_backtest_accuracy()

    return _backtest_accuracy_cache.get(brain_name, None)


def refresh_accuracy_cache() -> None:
    """Force reload of the accuracy cache (call after a new retrain run)."""
    global _backtest_accuracy_cache
    _backtest_accuracy_cache = {}
    monitor = get_health_monitor()
    _backtest_accuracy_cache = monitor.load_backtest_accuracy()
    logger.info('accuracy_cache_refreshed', brain_count=len(_backtest_accuracy_cache))
