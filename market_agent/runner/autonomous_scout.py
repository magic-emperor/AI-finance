"""
Autonomous Scout Service

Keeps the Aegis Brains active on the entire watchlist in the background.
Runs the full loop: Scan → Resolve → Learn → Observe → Research.

The brains are autonomous:
- They know WHEN to train (accuracy drops below threshold)
- They have authority to read news from all sources
- They track which sources perform best
- They consult the boss brain on repeated failures
"""

import time
import structlog
from datetime import datetime
from market_agent.runner.watchlist_scanner import run_watchlist_scan, WATCHLIST
from market_agent.data.storage.postgres import PostgresStorage

logger = structlog.get_logger()


def start_autonomous_scout(interval_minutes: int = 10):
    """
    Main loop for background monitoring.
    Each cycle runs the full Scan → Resolve → Learn pipeline.
    """
    storage = PostgresStorage()

    print("=" * 60)
    print("  AEGIS AUTONOMOUS SCOUT STARTING")
    print(f"  Monitoring {len(WATCHLIST)} symbols in rotation.")
    print(f"  Interval: {interval_minutes} minutes")
    print(f"  Pipeline: SCAN -> RESOLVE -> LEARN -> OBSERVE -> RESEARCH")
    print("=" * 60)

    cycle = 0
    while True:
        cycle += 1
        now = datetime.now().strftime("%H:%M:%S")
        try:
            print(f"\n{'='*60}")
            print(f"  CYCLE {cycle} started at {now}")
            print(f"{'='*60}")

            # Full pipeline (scan + resolve + learn + observe + research)
            run_watchlist_scan()

            next_run = datetime.now().strftime("%H:%M:%S")
            print(f"\n  Cycle {cycle} complete at {next_run}. Sleeping for {interval_minutes}m...")
            time.sleep(interval_minutes * 60)

        except KeyboardInterrupt:
            print("\n  Scout stopped by user.")
            break
        except Exception as e:
            print(f"\n  Loop error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    start_autonomous_scout(interval_minutes=10)
