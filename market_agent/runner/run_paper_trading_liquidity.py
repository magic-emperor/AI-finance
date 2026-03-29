"""
run_paper_trading.py
=====================
Liquidity-Sweep Brain v5 — Paper Trading Runner
================================================

WHAT THIS DOES:
  Runs the brain once per hour on each symbol.
  Logs every decision in plain English.
  Tracks open positions in a trade ledger (prevents double-entry).
  Monitors open trades for target/stop hits.
  Produces a daily performance summary.

CYCLE: Once per hour, call this script (via scheduler or cron).
  Each run takes ~2-5 seconds per symbol.
  Total per cycle: ~20-40 seconds for 8 symbols.

DATA REQUIRED PER CYCLE:
  - Last 300 H1 bars per symbol from DB (~37 trading days)
  - Regime is computed fresh each cycle (pre-computed in grid, live here)
  - Minimum 200 bars or brain returns HOLD

TRADE STATE MEMORY (Level 1 memory):
  Open positions tracked in a JSON ledger file.
  Brain checks ledger before deciding — won't enter if already in trade.
  Each cycle also checks if open trades have hit target or stop.

HOW TO RUN:
  # Single cycle (called by scheduler every hour):
  python -X utf8 -m market_agent.runner.run_paper_trading

  # Or run once manually for testing:
  python -X utf8 run_paper_trading.py

LOCKED PARAMETERS (from 270d grid validation):
  vol_spike_mult=1.5, min_level_dist_atr=0.3, pivot_bars=2, min_level_age_bars=5
  270d evidence: n=17, WR=58.8%, EV=1.337R, MaxDD=2R
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from market_agent.brain.liquidity_sweep import liquidity_sweep_signal
from market_agent.brain.brain_reasoning_logger import explain_signal, summarise_signal
from market_agent.brain.regime_ensemble import regime_ensemble_signal
from market_agent.runner.backtester import load_ohlcv
from market_agent.data.storage.postgres import PostgresStorage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)
log = logging.getLogger('paper_trading')


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION — LOCKED FROM 270d GRID
# ═══════════════════════════════════════════════════════════════

SYMBOLS = [
    'LT.NS', 'TATASTEEL.NS', 'AAPL', 'RELIANCE.NS',
    'ITC.NS', 'AMD', 'GOOGL', 'NVDA',
]

TIMEFRAME        = '1h'
HIST_BARS        = 300        # bars to load per symbol per cycle (~37 trading days)
MAX_BARS_TRADE   = 20         # max H1 bars before trade expires (same as backtester)
LEDGER_PATH      = Path('paper_trade_ledger.json')
LOG_PATH         = Path('paper_trade_log.txt')
VERBOSE_LOG      = True       # set False for compact one-line logs only


# ═══════════════════════════════════════════════════════════════
# TRADE LEDGER — LEVEL 1 MEMORY (trade state persistence)
# ═══════════════════════════════════════════════════════════════

def load_ledger() -> dict:
    """Load open positions from JSON ledger. Returns empty dict if not found."""
    if LEDGER_PATH.exists():
        try:
            with open(LEDGER_PATH) as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Ledger read error: {e} — starting fresh")
    return {'open_trades': {}, 'closed_trades': [], 'stats': {'wins': 0, 'losses': 0, 'expired': 0}}


def save_ledger(ledger: dict) -> None:
    """Persist ledger to JSON after every cycle."""
    with open(LEDGER_PATH, 'w') as f:
        json.dump(ledger, f, indent=2, default=str)


def is_in_trade(ledger: dict, symbol: str) -> bool:
    """Return True if we already have an open position on this symbol."""
    return symbol in ledger.get('open_trades', {})


def open_trade(ledger: dict, symbol: str, direction: str,
               entry: float, target: float, stop: float,
               bar_time: datetime, signal_summary: str) -> None:
    """Record a new open position in the ledger."""
    ledger['open_trades'][symbol] = {
        'direction':   direction,
        'entry':       entry,
        'target':      target,
        'stop_loss':   stop,
        'open_time':   str(bar_time),
        'bars_open':   0,
        'signal':      signal_summary,
    }
    log.info(f"  LEDGER: Opened {direction} trade on {symbol} "
             f"entry={entry:.4g} T1={target:.4g} SL={stop:.4g}")


def update_open_trades(ledger: dict, data_cache: dict) -> list:
    """
    Check each open trade against latest prices.
    Returns list of closed trade summaries.
    """
    closed = []
    to_remove = []

    for symbol, trade in ledger.get('open_trades', {}).items():
        if symbol not in data_cache:
            continue

        df         = data_cache[symbol]
        direction  = trade['direction']
        entry      = trade['entry']
        target     = trade['target']
        stop_loss  = trade['stop_loss']
        bars_open  = trade.get('bars_open', 0) + 1
        trade['bars_open'] = bars_open

        # Get the most recent bar
        if df.empty:
            continue

        latest_high  = float(df['High'].iloc[-1])
        latest_low   = float(df['Low'].iloc[-1])
        latest_close = float(df['Close'].iloc[-1])
        latest_time  = str(df.index[-1])

        outcome    = None
        r_achieved = 0.0
        risk       = abs(entry - stop_loss)

        if risk > 0:
            if direction == 'BUY':
                if latest_high >= target:
                    outcome    = 'WIN'
                    r_achieved = abs(target - entry) / risk
                elif latest_low <= stop_loss:
                    outcome    = 'LOSS'
                    r_achieved = -1.0
            else:  # SELL
                if latest_low <= target:
                    outcome    = 'WIN'
                    r_achieved = abs(entry - target) / risk
                elif latest_high >= stop_loss:
                    outcome    = 'LOSS'
                    r_achieved = -1.0

        if bars_open >= MAX_BARS_TRADE and outcome is None:
            outcome    = 'EXPIRED'
            r_achieved = (latest_close - entry) / risk if direction == 'BUY' else (entry - latest_close) / risk

        if outcome:
            summary = {
                'symbol':      symbol,
                'direction':   direction,
                'entry':       entry,
                'target':      target,
                'stop_loss':   stop_loss,
                'open_time':   trade['open_time'],
                'close_time':  latest_time,
                'bars_open':   bars_open,
                'outcome':     outcome,
                'r_achieved':  round(r_achieved, 3),
                'signal':      trade.get('signal', ''),
            }
            ledger['closed_trades'].append(summary)

            # Update stats
            stats = ledger.setdefault('stats', {'wins': 0, 'losses': 0, 'expired': 0})
            if outcome == 'WIN':
                stats['wins']    += 1
            elif outcome == 'LOSS':
                stats['losses']  += 1
            else:
                stats['expired'] += 1

            to_remove.append(symbol)
            closed.append(summary)

            emoji = '✅' if outcome == 'WIN' else ('❌' if outcome == 'LOSS' else '⏱')
            log.info(
                f"  TRADE CLOSED {emoji}  {symbol} {direction}  "
                f"outcome={outcome}  R={r_achieved:+.2f}  bars={bars_open}"
            )

    for sym in to_remove:
        del ledger['open_trades'][sym]

    return closed


# ═══════════════════════════════════════════════════════════════
# PLAIN-ENGLISH LOG WRITER
# ═══════════════════════════════════════════════════════════════

def _write_log(text: str) -> None:
    """Append to the plain-English log file and also log to console."""
    with open(LOG_PATH, 'a', encoding='utf-8') as f:
        f.write(text + '\n')


# ═══════════════════════════════════════════════════════════════
# MAIN CYCLE
# ═══════════════════════════════════════════════════════════════

def _is_market_open(now: datetime) -> bool:
    """
    Return True if at least one of our markets is currently open.
    NSE India:  Mon-Fri 03:45-10:00 UTC (09:15-15:30 IST)
    US markets: Mon-Fri 13:30-20:00 UTC (09:30-16:00 ET)
    We use a generous window — if within 30 min of open/close, treat as open.
    On weekends return False immediately.
    """
    if now.weekday() >= 5:   # Saturday=5, Sunday=6
        return False
    hour_utc = now.hour + now.minute / 60.0
    # NSE: 03:15-10:30 UTC (with buffer)
    nse_open = 3.25 <= hour_utc <= 10.5
    # US:  13:00-20:30 UTC (with buffer)
    us_open  = 13.0 <= hour_utc <= 20.5
    return nse_open or us_open


def run_cycle() -> None:
    """
    One full paper trading cycle:
      1. Check if any market is open — skip if weekend/holiday
      2. Load latest data for all symbols (90 calendar days = ~300 trading bars)
      3. Check open trades for target/stop hits
      4. Run brain on each symbol
      5. Log every decision in plain English
      6. Open new positions if signal fires and not already in trade
      7. Save ledger
    """
    now     = datetime.now(tz=None)  # local time for scheduling, UTC for display
    now_utc = datetime.utcnow()
    storage = PostgresStorage()
    ledger  = load_ledger()

    # ── Market-open check ─────────────────────────────────────────────────────
    if not _is_market_open(now_utc):
        day_name = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][now_utc.weekday()]
        log.info(
            f"Markets closed ({day_name} {now_utc.strftime('%H:%M')} UTC) — "
            f"skipping cycle. Brain runs only during market hours."
        )
        return

    log.info("=" * 68)
    log.info(f"PAPER TRADING CYCLE  {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Open positions: {list(ledger.get('open_trades', {}).keys()) or 'none'}")
    log.info("=" * 68)

    _write_log(f"\n{'='*68}")
    _write_log(f"CYCLE: {now_utc.strftime('%Y-%m-%d %H:%M UTC')}")
    _write_log(f"Open trades: {list(ledger.get('open_trades', {}).keys()) or 'none'}")
    _write_log(f"{'='*68}")

    # ── Load data — use calendar days, NOT hours ───────────────────────────────
    # Bug fix: HIST_BARS=300 H1 trading bars needs ~90 calendar days
    # (300 bars ÷ ~6-7 bars/trading day ÷ ~5 trading days/week × 7 = ~90 days)
    # Using hours was loading only 12.5 days = ~75 bars — not enough for brain.
    end   = now_utc
    start = end - timedelta(days=90)

    data_cache: dict = {}
    for sym in SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, start, end, storage)
        if df.empty:
            log.warning(f"  {sym}: NO DATA — skipping")
        else:
            data_cache[sym] = df
            log.info(
                f"  {sym}: {len(df)} bars loaded  "
                f"({df.index[0].strftime('%b %d')} → {df.index[-1].strftime('%b %d')})  "
                f"last close: {df['Close'].iloc[-1]:.4g}"
            )

    # ── Check open trades ─────────────────────────────────────────────────────
    if ledger.get('open_trades'):
        log.info("\nChecking open positions...")
        closed = update_open_trades(ledger, data_cache)
        if closed:
            for c in closed:
                msg = (
                    f"\n📊 TRADE RESULT: {c['symbol']} {c['direction']}\n"
                    f"  Entry: {c['entry']:.4g}  Target: {c['target']:.4g}  "
                    f"Stop: {c['stop_loss']:.4g}\n"
                    f"  Outcome: {c['outcome']}  R achieved: {c['r_achieved']:+.2f}\n"
                    f"  Duration: {c['bars_open']} bars  "
                    f"({c['open_time']} → {c['close_time']})"
                )
                _write_log(msg)
        save_ledger(ledger)

    # ── Run brain on each symbol ───────────────────────────────────────────────
    log.info("\nRunning brain on all symbols...")
    signals_fired = 0

    for sym in SYMBOLS:
        if sym not in data_cache:
            continue

        df = data_cache[sym]
        # Bug fix: skip threshold raised from 100 → 200 to match brain's _MIN_HIST_BARS
        if len(df) < 200:
            log.info(f"  {sym}: skipped — insufficient data ({len(df)} bars, need 200)")
            continue

        # Get regime
        try:
            regime_sig = regime_ensemble_signal(df)
            regime     = regime_sig.measurements.get('computed_regime', 'RANGING') or 'RANGING'
        except Exception as e:
            log.warning(f"  {sym}: regime error ({e}) — defaulting to RANGING")
            regime = 'RANGING'

        bar_time = df.index[-1]

        # Run brain
        try:
            signal = liquidity_sweep_signal(df, sym, regime)
        except Exception as e:
            log.error(f"  {sym}: brain error: {e}")
            continue

        # Log decision in plain English
        if VERBOSE_LOG:
            explanation = explain_signal(signal, sym, regime, bar_time)
            _write_log(explanation)
            log.info(f"  {sym}: {signal.direction}  conf={signal.confidence:.0%}  regime={regime}")
        else:
            summary = summarise_signal(signal, sym, regime, bar_time)
            _write_log(summary)
            log.info(summary)

        # Act on signal
        if signal.direction in ('BUY', 'SELL'):
            m = signal.measurements or {}

            if is_in_trade(ledger, sym):
                msg = (
                    f"\n⚠️  {sym}: Signal fired ({signal.direction}) but "
                    f"ALREADY IN TRADE — skipping. Brain prevented double-entry."
                )
                log.info(msg)
                _write_log(msg)
                continue

            entry  = m.get('entry_price',  float(df['Close'].iloc[-1]))
            target = m.get('target_1',     entry * 1.02)
            sl     = m.get('stop_loss',    entry * 0.99)

            open_trade(
                ledger, sym, signal.direction, entry, target, sl,
                bar_time, summarise_signal(signal, sym, regime, bar_time),
            )

            action_msg = (
                f"\n🟢 PAPER TRADE OPENED\n"
                f"  Symbol:    {sym}\n"
                f"  Direction: {signal.direction}\n"
                f"  Regime:    {regime}\n"
                f"  Entry:     {entry:.4g}\n"
                f"  Target T1: {target:.4g}\n"
                f"  Stop Loss: {sl:.4g}\n"
                f"  R:R ratio: {m.get('rr_achieved', 0):.1f}:1\n"
                f"  Confidence:{signal.confidence:.0%}\n"
                f"  Evidence:  {signal.primary_evidence}"
            )
            _write_log(action_msg)
            log.info(action_msg)
            signals_fired += 1
            save_ledger(ledger)

    # ── Cycle summary ──────────────────────────────────────────────────────────
    stats = ledger.get('stats', {})
    total_closed = stats.get('wins', 0) + stats.get('losses', 0) + stats.get('expired', 0)
    wr = stats.get('wins', 0) / total_closed if total_closed > 0 else 0.0

    summary_msg = (
        f"\nCYCLE COMPLETE  {now_utc.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"  Signals fired this cycle: {signals_fired}\n"
        f"  Open positions: {len(ledger.get('open_trades', {}))}\n"
        f"  Total closed trades: {total_closed}\n"
        f"  Win rate (all time): {wr:.1%}  "
        f"(W={stats.get('wins',0)} L={stats.get('losses',0)} E={stats.get('expired',0)})"
    )
    log.info(summary_msg)
    _write_log(summary_msg)

    save_ledger(ledger)


# ═══════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    run_cycle()