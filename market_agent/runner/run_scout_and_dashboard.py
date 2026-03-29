"""
Single command to run both the Autonomous Scout and the Streamlit Dashboard.

Usage:
  python -m market_agent.runner.run_scout_and_dashboard

This starts:
  1. Streamlit dashboard (market_agent/dashboard/app.py) — main process (browser opens)
  2. Autonomous scout in a background thread — runs scan every 10 minutes
  3. Daily jobs thread — runs daily report + daily training once per day (no Windows Task Scheduler needed)

When you Ctrl+C in the terminal, both stop. The scout keeps the DB updated so the
dashboard shows fresh accuracy, debates, and signal history.

Daily report/training time: set REPORT_UTC_HOUR (0-23) and optionally REPORT_UTC_MINUTE (default 0).
Example: REPORT_UTC_HOUR=10 REPORT_UTC_MINUTE=30 → 10:30 UTC (e.g. 4:00 PM IST).
If you prefer local time, set DAILY_REPORT_HOUR=16 (4 PM local) and DAILY_REPORT_MINUTE=0.
"""

import os
import sys
import time
import threading
from pathlib import Path
from datetime import datetime

# Project root (parent of market_agent)
ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)


def scout_loop(interval_minutes: int, stop: threading.Event):
    from market_agent.runner.watchlist_scanner import run_watchlist_scan
    cycle = 0
    while not stop.is_set():
        cycle += 1
        try:
            print(f"  [Scout] Cycle {cycle} running...", flush=True)
            run_watchlist_scan()
        except Exception as e:
            print(f"  [Scout] Error: {e}", flush=True)
        for _ in range(interval_minutes * 60):
            if stop.is_set():
                return
            time.sleep(1)


def daily_jobs_loop(stop: threading.Event):
    """Run daily report and daily training once per day at configured time (no Windows password needed)."""
    last_run_date = None
    # Use local time by default (DAILY_REPORT_HOUR=16 → 4 PM), or UTC if REPORT_UTC_HOUR set
    use_utc = "REPORT_UTC_HOUR" in os.environ
    hour = int(os.getenv("REPORT_UTC_HOUR", os.getenv("DAILY_REPORT_HOUR", "16")))
    minute = int(os.getenv("REPORT_UTC_MINUTE", os.getenv("DAILY_REPORT_MINUTE", "0")))

    while not stop.is_set():
        now = datetime.utcnow() if use_utc else datetime.now()
        today = now.date()
        if now.hour == hour and now.minute >= minute and last_run_date != today:
            try:
                print("  [Daily] Running daily report...", flush=True)
                from market_agent.runner.daily_report import generate_daily_report
                generate_daily_report()
                print("  [Daily] Running daily training...", flush=True)
                from market_agent.runner.run_daily_training import _default_symbols
                symbols = _default_symbols()
                from market_agent.training.backtester import backtester
                strategy = os.getenv("DAILY_TRAINING_STRATEGY", "Intraday (Scalp)")
                backtester.train_and_save(symbols, strategy)
                last_run_date = today
                print("  [Daily] Report and training done.", flush=True)
            except Exception as e:
                print(f"  [Daily] Error: {e}", flush=True)
        for _ in range(60):  # check every minute
            if stop.is_set():
                return
            time.sleep(1)


def main():
    interval_minutes = int(os.getenv("SCOUT_INTERVAL_MINUTES", "10"))
    app_path = "market_agent/dashboard/app.py"
    run_daily_jobs = os.getenv("RUN_DAILY_JOBS", "true").lower() in ("1", "true", "yes")

    print("=" * 60)
    print("  AEGIS — Scout + Dashboard (single command)")
    print("=" * 60)
    print("  Dashboard: starting Streamlit (browser will open)")
    print("  Scout: running in background every", interval_minutes, "minutes")
    if run_daily_jobs:
        print("  Daily jobs: report + training once per day (set RUN_DAILY_JOBS=false to disable)")
    print("  Press Ctrl+C to stop both.")
    print("=" * 60)

    stop = threading.Event()
    t = threading.Thread(target=scout_loop, args=(interval_minutes, stop), daemon=True)
    t.start()
    if run_daily_jobs:
        t2 = threading.Thread(target=daily_jobs_loop, args=(stop,), daemon=True)
        t2.start()

    try:
        import subprocess
        subprocess.run(
            [sys.executable, "-m", "streamlit", "run", app_path],
            cwd=str(ROOT),
            env=os.environ.copy(),
        )
    finally:
        stop.set()


if __name__ == "__main__":
    main()
