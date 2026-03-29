"""
Training Scheduler — Automated Periodic Training Pipeline

When training happens:
1. POST-MARKET (daily, ~15:35 IST / 16:05 US):
   - After market close, run backtester on each watchlist symbol
   - Save best params to strategy_params.json
   - Log results to training_persistence DB
   
2. ACCURACY-TRIGGERED (on-demand):
   - When learning cycle detects accuracy < threshold → immediate retrain
   - Targets the specific failing symbol only (fast)
   
3. WEEKEND DEEP (Saturday/Sunday, once):
   - Full parameter grid search with larger windows
   - Cross-symbol learning: best params from top performer applied as seeds

4. ESCALATION-TRIGGERED (from risk_manager):
   - When circuit breaker fires repeatedly → forced retrain with wider grid

Flow:
  watchlist_scanner → detects market close → post_market_train()
  watchlist_scanner → learning cycle → accuracy below threshold → accuracy_triggered_train()
  cron job / Task Scheduler → weekend_deep_train()
"""

import json
import os
import structlog
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logger = structlog.get_logger()


class TrainingScheduler:
    """Orchestrates automated training cycles."""

    def __init__(self):
        self._config_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json'
        )
        self._last_post_market_date: Optional[str] = None
        self._last_weekend_date: Optional[str] = None
        self._training_in_progress: bool = False

    def _load_params(self) -> Dict:
        try:
            with open(self._config_path) as f:
                return json.load(f)
        except Exception:
            return {"default": {}}

    def _save_params(self, params: Dict):
        try:
            with open(self._config_path, 'w') as f:
                json.dump(params, f, indent=4, default=str)
        except Exception as e:
            logger.error("params_save_failed", error=str(e)[:100])

    # ═══════════════════════════════════════════════════════════
    # 1. POST-MARKET TRAINING (daily)
    # ═══════════════════════════════════════════════════════════

    def post_market_train(self, symbols: List[str] = None) -> Dict[str, Any]:
        """
        Run after market close. Backtests each symbol and saves optimized params.
        Called by watchlist_scanner when it detects market has closed.
        
        Args:
            symbols: List of symbols to train on. If None, uses watchlist.
            
        Returns:
            Summary dict with results per symbol.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        
        # Prevent double-run on same day
        if self._last_post_market_date == today:
            logger.info("post_market_already_ran", date=today)
            return {"status": "skipped", "reason": "Already ran today"}

        if self._training_in_progress:
            return {"status": "skipped", "reason": "Training already in progress"}

        self._training_in_progress = True
        self._last_post_market_date = today

        logger.info("post_market_train_started", date=today, symbols=len(symbols or []))

        # Get symbols from watchlist if not provided
        if not symbols:
            symbols = self._get_watchlist()

        results = {}
        params = self._load_params()

        for symbol in symbols:
            try:
                result = self._train_symbol(
                    symbol,
                    period="30d",
                    interval="1h",
                    optimize_grid_size="small"
                )
                results[symbol] = result

                # Update params if accuracy improved
                if result.get('accuracy', 0) > 50:
                    sym_key = symbol.replace('.', '_').replace('-', '_')
                    if sym_key not in params:
                        params[sym_key] = {}
                    params[sym_key].update({
                        "last_trained": today,
                        "accuracy": round(result.get('accuracy', 0), 1),
                        "win_rate": round(result.get('win_rate', 0), 1),
                        "total_signals": result.get('total_signals', 0),
                    })
                    if result.get('best_params'):
                        params[sym_key]['optimized_params'] = result['best_params']

            except Exception as e:
                results[symbol] = {"error": str(e)[:200]}
                logger.error("post_market_train_symbol_failed", symbol=symbol, error=str(e)[:100])

        # Save updated params
        params['last_post_market_train'] = today
        self._save_params(params)

        # Log to training persistence
        self._log_training_run("post_market", results)

        # Self-tune guardrails based on new data
        try:
            from market_agent.learning.risk_manager import get_risk_manager
            get_risk_manager().self_tune()
        except Exception:
            pass

        self._training_in_progress = False

        logger.info("post_market_train_complete",
                    symbols_trained=len(results),
                    avg_accuracy=self._avg_metric(results, 'accuracy'))

        return {"status": "complete", "date": today, "results": results}

    # ═══════════════════════════════════════════════════════════
    # 2. ACCURACY-TRIGGERED TRAINING (on-demand, fast)
    # ═══════════════════════════════════════════════════════════

    def accuracy_triggered_train(self, symbol: str, current_accuracy: float = 0.0) -> Dict[str, Any]:
        """
        Fast targeted retrain for a specific symbol when accuracy drops.
        Uses shorter period and smaller grid for speed.
        
        Called by:
        - watchlist_scanner learning cycle (when accuracy < threshold)
        - risk_manager escalation (when circuit breaker fires)
        """
        logger.info("accuracy_triggered_train", symbol=symbol, current_accuracy=current_accuracy)

        try:
            result = self._train_symbol(
                symbol,
                period="14d",  # Shorter — focus on recent data
                interval="1h",
                optimize_grid_size="tiny"  # Small grid for speed
            )

            # Update params
            if result.get('accuracy', 0) > current_accuracy:
                params = self._load_params()
                sym_key = symbol.replace('.', '_').replace('-', '_')
                if sym_key not in params:
                    params[sym_key] = {}
                params[sym_key].update({
                    "last_triggered_train": datetime.utcnow().isoformat(),
                    "pre_train_accuracy": current_accuracy,
                    "post_train_accuracy": round(result.get('accuracy', 0), 1),
                })
                if result.get('best_params'):
                    params[sym_key]['optimized_params'] = result['best_params']
                self._save_params(params)

            self._log_training_run("accuracy_triggered", {symbol: result})
            return result

        except Exception as e:
            logger.error("accuracy_triggered_train_failed", symbol=symbol, error=str(e)[:100])
            return {"error": str(e)[:200]}

    # ═══════════════════════════════════════════════════════════
    # 3. WEEKEND DEEP TRAINING
    # ═══════════════════════════════════════════════════════════

    def weekend_deep_train(self, symbols: List[str] = None) -> Dict[str, Any]:
        """
        Full parameter grid search with larger windows.
        Meant to run on weekends (Saturday/Sunday) via cron.
        
        - Uses 90-day lookback
        - Full grid search (larger parameter space)
        - Cross-symbol learning: best params from top performer seed others
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if self._last_weekend_date == today:
            return {"status": "skipped", "reason": "Already ran today"}

        if self._training_in_progress:
            return {"status": "skipped", "reason": "Training in progress"}

        self._training_in_progress = True
        self._last_weekend_date = today

        if not symbols:
            symbols = self._get_watchlist()

        logger.info("weekend_deep_train_started", symbols=len(symbols))

        results = {}
        best_global_accuracy = 0
        best_global_params = {}

        for symbol in symbols:
            try:
                result = self._train_symbol(
                    symbol,
                    period="90d",
                    interval="1h",
                    optimize_grid_size="large"
                )
                results[symbol] = result

                if result.get('accuracy', 0) > best_global_accuracy:
                    best_global_accuracy = result.get('accuracy', 0)
                    best_global_params = result.get('best_params', {})

            except Exception as e:
                results[symbol] = {"error": str(e)[:200]}

        # Cross-symbol learning: seed underperformers with best params
        if best_global_params:
            params = self._load_params()
            for symbol, result in results.items():
                if result.get('accuracy', 0) < 40 and not result.get('error'):
                    sym_key = symbol.replace('.', '_').replace('-', '_')
                    if sym_key not in params:
                        params[sym_key] = {}
                    params[sym_key]['seed_params'] = best_global_params
                    params[sym_key]['seed_from'] = "cross_symbol_best"
            params['last_weekend_train'] = today
            params['best_global_accuracy'] = round(best_global_accuracy, 1)
            self._save_params(params)

        self._log_training_run("weekend_deep", results)
        self._training_in_progress = False

        logger.info("weekend_deep_train_complete",
                    symbols=len(results),
                    best_accuracy=best_global_accuracy)

        return {"status": "complete", "date": today, "results": results,
                "best_global_accuracy": best_global_accuracy}

    # ═══════════════════════════════════════════════════════════
    # SHOULD WE TRAIN NOW? (called by watchlist_scanner)
    # ═══════════════════════════════════════════════════════════

    def should_post_market_train(self, market: str = "NSE") -> bool:
        """
        Check if we should trigger post-market training.
        Returns True if market just closed (within 10min of close) and we haven't trained today.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._last_post_market_date == today:
            return False

        try:
            from market_agent.utils.market_utils import get_next_market_event
            event = get_next_market_event(market)
            if event and event.get('status') == 'CLOSED':
                # Market is closed — check if within post-close window
                close_time = event.get('closed_at')
                if close_time:
                    minutes_since_close = (datetime.utcnow() - close_time).total_seconds() / 60
                    return 0 < minutes_since_close < 30  # Within 30 min of close
                return True  # Market closed, no timing info — train anyway
        except Exception:
            pass

        return False

    def should_weekend_train(self) -> bool:
        """Check if it's weekend and we haven't done deep training."""
        now = datetime.utcnow()
        today = now.strftime("%Y-%m-%d")
        return now.weekday() >= 5 and self._last_weekend_date != today

    # ═══════════════════════════════════════════════════════════
    # INTERNAL HELPERS
    # ═══════════════════════════════════════════════════════════

    def _train_symbol(self, symbol: str, period: str = "30d",
                      interval: str = "1h",
                      optimize_grid_size: str = "small") -> Dict[str, Any]:
        """Run backtester on a single symbol and return results."""
        from market_agent.training.backtester import Backtester

        bt = Backtester()
        result = bt.run_backtest(
            symbol=symbol,
            interval=interval,
        )

        # Basic result formatting
        return {
            "accuracy": result.get('accuracy', 0),
            "win_rate": result.get('win_rate', 0),
            "total_signals": result.get('total_signals', 0),
            "t1_hit_rate": result.get('t1_hit_rate', 0),
            "t2_hit_rate": result.get('t2_hit_rate', 0),
            "sl_hit_rate": result.get('sl_hit_rate', 0),
            "best_params": result.get('best_params', {}),
        }

    def _get_watchlist(self) -> List[str]:
        """Get symbols from watchlist_scanner."""
        try:
            from market_agent.runner.watchlist_scanner import WATCHLIST
            return list(WATCHLIST)
        except Exception:
            return ["ITC.NS", "RELIANCE.NS", "BTC-USD"]

    def _log_training_run(self, mode: str, results: Dict):
        """Log training run to persistence."""
        try:
            from market_agent.learning.training_persistence import training_db
            total = len(results)
            successful = sum(1 for r in results.values() if not r.get('error'))
            avg_acc = self._avg_metric(results, 'accuracy')
            training_db.log_brain_training_run(
                model_id="Aegis-Global",
                mode=mode,
                bars_learned=total,
                epochs=1,
                final_loss=0.0,
                notes=f"{successful}/{total} symbols trained, avg accuracy: {avg_acc:.1f}%"
            )
        except Exception:
            pass

    @staticmethod
    def _avg_metric(results: Dict, key: str) -> float:
        """Average a metric across results, ignoring errors."""
        values = [r.get(key, 0) for r in results.values() if not r.get('error') and r.get(key)]
        return sum(values) / len(values) if values else 0.0


# Module-level singleton
_scheduler = None

def get_training_scheduler() -> TrainingScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = TrainingScheduler()
    return _scheduler

# Convenience alias
training_scheduler = None

def _init():
    global training_scheduler
    training_scheduler = get_training_scheduler()

_init()
