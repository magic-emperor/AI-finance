"""
Run historical (backtest) training daily — e.g. via cron, Task Scheduler, or in-process scheduler.

Usage:
  python -m market_agent.runner.run_daily_training

When DAILY_TRAINING_SYMBOLS is not set, uses the watchlist from watchlist_scanner (first 8 symbols
for a reasonable runtime; full list can make backtest slow).
Optional env:
  DAILY_TRAINING_SYMBOLS  — comma-separated; if unset, uses WATCHLIST[:8]
  DAILY_TRAINING_STRATEGY — "Intraday (Scalp)" or "Swing (Hold)"
"""

import os
from datetime import datetime

def _default_symbols():
    try:
        from market_agent.runner.watchlist_scanner import WATCHLIST
        return WATCHLIST[:8]  # First 8 for reasonable daily runtime
    except Exception:
        return ["ITC.NS", "RELIANCE.NS", "BTC-USD"]

if __name__ == "__main__":
    symbols_str = os.getenv("DAILY_TRAINING_SYMBOLS", "")
    strategy = os.getenv("DAILY_TRAINING_STRATEGY", "Intraday (Scalp)")
    symbols = [s.strip() for s in symbols_str.split(",") if s.strip()] if symbols_str else _default_symbols()

    print("=" * 60)
    print("  DAILY HISTORICAL TRAINING")
    print(f"  Date: {datetime.now().isoformat()}")
    print(f"  Symbols: {symbols}")
    print(f"  Strategy: {strategy}")
    print("=" * 60)

    from market_agent.training.backtester import backtester
    result = backtester.train_and_save(symbols, strategy)

    print("\n  Result:", result.get("best_accuracy"), "% best accuracy")
    print("  Config updated:", result.get("config_updated", False))
    print("=" * 60)
