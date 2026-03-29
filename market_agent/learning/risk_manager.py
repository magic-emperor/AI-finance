"""
Risk Manager — Circuit Breaker + Drawdown Protection

The brain's self-preservation system. Prevents catastrophic losses by:
1. Daily loss limit: Pause signals after cumulative loss > threshold
2. Consecutive loss breaker: Force cooldown after N losses in a row
3. Max concurrent signals: Cap active positions per symbol
4. Regime lockout: Pause trading in regimes with < 30% accuracy

CRITICAL DESIGN: Pausing signals ≠ pausing training.
When circuit breaker fires → ESCALATE training, don't stop it.

Escalation ladder (same-breaker fires N days):
  Day 1: Quick retrain (targeted symbol)
  Day 2: Deep retrain + boss brain consultation
  Day 3+: Human review flag + symbol suspension

All thresholds are SEEDS that self-tune based on historical outcomes.
"""

import json
import os
import structlog
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

logger = structlog.get_logger()


class RiskManager:
    """Real-time risk controller with circuit breakers and self-tuning guards."""

    def __init__(self, config_path: str = None):
        self._config_path = config_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'config', 'guardrails.json'
        )
        self._config = self._load_config()
        self._risk_cfg = self._config.get('risk_management', {})

        # In-memory state (resets on restart, DB state is persistent)
        self._daily_pnl: Dict[str, float] = {}  # {date_str: cumulative_pnl_pct}
        self._consecutive_losses: Dict[str, int] = {}  # {symbol: count}
        self._active_signals: Dict[str, int] = {}  # {symbol: count}
        self._cooldowns: Dict[str, datetime] = {}  # {symbol: cooldown_until}
        self._breaker_history: List[Dict] = []  # [{date, symbol, reason, count}]
        self._regime_lockouts: Dict[str, datetime] = {}  # {regime: lockout_until}

        # Load persistent state from DB if available
        self._load_persistent_state()

    def _load_config(self) -> Dict:
        try:
            with open(self._config_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_config(self, config: Dict):
        try:
            with open(self._config_path, 'w') as f:
                json.dump(config, f, indent=4, default=str)
        except Exception:
            pass

    def _load_persistent_state(self):
        """Load breaker history from DB to track multi-day patterns."""
        try:
            from market_agent.learning.training_persistence import training_db
            # Check if there were breaker events in recent days
            recent = training_db.get_recent_events('circuit_breaker', days=7)
            if recent:
                self._breaker_history = recent
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # CORE: can_trade() — called before every signal
    # ═══════════════════════════════════════════════════════════

    def can_trade(self, symbol: str, regime: str = "UNKNOWN") -> Dict[str, Any]:
        """
        Check if trading is allowed for this symbol right now.

        Returns:
            {
                "allowed": bool,
                "reason": str,
                "action": str,  # "TRADE", "WAIT", "RETRAIN", "ESCALATE", "HUMAN_REVIEW"
                "escalation_level": int  # 0=normal, 1=retrain, 2=deep, 3=human
            }
        """
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")

        # 1. Check cooldown
        if symbol in self._cooldowns and now < self._cooldowns[symbol]:
            remaining = (self._cooldowns[symbol] - now).total_seconds() / 60
            return {
                "allowed": False,
                "reason": f"Cooldown active: {remaining:.0f}min remaining after consecutive losses",
                "action": "WAIT",
                "escalation_level": 0,
            }

        # 2. Check daily loss limit
        max_daily_loss = self._risk_cfg.get('max_daily_loss_pct', 3.0)
        daily_pnl = self._daily_pnl.get(today, 0.0)
        if daily_pnl < -max_daily_loss:
            escalation = self._get_escalation_level(symbol)
            return {
                "allowed": False,
                "reason": f"Daily loss limit hit: {daily_pnl:.1f}% (limit: -{max_daily_loss}%)",
                "action": self._escalation_action(escalation),
                "escalation_level": escalation,
            }

        # 3. Check consecutive losses
        max_consecutive = self._risk_cfg.get('consecutive_loss_limit', 3)
        consec = self._consecutive_losses.get(symbol, 0)
        if consec >= max_consecutive:
            cooldown_min = self._risk_cfg.get('cooldown_minutes', 30)
            self._cooldowns[symbol] = now + timedelta(minutes=cooldown_min)
            self._consecutive_losses[symbol] = 0  # Reset after cooldown starts

            # Record breaker event
            self._record_breaker_event(symbol, f"consecutive_losses_{consec}")
            escalation = self._get_escalation_level(symbol)

            return {
                "allowed": False,
                "reason": f"{consec} consecutive SL hits on {symbol} — {cooldown_min}min cooldown",
                "action": self._escalation_action(escalation),
                "escalation_level": escalation,
            }

        # 4. Check max concurrent signals
        max_concurrent = self._risk_cfg.get('max_concurrent_signals', 5)
        active = self._active_signals.get(symbol, 0)
        if active >= max_concurrent:
            return {
                "allowed": False,
                "reason": f"Max concurrent signals ({max_concurrent}) reached for {symbol}",
                "action": "WAIT",
                "escalation_level": 0,
            }

        # 5. Check regime lockout
        if regime in self._regime_lockouts and now < self._regime_lockouts[regime]:
            return {
                "allowed": False,
                "reason": f"Regime {regime} locked out due to poor accuracy",
                "action": "RETRAIN",
                "escalation_level": 1,
            }

        return {
            "allowed": True,
            "reason": "All checks passed",
            "action": "TRADE",
            "escalation_level": 0,
        }

    # ═══════════════════════════════════════════════════════════
    # EVENT HANDLERS — called by signal_resolver after resolution
    # ═══════════════════════════════════════════════════════════

    def record_outcome(self, symbol: str, resolution_type: str,
                       pnl_pct: float, regime: str = "UNKNOWN"):
        """
        Record a signal outcome. Updates all risk counters.

        Args:
            resolution_type: 'T1_HIT', 'T2_HIT', 'SL_HIT', 'EXPIRED'
            pnl_pct: Percentage P&L (positive for wins, negative for losses)
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # Update daily P&L
        if today not in self._daily_pnl:
            self._daily_pnl[today] = 0.0
        self._daily_pnl[today] += pnl_pct

        # Update consecutive losses
        if resolution_type == 'SL_HIT':
            self._consecutive_losses[symbol] = self._consecutive_losses.get(symbol, 0) + 1
            logger.info("risk_loss_recorded", symbol=symbol,
                       consecutive=self._consecutive_losses[symbol],
                       daily_pnl=f"{self._daily_pnl[today]:.2f}%")
        elif resolution_type in ('T1_HIT', 'T2_HIT'):
            self._consecutive_losses[symbol] = 0  # Reset on win

        # Update active signals count
        if symbol in self._active_signals:
            self._active_signals[symbol] = max(0, self._active_signals[symbol] - 1)

    def record_new_signal(self, symbol: str):
        """Increment active signal count when a new signal is stored."""
        self._active_signals[symbol] = self._active_signals.get(symbol, 0) + 1

    def lock_regime(self, regime: str, hours: float = 4.0):
        """Lock a regime from trading for N hours."""
        self._regime_lockouts[regime] = datetime.utcnow() + timedelta(hours=hours)
        logger.warning("regime_locked", regime=regime, hours=hours)

    # ═══════════════════════════════════════════════════════════
    # ESCALATION SYSTEM
    # ═══════════════════════════════════════════════════════════

    def _get_escalation_level(self, symbol: str) -> int:
        """
        How many days has the breaker fired for this symbol recently?
        Day 1 → level 1 (retrain)
        Day 2 → level 2 (deep retrain + boss)
        Day 3+ → level 3 (human review + suspend)
        """
        now = datetime.utcnow()
        recent_days = set()
        for event in self._breaker_history:
            evt_symbol = event.get('symbol', '')
            evt_date = event.get('date', '')
            if evt_symbol == symbol:
                try:
                    evt_dt = datetime.fromisoformat(evt_date) if isinstance(evt_date, str) else evt_date
                    if (now - evt_dt).days <= 5:
                        recent_days.add(evt_dt.strftime("%Y-%m-%d"))
                except Exception:
                    pass
        return min(len(recent_days), 3)

    def _escalation_action(self, level: int) -> str:
        if level <= 0:
            return "WAIT"
        elif level == 1:
            return "RETRAIN"
        elif level == 2:
            return "ESCALATE"
        else:
            return "HUMAN_REVIEW"

    def _record_breaker_event(self, symbol: str, reason: str):
        """Persist breaker event to history and DB."""
        event = {
            "date": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "reason": reason,
        }
        self._breaker_history.append(event)

        try:
            from market_agent.learning.training_persistence import training_db
            training_db.log_event('circuit_breaker', event)
        except Exception:
            pass

    def trigger_escalation(self, symbol: str, level: int):
        """
        Execute the escalation action.
        Called by signal_engine or watchlist_scanner when can_trade() returns escalation.

        Does NOT pause training — it INCREASES training urgency.
        """
        logger.warning("escalation_triggered", symbol=symbol, level=level)

        if level >= 1:
            # Level 1: Quick retrain on this symbol
            try:
                from market_agent.training.training_scheduler import training_scheduler
                training_scheduler.accuracy_triggered_train(symbol)
            except Exception as e:
                logger.error("escalation_retrain_failed", error=str(e)[:100])

        if level >= 2:
            # Level 2: Consult boss brain
            try:
                from market_agent.data.storage.postgres import PostgresStorage
                from market_agent.learning.evaluator import RegretEngine
                storage = PostgresStorage()
                regret = RegretEngine(storage)
                regret.consult_boss_brain(
                    model_name=f"Aegis-{symbol.replace('.', '-')}",
                    failure_timestamp=datetime.utcnow()
                )
            except Exception:
                pass

        if level >= 3:
            # Level 3: Flag for human review
            logger.critical("HUMAN_REVIEW_REQUIRED", symbol=symbol,
                          message=f"Breaker fired 3+ days. {symbol} performance degrading.")

    # ═══════════════════════════════════════════════════════════
    # SELF-TUNING GUARDRAILS
    # ═══════════════════════════════════════════════════════════

    def self_tune(self):
        """
        Adjust guardrail thresholds based on recent outcomes.
        Called daily after market close.

        Logic:
        - If breaker fires every day → threshold too tight → widen by 10%
        - If breaker never fires but accuracy < 40% → too loose → tighten by 10%
        - Bounds: max_daily_loss between 1% and 10%
        - Bounds: consecutive_loss_limit between 2 and 7
        """
        config = self._load_config()
        risk_cfg = config.get('risk_management', {})

        # Count breaker fires in last 7 days
        now = datetime.utcnow()
        recent_fires = sum(
            1 for e in self._breaker_history
            if (now - datetime.fromisoformat(e['date'])).days <= 7
        ) if self._breaker_history else 0

        # Get recent accuracy
        try:
            from market_agent.data.storage.postgres import PostgresStorage
            from market_agent.learning.signal_resolver import SignalResolver
            storage = PostgresStorage()
            resolver = SignalResolver(storage)
            stats = resolver.get_accuracy_stats(last_n=50)
            accuracy = stats.get('accuracy', 50.0)
        except Exception:
            accuracy = 50.0

        adjusted = False

        # Daily loss limit adjustment
        current_limit = risk_cfg.get('max_daily_loss_pct', 3.0)
        if recent_fires >= 5:
            # Fires too often → widen
            new_limit = min(10.0, current_limit * 1.1)
            risk_cfg['max_daily_loss_pct'] = round(new_limit, 1)
            adjusted = True
        elif recent_fires == 0 and accuracy < 40:
            # Never fires but accuracy is bad → tighten
            new_limit = max(1.0, current_limit * 0.9)
            risk_cfg['max_daily_loss_pct'] = round(new_limit, 1)
            adjusted = True

        # Consecutive loss limit adjustment
        current_consec = risk_cfg.get('consecutive_loss_limit', 3)
        if recent_fires >= 5 and current_consec <= 3:
            risk_cfg['consecutive_loss_limit'] = min(7, current_consec + 1)
            adjusted = True
        elif recent_fires == 0 and accuracy < 40 and current_consec >= 4:
            risk_cfg['consecutive_loss_limit'] = max(2, current_consec - 1)
            adjusted = True

        if adjusted:
            config['risk_management'] = risk_cfg
            config['risk_management']['last_tuned'] = now.isoformat()
            self._save_config(config)
            self._risk_cfg = risk_cfg
            logger.info("guardrails_self_tuned",
                       daily_limit=risk_cfg.get('max_daily_loss_pct'),
                       consec_limit=risk_cfg.get('consecutive_loss_limit'),
                       recent_fires=recent_fires, accuracy=accuracy)

    def get_status(self) -> Dict[str, Any]:
        """Dashboard-friendly status summary."""
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return {
            "daily_pnl": self._daily_pnl.get(today, 0.0),
            "max_daily_loss": self._risk_cfg.get('max_daily_loss_pct', 3.0),
            "consecutive_losses": dict(self._consecutive_losses),
            "active_cooldowns": {
                k: v.isoformat() for k, v in self._cooldowns.items()
                if v > datetime.utcnow()
            },
            "active_signals": dict(self._active_signals),
            "regime_lockouts": {
                k: v.isoformat() for k, v in self._regime_lockouts.items()
                if v > datetime.utcnow()
            },
            "breaker_fires_7d": len([
                e for e in self._breaker_history
                if (datetime.utcnow() - datetime.fromisoformat(e['date'])).days <= 7
            ]) if self._breaker_history else 0,
        }


# Module-level singleton
_risk_manager = None

def get_risk_manager() -> RiskManager:
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager
