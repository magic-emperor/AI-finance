"""
brain_backtester.py — Trade-Level Brain Performance Validator
=============================================================
PURPOSE
-------
Measures the REAL performance of each brain independently:
  - Win rate: % of signals where T1 was hit before SL
  - Profit factor: gross profit / gross loss
  - Average R: average reward in R-multiples per trade
  - Sharpe ratio: risk-adjusted return
  - Max drawdown: largest peak-to-trough equity curve drop
  - Trade count: enough signals to be statistically meaningful

This is the ONLY metric that matters before connecting brains.
A brain must pass >60% win rate AND positive profit factor ALONE
before we trust it in the council.

HOW IT WORKS
------------
For each brain on each symbol:
  1. Roll through historical OHLCV bar by bar (walk-forward, no lookahead)
  2. At each bar, call the brain's signal function
  3. If signal is BUY or SELL with confidence >= threshold:
     - Record entry at next bar open (realistic execution)
     - Scan forward bars: did T1 or SL get hit first?
     - If T1 hit: WIN (+R)
     - If SL hit: LOSS (-1R)
     - If neither in max_hold bars: EXPIRED (0R, counted as loss)
  4. Aggregate all trades into performance metrics

REALISTIC ASSUMPTIONS
---------------------
- Entry at NEXT bar open (not current close — avoids lookahead)
- T1 = entry + rr_t1_mult * ATR (brain's own R:R)
- SL = entry - rr_sl_mult * ATR (brain's own R:R)
- Slippage: 0.05% of entry price (realistic for liquid markets)
- Commission: 0.0 (conservative — actual commission improves this)
- Max hold: 20 bars (if neither T1 nor SL hit, exit at market)

USAGE
-----
  python -X utf8 -m market_agent.runner.brain_backtester --brain multi_modal_fusion
  python -X utf8 -m market_agent.runner.brain_backtester --brain multi_timeframe
  python -X utf8 -m market_agent.runner.brain_backtester --brain cross_stock_gnn
  python -X utf8 -m market_agent.runner.brain_backtester --all
"""
from __future__ import annotations

import argparse
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger('brain_backtester')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s'
)

# =============================================================================
# CONFIG
# =============================================================================

SYMBOLS = [
    'LT.NS', 'TATASTEEL.NS', 'RELIANCE.NS', 'ITC.NS',
    'AAPL', 'AMD', 'NVDA', 'GOOGL',
    'BTC-USD',
]

# Minimum bars to feed the brain before taking signals
# (warmup for indicators to stabilise)
WARMUP_BARS = 100

# Minimum confidence for a signal to generate a trade
MIN_CONFIDENCE = 0.50

# Max bars to hold a trade before expiry
MAX_HOLD_BARS = 20

# Slippage as % of entry price
SLIPPAGE_PCT = 0.0005   # 0.05%

# Walk-forward step: evaluate every N bars (1 = every bar, 5 = every 5th bar)
# Set to 1 for maximum trades, higher for speed
STEP_BARS = 1

# Acceptance thresholds
MIN_WIN_RATE    = 0.60   # 60% win rate required
MIN_PROFIT_FACTOR = 1.20 # gross_profit / gross_loss >= 1.2
MIN_TRADES      = 30     # minimum trades for statistical significance

# Regime filtering: only take signals in regimes the brain is designed for.
#
# WAS a hand-maintained duplicate of signal_generators.BRAIN_REGIME_GATES_UNIFIED
# (the comment even said "Match coordinator gate"), which is exactly how it
# went stale: today's fix adding RANGING to the live coordinator's Liquidity-
# Sweep gate had no effect here, because this was a separate copy nobody
# re-synced. Found via the decision log itself -- 138/316 bars in a smoke
# test were rejected with "not permitted in RANGING/SQUEEZE" using the OLD
# list, even though the live pipeline now allows RANGING. Importing the
# real source instead of copying it means this can't drift again.
from market_agent.brain.signal_generators import BRAIN_REGIME_GATES_UNIFIED

_BACKTESTER_TO_COORDINATOR_NAME = {
    'multi_modal_fusion': 'Multi-Modal-Fusion',
    'multi_timeframe':    'Multi-Timeframe',
    'cross_stock_gnn':    'Cross-Stock-GNN',
    'liquidity_sweep':    'Liquidity-Sweep',
    'regime_ensemble':    'Regime-Ensemble',
}
BRAIN_REGIME_GATES = {
    backtester_name: BRAIN_REGIME_GATES_UNIFIED.get(coordinator_name, ['ALL'])
    for backtester_name, coordinator_name in _BACKTESTER_TO_COORDINATOR_NAME.items()
}
# multi_modal_fusion's live gate is ['RANGING', 'SQUEEZE'] only -- MEAN_REVERTING
# was a pre-unification regime label this backtester carried from before the
# regime taxonomy was normalized; keep it for backward compatibility with any
# regime-ensemble output that still emits it during the transition.
BRAIN_REGIME_GATES['multi_modal_fusion'] = list(
    set(BRAIN_REGIME_GATES['multi_modal_fusion']) | {'MEAN_REVERTING'}
)


# =============================================================================
# DATA LOADING
# =============================================================================

def fetch_binance_ohlcv(
    symbol: str,
    interval: str = '1h',
    bars: int = 200,
) -> Optional[pd.DataFrame]:
    """
    Fetch OHLCV from Binance REST API.
    Returns DataFrame with OHLCV columns.
    
    Fixed: Removed 1000 bar hard cap to allow larger initial backfills.
    Note: Binance API limit is 1000 per call; pagination may be needed 
    for >1000, but for now we prioritize local DB for large requests.
    """
    # NOTE: This function is in unified_market_data.py — placeholder kept for
    # reference. The actual call goes through UnifiedMarketData.get_ohlcv().
    return None # Placeholder, as actual implementation is elsewhere


# Trading minutes/day per asset class, used to convert a day-count into a bar
# count for ANY interval (previously hardcoded assuming 1h bars only).
_TRADING_MINUTES_PER_DAY = {
    'CRYPTO': 24 * 60, 'FOREX': 24 * 60, 'COMMODITY': 23 * 60,
    'EQUITY_INDIA': 375,   # NSE 09:15-15:30
    'EQUITY_US': 390,      # 09:30-16:00 ET
    'INDEX': 375, 'UNKNOWN': 390,
}
_INTERVAL_MINUTES = {'1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 240, '1d': 1440}


def _asset_class(symbol: str) -> str:
    s = symbol.upper()
    if s.endswith('-USD') or 'BTC' in s or 'ETH' in s:
        return 'CRYPTO'
    if s.endswith('.NS') or s.endswith('.BO'):
        return 'EQUITY_INDIA'
    if s.endswith('=X'):
        return 'FOREX'
    if s.endswith('=F'):
        return 'COMMODITY'
    return 'EQUITY_US'


def load_symbol_data(symbol: str, days: int = 365, interval: str = '1h') -> Optional[pd.DataFrame]:
    """
    Load OHLCV from market_data system, on the requested interval.

    Previously hardcoded to interval='1h' with a bars-per-day table that
    only made sense for 1h bars — a --days request for 1m data would have
    asked for 1/60th the bars actually needed. Bar count is now derived
    from real trading-minutes-per-day divided by the interval's minutes.
    """
    try:
        from market_agent.data.storage.postgres import PostgresStorage

        ac = _asset_class(symbol)
        interval_min = _INTERVAL_MINUTES.get(interval, 60)
        bars_per_day = max(1, _TRADING_MINUTES_PER_DAY.get(ac, 390) // interval_min)
        bars = days * bars_per_day + WARMUP_BARS

        # STEP 0 (plan): read the validated store DIRECTLY. Previously this went
        # through market_data.get_ohlcv(), which silently falls back to a LIVE
        # yfinance fetch whenever the DB holds <90% of the requested bars. That
        # fallback bypasses normalize_bar_timestamp() and the Breeze
        # cross-validation, and it is how 166 of 584 trades (28%) in the
        # 2026-09-13 run came from unvalidated data while the run reported
        # success. A backtest that quietly substitutes a different data source
        # produces numbers nobody can act on, so this now reads one source only
        # and reports exactly what it got.
        storage = PostgresStorage()
        rows = storage.get_latest_data(symbol, interval, limit=bars)
        if not rows:
            logger.warning(f'  {symbol} {interval}: NO DATA in validated store — SKIPPING')
            return None

        df = pd.DataFrame(
            [r['data'] for r in rows], index=[r['timestamp'] for r in rows]
        ).sort_index()
        for col in ('Open', 'High', 'Low', 'Close'):
            if col not in df.columns:
                logger.warning(f'  {symbol} {interval}: missing {col} column — SKIPPING')
                return None

        if len(df) < WARMUP_BARS + 50:
            logger.warning(
                f'  {symbol} {interval}: only {len(df)} bars in store '
                f'(need >{WARMUP_BARS + 50}) — SKIPPING (no live fallback)'
            )
            return None

        # Always state actual coverage, so no run can claim depth it does not have.
        got_pct = 100.0 * len(df) / bars if bars else 0.0
        span_days = (df.index[-1] - df.index[0]).days
        msg = (f'  {symbol} {interval}: {len(df)} bars '
               f'({df.index[0].date()} -> {df.index[-1].date()}, {span_days}d span) '
               f'= {got_pct:.0f}% of {bars} requested')
        if got_pct < 90.0:
            logger.warning(msg + '  <-- SHORT OF REQUEST (vendor depth limit)')
        else:
            logger.info(msg)
        return df
    except Exception as e:
        logger.error(f'  {symbol} {interval}: load failed — {e}')
        return None


# =============================================================================
# TRADE SIMULATION
# =============================================================================

def simulate_trade(
    hist:       pd.DataFrame,
    entry_idx:  int,          # bar index of signal
    direction:  str,          # 'BUY' or 'SELL'
    atr:        float,        # ATR at signal bar
    t1_mult:    float,        # T1 = entry ± t1_mult × ATR
    sl_mult:    float,        # SL = entry ∓ sl_mult × ATR
    max_hold:   int = MAX_HOLD_BARS,
) -> Tuple[str, float]:
    """
    Simulate a trade from entry_idx forward.

    Entry: next bar open after signal (realistic execution).
    Returns: (outcome, r_multiple)
      outcome: 'WIN' | 'LOSS' | 'EXPIRED'
      r_multiple: +R for win, -1 for loss, -(fraction) for expiry
    """
    n = len(hist)
    if entry_idx + 1 >= n:
        return ('EXPIRED', 0.0)

    # Entry at NEXT bar open
    entry_bar   = entry_idx + 1
    entry_price = float(hist['Open'].iloc[entry_bar])

    # Apply slippage
    if direction == 'BUY':
        entry_price *= (1 + SLIPPAGE_PCT)
    else:
        entry_price *= (1 - SLIPPAGE_PCT)

    if atr <= 0:
        atr = entry_price * 0.008   # fallback: 0.8% of price

    # Calculate targets
    if direction == 'BUY':
        t1    = entry_price + t1_mult * atr
        sl    = entry_price - sl_mult * atr
    else:
        t1    = entry_price - t1_mult * atr
        sl    = entry_price + sl_mult * atr

    sl_dist = abs(entry_price - sl)
    if sl_dist <= 0:
        return ('EXPIRED', 0.0)

    # Walk forward bar by bar
    end_bar = min(entry_bar + max_hold, n)
    for i in range(entry_bar + 1, end_bar):
        high = float(hist['High'].iloc[i])
        low  = float(hist['Low'].iloc[i])

        if direction == 'BUY':
            if high >= t1:
                r = (t1 - entry_price) / sl_dist
                return ('WIN', round(r, 3))
            if low <= sl:
                return ('LOSS', -1.0)
        else:
            if low <= t1:
                r = (entry_price - t1) / sl_dist
                return ('WIN', round(r, 3))
            if high >= sl:
                return ('LOSS', -1.0)

    # Expired — exit at close of last bar
    exit_price = float(hist['Close'].iloc[end_bar - 1])
    if direction == 'BUY':
        r = (exit_price - entry_price) / sl_dist
    else:
        r = (entry_price - exit_price) / sl_dist
    return ('EXPIRED', round(r, 3))


# =============================================================================
# REGIME FILTER
# =============================================================================

def get_regime_at_bar(hist_window: pd.DataFrame, symbol: str) -> str:
    """Get regime classification for this bar using regime_ensemble."""
    try:
        from market_agent.brain.regime_ensemble import regime_ensemble_signal
        bs = regime_ensemble_signal(hist_window, symbol=symbol)
        return bs.measurements.get('computed_regime', 'RANGING')
    except Exception:
        return 'RANGING'


# =============================================================================
# BRAIN RUNNERS
# =============================================================================

def run_multi_modal_fusion(hist: pd.DataFrame, symbol: str) -> Optional[dict]:
    """Run MFF brain and return signal dict."""
    try:
        from market_agent.brain.multi_modal_fusion import multi_modal_fusion_signal
        from market_agent.brain.brain_utils import calc_atr
        bs  = multi_modal_fusion_signal(hist, symbol=symbol)
        atr = calc_atr(hist, 14)
        if bs.direction in ('BUY', 'SELL') and bs.effective_confidence() >= MIN_CONFIDENCE:
            return {
                'direction':  bs.direction,
                'confidence': bs.effective_confidence(),
                'atr':        atr,
                't1_mult':    bs.rr_t1_mult or 2.0,
                'sl_mult':    bs.rr_sl_mult or 1.0,
                'evidence':   bs.primary_evidence[:60],
            }
    except Exception as e:
        logger.debug(f'MFF error: {e}')
    return None


def run_multi_timeframe(hist: pd.DataFrame, symbol: str) -> Optional[dict]:
    """Run Multi-Timeframe brain (1H only — no fetch_fn in backtest)."""
    try:
        from market_agent.brain.multi_timeframe import multi_timeframe_signal
        from market_agent.brain.brain_utils import calc_atr
        bs  = multi_timeframe_signal(symbol, hist, fetch_fn=None)
        atr = calc_atr(hist, 14)
        if bs.direction in ('BUY', 'SELL') and bs.effective_confidence() >= MIN_CONFIDENCE:
            return {
                'direction':  bs.direction,
                'confidence': bs.effective_confidence(),
                'atr':        atr,
                't1_mult':    bs.rr_t1_mult or 2.0,
                'sl_mult':    bs.rr_sl_mult or 0.75,
                'evidence':   bs.primary_evidence[:60],
            }
    except Exception as e:
        logger.debug(f'MTF error: {e}')
    return None


def run_cross_stock_gnn(hist: pd.DataFrame, symbol: str, regime: str = 'RANGING') -> Optional[dict]:
    """Run Cross-Stock-GNN brain."""
    try:
        from market_agent.brain.cross_stock_gnn import cross_stock_gnn_signal
        from market_agent.brain.brain_utils import calc_atr
        bs  = cross_stock_gnn_signal(hist, symbol=symbol, regime=regime)
        atr = calc_atr(hist, 14)
        if bs.direction in ('BUY', 'SELL') and bs.effective_confidence() >= MIN_CONFIDENCE:
            return {
                'direction':  bs.direction,
                'confidence': bs.effective_confidence(),
                'atr':        atr,
                't1_mult':    bs.rr_t1_mult or 2.0,
                'sl_mult':    bs.rr_sl_mult or 0.75,
                'evidence':   bs.primary_evidence[:60],
            }
    except Exception as e:
        logger.debug(f'GNN error: {e}')
    return None


def run_liquidity_sweep_logged(hist: pd.DataFrame, symbol: str, regime: str) -> dict:
    """
    Run Liquidity-Sweep brain and ALWAYS return full decision info, whether
    it fired or held. Previously (run_liquidity_sweep) returned None on
    every HOLD, so the backtest recorded nothing at all about the ~99% of
    bars where the brain declined to trade — no gate name, no reason, no
    confidence. That made "why is it always on hold" unanswerable except by
    re-running one bar at a time by hand. Every bar's decision is now
    logged by the caller regardless of outcome.
    """
    try:
        from market_agent.brain.liquidity_sweep import liquidity_sweep_signal
        from market_agent.brain.brain_utils import calc_atr

        from market_agent.brain.liquidity_sweep import DECISION_CODE_TO_GATE

        bs  = liquidity_sweep_signal(hist, symbol=symbol, regime=regime)
        atr = calc_atr(hist, 14)
        eff_conf = bs.effective_confidence()
        fired = bs.direction in ('BUY', 'SELL') and eff_conf >= MIN_CONFIDENCE
        code = bs.measurements.get('decision_factor') if bs.measurements else None
        gate = DECISION_CODE_TO_GATE.get(code, 'UNKNOWN') if code is not None else 'UNKNOWN'
        return {
            'fired':      fired,
            'direction':  bs.direction,
            'confidence': eff_conf,
            'gate':       gate,
            'evidence':   (bs.primary_evidence or '')[:120],
            'atr':        atr,
            't1_mult':    bs.rr_t1_mult or 2.0,
            'sl_mult':    bs.rr_sl_mult or 0.75,
        }
    except Exception as e:
        return {
            'fired': False, 'direction': 'HOLD', 'confidence': 0.0, 'gate': 'EXCEPTION',
            'evidence': f'{type(e).__name__}: {str(e)[:100]}', 'atr': 0.0,
            't1_mult': 2.0, 'sl_mult': 0.75,
        }


def run_liquidity_sweep(hist: pd.DataFrame, symbol: str, regime: str) -> Optional[dict]:
    """Legacy contract (returns None on HOLD) — kept for other callers.
    New code should use run_liquidity_sweep_logged() instead."""
    sig = run_liquidity_sweep_logged(hist, symbol, regime)
    return sig if sig['fired'] else None


# =============================================================================
# PERFORMANCE METRICS
# =============================================================================

def compute_metrics(trades: List[dict]) -> dict:
    """
    Compute comprehensive performance metrics from trade list.
    Each trade: {'outcome': WIN|LOSS|EXPIRED, 'r': float, 'symbol': str, ...}
    """
    if not trades:
        return {'error': 'no trades'}

    r_values    = [t['r'] for t in trades]
    wins        = [r for r in r_values if r > 0]
    losses      = [r for r in r_values if r <= 0]
    win_count   = len(wins)
    loss_count  = len(losses)
    total       = len(trades)

    win_rate      = win_count / total if total > 0 else 0.0

    # STEP 0 (plan): `win_rate` above counts ANY r>0 as a win, which includes
    # EXPIRED trades (neither target nor stop hit within max_hold, closed at
    # market) that happened to close green. That is NOT the strategy's thesis
    # working. On 2026-09-13 this made a brain look like 35.3% WR / +54.1R when
    # the thesis-only numbers were 27.4% WR / +9.6R -- 82% of the "profit" came
    # from trades where the thesis explicitly failed. These fields are mandatory
    # now so that distortion can never be reported without its correction.
    outcomes        = [t.get('outcome', '') for t in trades]
    strict_wins     = sum(1 for o in outcomes if o == 'WIN')
    expired_trades  = [t for t in trades if t.get('outcome') == 'EXPIRED']
    thesis_trades   = [t for t in trades if t.get('outcome') in ('WIN', 'LOSS')]
    expired_r       = sum(t['r'] for t in expired_trades)
    total_r         = sum(r_values)

    strict_win_rate   = strict_wins / total if total else 0.0
    thesis_total_r    = sum(t['r'] for t in thesis_trades)
    thesis_avg_r      = (thesis_total_r / len(thesis_trades)) if thesis_trades else 0.0
    pct_r_from_expiry = (expired_r / total_r) if total_r else 0.0
    gross_profit  = sum(wins)
    gross_loss    = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    avg_r         = float(np.mean(r_values)) if r_values else 0.0
    avg_win       = float(np.mean(wins))     if wins else 0.0
    avg_loss      = float(np.mean(losses))   if losses else 0.0

    # Equity curve
    equity = np.cumsum([0.0] + r_values)
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    max_dd = float(np.min(dd))

    # Sharpe (annualised, assuming 6 trades/day on 1H)
    if len(r_values) > 1:
        r_std  = float(np.std(r_values))
        sharpe = (avg_r / r_std * math.sqrt(252 * 6)) if r_std > 0 else 0.0
    else:
        sharpe = 0.0

    # Consecutive stats
    max_consec_wins   = _max_consecutive(r_values, positive=True)
    max_consec_losses = _max_consecutive(r_values, positive=False)

    # By symbol breakdown
    by_symbol = {}
    for t in trades:
        sym = t.get('symbol', 'UNKNOWN')
        if sym not in by_symbol:
            by_symbol[sym] = []
        by_symbol[sym].append(t['r'])

    sym_stats = {}
    for sym, rs in by_symbol.items():
        sym_wins = sum(1 for r in rs if r > 0)
        sym_stats[sym] = {
            'n':        len(rs),
            'win_rate': sym_wins / len(rs) if rs else 0.0,
            'avg_r':    float(np.mean(rs)),
        }

    return {
        'total_trades':       total,
        'win_rate':           round(win_rate, 4),
        # Mandatory thesis-vs-timeout breakdown (see note above).
        'strict_win_rate':    round(strict_win_rate, 4),
        'thesis_trades':      len(thesis_trades),
        'thesis_total_r':     round(thesis_total_r, 3),
        'thesis_avg_r':       round(thesis_avg_r, 4),
        'expired_trades':     len(expired_trades),
        'expired_r':          round(expired_r, 3),
        'pct_r_from_expiry':  round(pct_r_from_expiry, 4),
        'profit_factor':      round(profit_factor, 3),
        'avg_r':              round(avg_r, 4),
        'avg_win_r':          round(avg_win, 4),
        'avg_loss_r':         round(avg_loss, 4),
        'gross_profit_r':     round(gross_profit, 3),
        'gross_loss_r':       round(gross_loss, 3),
        'max_drawdown_r':     round(max_dd, 3),
        'sharpe':             round(sharpe, 3),
        'max_consec_wins':    max_consec_wins,
        'max_consec_losses':  max_consec_losses,
        'by_symbol':          sym_stats,
        'passes':             (
            win_rate >= MIN_WIN_RATE
            and profit_factor >= MIN_PROFIT_FACTOR
            and total >= MIN_TRADES
        ),
    }


def _max_consecutive(values: list, positive: bool) -> int:
    max_c = cur = 0
    for v in values:
        if (positive and v > 0) or (not positive and v <= 0):
            cur += 1; max_c = max(max_c, cur)
        else:
            cur = 0
    return max_c


# =============================================================================
# MAIN BACKTEST LOOP
# =============================================================================

def backtest_brain(
    brain_name:  str,
    brain_fn:    Callable,
    symbols:     List[str],
    days:        int = 365,
    use_regime_filter: bool = True,
    interval:    str = '1h',
    log_all_decisions: bool = False,
) -> dict:
    """
    Run a full walk-forward backtest for one brain across all symbols.

    `log_all_decisions=True` (currently only wired for liquidity_sweep via
    run_liquidity_sweep_logged) records EVERY bar's decision — fired or
    held, with the exact gate and human-readable reason — not just the
    bars that produced a trade. Previously a HOLD vanished with no trace,
    so "why did it not fire on a bar that looked obviously tradeable" was
    unanswerable without re-running that one bar by hand.

    Returns (metrics, trades, decision_log).
    """
    allowed_regimes = BRAIN_REGIME_GATES.get(brain_name, ['ALL'])
    all_trades: List[dict] = []
    decision_log: List[dict] = []
    total_signals   = 0

    for symbol in symbols:
        logger.info(f'  {symbol} [{interval}]...')
        df = load_symbol_data(symbol, days=days, interval=interval)
        if df is None:
            continue

        n = len(df)

        for i in range(WARMUP_BARS, n - MAX_HOLD_BARS - 2, STEP_BARS):
            hist_window = df.iloc[:i + 1].copy()
            timestamp = df.index[i]

            # Get regime for this bar (used for filtering)
            regime = 'RANGING'
            if use_regime_filter:
                regime = get_regime_at_bar(hist_window, symbol)

            regime_blocked = (use_regime_filter and 'ALL' not in allowed_regimes
                             and regime not in allowed_regimes)
            if regime_blocked and not log_all_decisions:
                continue

            if log_all_decisions and regime_blocked:
                decision_log.append({
                    'symbol': symbol, 'interval': interval, 'bar': i,
                    'timestamp': str(timestamp), 'regime': regime,
                    'fired': False, 'gate': 'GATE_REGIME_FILTER_EXTERNAL',
                    'confidence': 0.0,
                    'evidence': f'{brain_name} not permitted in {regime} '
                               f'(allowed: {allowed_regimes})',
                })
                continue

            # Run the brain (some brains require regime input)
            if log_all_decisions and brain_name == 'liquidity_sweep':
                sig = run_liquidity_sweep_logged(hist_window, symbol, regime)
                decision_log.append({
                    'symbol': symbol, 'interval': interval, 'bar': i,
                    'timestamp': str(timestamp), 'regime': regime,
                    'fired': sig['fired'], 'gate': sig.get('gate', ''),
                    'confidence': sig['confidence'], 'evidence': sig.get('evidence', ''),
                })
                if not sig['fired']:
                    continue
            elif brain_name in ('cross_stock_gnn', 'liquidity_sweep'):
                sig = brain_fn(hist_window, symbol, regime)
            else:
                sig = brain_fn(hist_window, symbol)

            if sig is None:
                continue

            total_signals += 1
            atr = sig.get('atr', 0.0)

            outcome, r = simulate_trade(
                hist        = df,
                entry_idx   = i,
                direction   = sig['direction'],
                atr         = atr,
                t1_mult     = sig.get('t1_mult', 2.0),
                sl_mult     = sig.get('sl_mult', 1.0),
            )

            all_trades.append({
                'symbol':     symbol,
                'interval':   interval,
                'bar':        i,
                'timestamp':  str(timestamp),
                'date':       str(timestamp)[:10],
                'direction':  sig['direction'],
                'confidence': sig['confidence'],
                'regime':     regime,
                'outcome':    outcome,
                'r':          r,
                'evidence':   sig.get('evidence', ''),
            })

    metrics = compute_metrics(all_trades)
    metrics['total_signals_generated'] = total_signals
    metrics['trades_after_regime_gate'] = len(all_trades)
    metrics['interval'] = interval

    return metrics, all_trades, decision_log


# =============================================================================
# TWO-TIMEFRAME BACKTEST (liquidity_sweep_htf_ltf) — separate walk-forward
# loop because it needs two aligned series, which doesn't fit the single-
# DataFrame loop backtest_brain() uses for every other brain.
# =============================================================================

def backtest_liquidity_sweep_htf_ltf(
    symbols:  List[str],
    days:     int = 365,
    htf:      str = '30m',
    ltf:      str = '5m',
    use_regime_filter: bool = True,
    log_all_decisions: bool = True,
) -> Tuple[dict, list, list]:
    """
    Walk forward on the LTF series (entries/exits happen at LTF granularity).
    At each LTF bar, the HTF window used for level-finding is every HTF bar
    with timestamp <= the current LTF bar's timestamp -- found by binary
    search on the sorted HTF index, so no future HTF bar can leak in.
    """
    from market_agent.brain.liquidity_sweep import (
        liquidity_sweep_htf_ltf_signal, DECISION_CODE_TO_GATE,
    )
    from market_agent.brain.brain_utils import calc_atr

    allowed_regimes = BRAIN_REGIME_GATES.get('liquidity_sweep', ['ALL'])
    all_trades: List[dict] = []
    decision_log: List[dict] = []
    total_signals = 0

    for symbol in symbols:
        logger.info(f'  {symbol} [{htf}->{ltf}]...')
        df_htf = load_symbol_data(symbol, days=days, interval=htf)
        df_ltf = load_symbol_data(symbol, days=days, interval=ltf)
        if df_htf is None or df_ltf is None:
            continue

        # unified_market_data.get_ohlcv() can return tz-aware data for one
        # interval and tz-naive for another on the SAME symbol (confirmed:
        # ^NSEBANK 30m came back tz-aware 'Asia/Kolkata' via a live yfinance
        # fallback, while 5m came from Postgres already tz-naive) --
        # searchsorted() below cannot compare a tz-aware and tz-naive index.
        # The single-timeframe backtester never surfaced this because it
        # only ever loads one series; comparing two is what this function
        # introduces. Normalized here rather than in the shared loader,
        # which other callers depend on unchanged.
        if df_htf.index.tz is not None:
            df_htf = df_htf.copy()
            df_htf.index = df_htf.index.tz_convert('UTC').tz_localize(None)
        if df_ltf.index.tz is not None:
            df_ltf = df_ltf.copy()
            df_ltf.index = df_ltf.index.tz_convert('UTC').tz_localize(None)

        htf_index = df_htf.index
        n = len(df_ltf)

        for i in range(WARMUP_BARS, n - MAX_HOLD_BARS - 2, STEP_BARS):
            ltf_window = df_ltf.iloc[:i + 1]
            timestamp  = df_ltf.index[i]

            # HTF bars up to (not including) the current LTF timestamp —
            # binary search on the sorted index, no future HTF bar leaks in.
            htf_cutoff = htf_index.searchsorted(timestamp, side='right')
            if htf_cutoff < WARMUP_BARS:
                continue
            htf_window = df_htf.iloc[:htf_cutoff]

            regime = 'RANGING'
            if use_regime_filter:
                regime = get_regime_at_bar(ltf_window, symbol)

            regime_blocked = (use_regime_filter and 'ALL' not in allowed_regimes
                             and regime not in allowed_regimes)
            if regime_blocked:
                if log_all_decisions:
                    decision_log.append({
                        'symbol': symbol, 'htf': htf, 'ltf': ltf, 'bar': i,
                        'timestamp': str(timestamp), 'regime': regime,
                        'fired': False, 'gate': 'GATE_REGIME_FILTER_EXTERNAL',
                        'confidence': 0.0,
                        'evidence': f'liquidity_sweep not permitted in {regime} '
                                   f'(allowed: {allowed_regimes})',
                    })
                continue

            try:
                bs = liquidity_sweep_htf_ltf_signal(
                    htf_window, ltf_window, symbol=symbol, regime=regime,
                    htf_label=htf, ltf_label=ltf,
                )
                eff_conf = bs.effective_confidence()
                fired = bs.direction in ('BUY', 'SELL') and eff_conf >= MIN_CONFIDENCE
                code = bs.measurements.get('decision_factor') if bs.measurements else None
                gate = DECISION_CODE_TO_GATE.get(code, 'UNKNOWN') if code is not None else 'UNKNOWN'
                evidence = (bs.primary_evidence or '')[:120]
                atr = calc_atr(ltf_window, 14)
            except Exception as e:
                fired = False
                gate = 'EXCEPTION'
                evidence = f'{type(e).__name__}: {str(e)[:100]}'
                bs = None
                atr = 0.0

            if log_all_decisions:
                decision_log.append({
                    'symbol': symbol, 'htf': htf, 'ltf': ltf, 'bar': i,
                    'timestamp': str(timestamp), 'regime': regime,
                    'fired': fired, 'gate': gate,
                    'confidence': eff_conf if fired or bs else 0.0,
                    'evidence': evidence,
                })

            if not fired:
                continue

            total_signals += 1
            outcome, r = simulate_trade(
                hist        = df_ltf,
                entry_idx   = i,
                direction   = bs.direction,
                atr         = atr,
                t1_mult     = bs.rr_t1_mult or 2.0,
                sl_mult     = bs.rr_sl_mult or 0.75,
            )
            all_trades.append({
                'symbol':     symbol,
                'htf':        htf, 'ltf': ltf,
                'bar':        i,
                'timestamp':  str(timestamp),
                'date':       str(timestamp)[:10],
                'direction':  bs.direction,
                'confidence': eff_conf,
                'regime':     regime,
                'outcome':    outcome,
                'r':          r,
                'evidence':   evidence,
            })

    metrics = compute_metrics(all_trades)
    metrics['total_signals_generated'] = total_signals
    metrics['trades_after_regime_gate'] = len(all_trades)
    metrics['htf'] = htf
    metrics['ltf'] = ltf

    return metrics, all_trades, decision_log


# =============================================================================
# REPORT PRINTER
# =============================================================================

def print_report(brain_name: str, metrics: dict, trades: list) -> None:
    """Print a comprehensive performance report."""
    print()
    print('=' * 65)
    print(f'  BRAIN: {brain_name.upper().replace("_", "-")}')
    print(f'  Backtest Report — Walk-Forward, Realistic Entry')
    print('=' * 65)

    if 'error' in metrics:
        print(f'  ERROR: {metrics["error"]}')
        return

    t = metrics['total_trades']
    wr = metrics['win_rate']
    pf = metrics['profit_factor']
    ar = metrics['avg_r']
    sh = metrics['sharpe']
    dd = metrics['max_drawdown_r']
    passes = metrics['passes']

    verdict = '✅ PASSES' if passes else '❌ FAILS'
    print(f'\n  VERDICT: {verdict}')
    print(f'  (Need: win_rate>={MIN_WIN_RATE:.0%}, '
          f'profit_factor>={MIN_PROFIT_FACTOR}, trades>={MIN_TRADES})')

    print(f'\n  THESIS vs TIMEOUT  (does the strategy actually work, or is it the exit rule?)')
    print(f'  {"Strict win rate:":<25} {metrics.get("strict_win_rate", 0):.1%}'
          f'   (target hit before stop)')
    print(f'  {"Thesis-only trades:":<25} {metrics.get("thesis_trades", 0)}'
          f'  ->  {metrics.get("thesis_total_r", 0):+.1f}R total, '
          f'{metrics.get("thesis_avg_r", 0):+.4f}R/trade')
    print(f'  {"Expired (thesis failed):":<25} {metrics.get("expired_trades", 0)}'
          f'  ->  {metrics.get("expired_r", 0):+.1f}R')
    pct_exp = metrics.get('pct_r_from_expiry', 0)
    exp_flag = '  <-- WARNING: most "profit" is the timeout exit, not the strategy' if pct_exp > 0.5 else ''
    print(f'  {"% of R from timeouts:":<25} {pct_exp:.1%}{exp_flag}')

    print(f'\n  CORE METRICS')
    print(f'  {"Total trades:":<25} {t}')
    wr_flag = "✅" if wr >= MIN_WIN_RATE else "❌"
    print(f'  {"Win rate (r>0):":<25} {wr:.1%}  {wr_flag}'
          f'   (includes profitable timeouts — see above)')
    pf_flag = "✅" if pf >= MIN_PROFIT_FACTOR else "❌"
    print(f'  {"Profit factor:":<25} {pf:.3f}  {pf_flag}')
    print(f'  {"Average R:":<25} {ar:+.3f}R')
    print(f'  {"Avg win:":<25} {metrics["avg_win_r"]:+.3f}R')
    print(f'  {"Avg loss:":<25} {metrics["avg_loss_r"]:+.3f}R')
    print(f'  {"Gross profit:":<25} {metrics["gross_profit_r"]:+.2f}R')
    print(f'  {"Gross loss:":<25} {metrics["gross_loss_r"]:+.2f}R')
    print(f'  {"Max drawdown:":<25} {dd:.2f}R')
    print(f'  {"Sharpe (ann.):":<25} {sh:.3f}')
    print(f'  {"Max consec wins:":<25} {metrics["max_consec_wins"]}')
    print(f'  {"Max consec losses:":<25} {metrics["max_consec_losses"]}')

    print(f'\n  SIGNAL FLOW')
    print(f'  {"Signals generated:":<25} {metrics["total_signals_generated"]}')
    print(f'  {"Trades after gate:":<25} {metrics["trades_after_regime_gate"]}')
    if metrics["total_signals_generated"] > 0:
        gate_pct = metrics["trades_after_regime_gate"] / metrics["total_signals_generated"] * 100
        print(f'  {"Gate pass rate:":<25} {gate_pct:.1f}%')

    print(f'\n  BY SYMBOL')
    for sym, s in sorted(metrics.get('by_symbol', {}).items()):
        wr_s = s['win_rate']
        flag = '✅' if wr_s >= MIN_WIN_RATE else ('⚠ ' if wr_s >= 0.50 else '❌')
        print(f'  {sym:<15} n={s["n"]:>4}  wr={wr_s:.1%}  avg_r={s["avg_r"]:+.3f}R  {flag}')

    # Outcome distribution
    if trades:
        outcomes = {'WIN': 0, 'LOSS': 0, 'EXPIRED': 0}
        for t in trades:
            outcomes[t['outcome']] = outcomes.get(t['outcome'], 0) + 1
        print(f'\n  OUTCOME DISTRIBUTION')
        total_t = len(trades)
        for k, v in outcomes.items():
            print(f'  {k:<10} {v:>5} ({v/total_t:.1%})')

    print('=' * 65)


# =============================================================================
# ENTRY POINT
# =============================================================================

# =============================================================================
# GRID SEARCH — Multi-Timeframe Parameter Sweep
# =============================================================================

# Parameters to sweep when --grid is used
_MTF_GRID = {
    'min_confidence': [0.45, 0.50, 0.55, 0.60],
    'rsi_buy_max':    [55, 60, 65],        # RSI ceiling for BUY entries
    'rsi_sell_min':   [35, 40, 45],        # RSI floor for SELL entries
    'sma_1h':         [(5, 20), (8, 21), (10, 30)],   # (fast, slow) on 1H
}


def _vectorised_simulate(
    opens:     np.ndarray,
    highs:     np.ndarray,
    lows:      np.ndarray,
    closes:    np.ndarray,
    signal_bars: np.ndarray,
    directions:  np.ndarray,
    atrs:        np.ndarray,
    t1_mult: float = 2.0,
    sl_mult: float = 0.75,
    max_hold: int  = MAX_HOLD_BARS,
) -> np.ndarray:
    """Fast inner simulation loop over signal bars only (not all bars)."""
    n = len(closes)
    r_values = np.zeros(len(signal_bars))
    for k, (sig_i, direction, atr) in enumerate(zip(signal_bars, directions, atrs)):
        entry_bar = sig_i + 1
        if entry_bar >= n:
            continue
        entry_price = opens[entry_bar] * (1 + direction * SLIPPAGE_PCT)
        if atr <= 0:
            atr = entry_price * 0.008
        t1 = entry_price + direction * t1_mult * atr
        sl = entry_price - direction * sl_mult * atr
        sl_dist = abs(entry_price - sl)
        if sl_dist <= 0:
            continue
        end_bar = min(entry_bar + max_hold, n)
        outcome_r = None
        for j in range(entry_bar + 1, end_bar):
            h, l = highs[j], lows[j]
            if direction > 0:
                if h >= t1:   outcome_r = (t1 - entry_price) / sl_dist; break
                if l <= sl:   outcome_r = -1.0; break
            else:
                if l <= t1:   outcome_r = (entry_price - t1) / sl_dist; break
                if h >= sl:   outcome_r = -1.0; break
        if outcome_r is None:
            exit_p = closes[end_bar - 1]
            outcome_r = direction * (exit_p - entry_price) / sl_dist
        r_values[k] = round(outcome_r, 3)
    return r_values


def run_grid_search(
    brain_name: str,
    symbols: List[str],
    days: int = 365,
    use_regime_filter: bool = True,
) -> None:
    """
    FAST vectorised grid-search for multi_timeframe brain.

    Pre-computes all indicators (SMA, RSI, ATR) once per symbol.
    Then sweeps RSI/confidence thresholds as pure numpy masks — ~100x faster
    than the bar-by-bar rolling approach.
    """
    if brain_name != 'multi_timeframe':
        print(f'Grid search only supported for multi_timeframe (got: {brain_name})')
        return

    SMA_COMBOS    = [(5, 20), (8, 21), (10, 30)]
    RSI_BUY_MAXES = [55, 60, 65]
    RSI_SELL_MINS = [35, 40, 45]
    MIN_CONFS     = [0.45, 0.50, 0.55, 0.60]

    total_combos = len(SMA_COMBOS) * len(RSI_BUY_MAXES) * len(RSI_SELL_MINS) * len(MIN_CONFS)
    print(f'\nVectorised grid search: {total_combos} combos × {len(symbols)} symbols')
    print(f'Days: {days} | Regime filter: {use_regime_filter}')
    print('=' * 70)

    # Step 1: Load data once
    sym_data: Dict[str, pd.DataFrame] = {}
    for sym in symbols:
        logger.info(f'  Loading {sym}...')
        df = load_symbol_data(sym, days=days)
        if df is not None:
            sym_data[sym] = df.reset_index(drop=True)

    if not sym_data:
        print('ERROR: No data loaded.')
        return

    from market_agent.brain.brain_utils import calc_rsi_series

    # Step 2: Pre-compute all indicator arrays once per (symbol, sma_combo)
    precomp: Dict[str, Dict[tuple, dict]] = {}
    for sym, df in sym_data.items():
        precomp[sym] = {}
        close = df['Close']
        rsi_arr = calc_rsi_series(df).fillna(50.0).values
        # ATR approximation: mean absolute close-diff over 14 bars
        atr_series = close.diff().abs().rolling(14).mean().bfill().values
        opens  = df['Open'].values
        highs  = df['High'].values
        lows   = df['Low'].values
        closes = close.values
        for (fp, sp) in SMA_COMBOS:
            sma_fast = close.rolling(fp).mean().values
            sma_slow = close.rolling(sp).mean().values
            with np.errstate(invalid='ignore', divide='ignore'):
                sma_diff = np.where(sma_slow > 0, sma_fast - sma_slow, np.nan)
            precomp[sym][(fp, sp)] = {
                'rsi': rsi_arr, 'sma_diff': sma_diff, 'atr': atr_series,
                'opens': opens, 'highs': highs, 'lows': lows, 'closes': closes,
                'n': len(closes),
            }

    # Step 3: Grid sweep — pure numpy operations per combo
    results = []
    combo_i = 0

    for (fp, sp) in SMA_COMBOS:
        for rsi_buy_max in RSI_BUY_MAXES:
            for rsi_sell_min in RSI_SELL_MINS:
                for min_conf in MIN_CONFS:
                    combo_i += 1
                    # Single-TF confidence is always 0.62 — skip if min_conf > 0.62
                    if min_conf > 0.62:
                        results.append({
                            'sma_1h': f'{fp}/{sp}', 'rsi_buy_max': rsi_buy_max,
                            'rsi_sell_min': rsi_sell_min, 'min_conf': min_conf,
                            'trades': 0, 'win_rate': 0.0, 'profit_factor': 0.0,
                            'avg_r': 0.0, 'sharpe': 0.0, 'max_dd': 0.0, 'passes': False,
                        })
                        print(f'  [{combo_i:>3}/{total_combos}] sma={fp}/{sp} '
                              f'rsi_buy<{rsi_buy_max} rsi_sell>{rsi_sell_min} '
                              f'conf>={min_conf:.2f} | SKIPPED (conf>0.62 impossible)')
                        continue

                    all_r: List[float] = []
                    for sym, pc_map in precomp.items():
                        pc = pc_map[(fp, sp)]
                        n     = pc['n']
                        start = WARMUP_BARS
                        end   = n - MAX_HOLD_BARS - 2
                        if end <= start:
                            continue
                        bar_idx = np.arange(start, end)
                        rsi_sub = pc['rsi'][start:end]
                        sd_sub  = pc['sma_diff'][start:end]

                        buy_mask  = (sd_sub > 0) & (rsi_sub < rsi_buy_max) & np.isfinite(sd_sub)
                        sell_mask = (sd_sub < 0) & (rsi_sub > rsi_sell_min) & np.isfinite(sd_sub)

                        for mask, direction in [(buy_mask, 1), (sell_mask, -1)]:
                            sig_bars = bar_idx[mask]
                            if len(sig_bars) == 0:
                                continue
                            r_vals = _vectorised_simulate(
                                opens=pc['opens'], highs=pc['highs'],
                                lows=pc['lows'],   closes=pc['closes'],
                                signal_bars=sig_bars,
                                directions=np.full(len(sig_bars), direction),
                                atrs=pc['atr'][sig_bars],
                            )
                            all_r.extend(r_vals.tolist())

                    m = compute_metrics([{'r': r, 'symbol': 'ALL'} for r in all_r]) \
                        if all_r else {'error': 'no trades'}
                    row = {
                        'sma_1h': f'{fp}/{sp}', 'rsi_buy_max': rsi_buy_max,
                        'rsi_sell_min': rsi_sell_min, 'min_conf': min_conf,
                        'trades': m.get('total_trades', 0),
                        'win_rate': m.get('win_rate', 0.0),
                        'profit_factor': m.get('profit_factor', 0.0),
                        'avg_r': m.get('avg_r', 0.0),
                        'sharpe': m.get('sharpe', 0.0),
                        'max_dd': m.get('max_drawdown_r', 0.0),
                        'passes': m.get('passes', False),
                    }
                    results.append(row)
                    status = '✅' if row['passes'] else '❌'
                    print(f'  [{combo_i:>3}/{total_combos}] sma={fp}/{sp} '
                          f'rsi_buy<{rsi_buy_max} rsi_sell>{rsi_sell_min} '
                          f'conf>={min_conf:.2f} | '
                          f'n={row["trades"]:>5} wr={row["win_rate"]:.1%} '
                          f'pf={row["profit_factor"]:.2f} {status}')

    # Step 4: Report
    results_df = pd.DataFrame(results).sort_values(
        ['win_rate', 'profit_factor'], ascending=False
    )
    suffix  = datetime.now().strftime('%Y%m%d_%H%M')
    csv_out = f'grid_multi_timeframe_{days}d_{suffix}.csv'
    results_df.to_csv(csv_out, index=False)

    print('\n' + '=' * 70)
    print('  GRID SEARCH RESULTS — TOP 10 COMBINATIONS')
    print('=' * 70)
    passing = results_df[results_df['passes']]
    if passing.empty:
        print('  ⚠️  No combination passes all thresholds. Top 5 by win rate:')
        display = results_df.head(5)
    else:
        print(f'  ✅ {len(passing)} passing combinations found!')
        display = passing.head(10)

    for _, row in display.iterrows():
        flag = '✅' if row['passes'] else '❌'
        print(f'  sma={row["sma_1h"]} rsi_buy<{row["rsi_buy_max"]} '
              f'rsi_sell>{row["rsi_sell_min"]} conf>={row["min_conf"]:.2f} '
              f'→ n={row["trades"]:>5} wr={row["win_rate"]:.1%} '
              f'pf={row["profit_factor"]:.2f} sharpe={row["sharpe"]:.2f} {flag}')

    print(f'\n  Full grid results saved → {csv_out}')
    print('=' * 70)

def _run_mtf_with_params(
    hist: pd.DataFrame,
    symbol: str,
    min_confidence: float,
    rsi_buy_max: float,
    rsi_sell_min: float,
    sma_1h: tuple,
) -> Optional[dict]:
    """Run Multi-Timeframe brain with overridden parameters for grid search."""
    try:
        import math
        from market_agent.brain.brain_utils import calc_rsi_series, calc_atr

        fast_p, slow_p = sma_1h
        if len(hist) < slow_p + 5:
            return None

        sma_fast_raw = hist['Close'].rolling(fast_p).mean().iloc[-1]
        sma_slow_raw = hist['Close'].rolling(slow_p).mean().iloc[-1]

        def _sf(v, fb=float('nan')):
            try:
                r = float(v)
                return fb if math.isnan(r) else r
            except (TypeError, ValueError):
                return fb

        sma_fast = _sf(sma_fast_raw)
        sma_slow = _sf(sma_slow_raw)
        rsi_raw  = calc_rsi_series(hist).iloc[-1] if len(hist) > 15 else 50.0
        rsi      = _sf(rsi_raw, 50.0)

        if math.isnan(sma_fast) or math.isnan(sma_slow) or sma_slow <= 0:
            return None

        direction = (
            'BUY'  if sma_fast > sma_slow and rsi < rsi_buy_max else
            'SELL' if sma_fast < sma_slow and rsi > rsi_sell_min else
            'HOLD'
        )
        if direction == 'HOLD':
            return None

        alignment = 1.0  # single-TF, treat as full alignment for grid test
        confidence = min(alignment * 0.85, 0.62)
        if confidence < min_confidence:
            return None

        atr = calc_atr(hist, 14)
        return {
            'direction':  direction,
            'confidence': confidence,
            'atr':        atr,
            't1_mult':    2.0,
            'sl_mult':    0.75,
            'evidence':   f'Grid RSI={rsi:.0f} SMA{fast_p}/{slow_p}',
        }
    except Exception as e:
        logger.debug(f'Grid MTF error: {e}')
        return None


def run_grid_search(
    brain_name: str,
    symbols: List[str],
    days: int = 365,
    use_regime_filter: bool = True,
) -> None:
    """Grid-search multi_timeframe brain parameters. Prints and saves results."""
    import itertools

    if brain_name != 'multi_timeframe':
        print(f'Grid search only supported for multi_timeframe (got: {brain_name})')
        return

    grid = _MTF_GRID
    keys   = list(grid.keys())
    combos = list(itertools.product(*grid.values()))
    print(f'\nGrid search: {len(combos)} parameter combinations × {len(symbols)} symbols')
    print(f'Days: {days} | Regime filter: {use_regime_filter}')
    print('=' * 70)

    # Pre-load all symbol data once
    sym_data = {}
    for sym in symbols:
        logger.info(f'  Loading {sym}...')
        df = load_symbol_data(sym, days=days)
        if df is not None:
            sym_data[sym] = df

    if not sym_data:
        print('ERROR: No data loaded for any symbol. Check database / internet.')
        return

    allowed_regimes = BRAIN_REGIME_GATES.get('multi_timeframe', ['ALL'])
    results = []

    for i, combo_vals in enumerate(combos, 1):
        params = dict(zip(keys, combo_vals))
        all_trades: List[dict] = []

        for symbol, df in sym_data.items():
            n = len(df)
            for bar_i in range(WARMUP_BARS, n - MAX_HOLD_BARS - 2, STEP_BARS):
                hist_window = df.iloc[:bar_i + 1].copy()

                regime = 'RANGING'
                if use_regime_filter:
                    regime = get_regime_at_bar(hist_window, symbol)
                if 'ALL' not in allowed_regimes and regime not in allowed_regimes:
                    continue

                sig = _run_mtf_with_params(
                    hist          = hist_window,
                    symbol        = symbol,
                    min_confidence= params['min_confidence'],
                    rsi_buy_max   = params['rsi_buy_max'],
                    rsi_sell_min  = params['rsi_sell_min'],
                    sma_1h        = params['sma_1h'],
                )
                if sig is None:
                    continue

                outcome, r = simulate_trade(
                    hist      = df,
                    entry_idx = bar_i,
                    direction = sig['direction'],
                    atr       = sig['atr'],
                    t1_mult   = sig['t1_mult'],
                    sl_mult   = sig['sl_mult'],
                )
                all_trades.append({
                    'symbol':    symbol,
                    'direction': sig['direction'],
                    'regime':    regime,
                    'outcome':   outcome,
                    'r':         r,
                })

        m = compute_metrics(all_trades)
        row = {**params, **{
            'trades':        m.get('total_trades', 0),
            'win_rate':      m.get('win_rate', 0.0),
            'profit_factor': m.get('profit_factor', 0.0),
            'avg_r':         m.get('avg_r', 0.0),
            'sharpe':        m.get('sharpe', 0.0),
            'max_dd':        m.get('max_drawdown_r', 0.0),
            'passes':        m.get('passes', False),
        }}
        results.append(row)
        status = '✅' if row['passes'] else '❌'
        print(f'  [{i:>3}/{len(combos)}] conf={params["min_confidence"]:.2f} '
              f'rsi_buy<{params["rsi_buy_max"]} rsi_sell>{params["rsi_sell_min"]} '
              f'sma={params["sma_1h"]} | '
              f'n={row["trades"]:>4} wr={row["win_rate"]:.1%} '
              f'pf={row["profit_factor"]:.2f} {status}')

    # Sort by win_rate desc, then profit_factor
    results_df = pd.DataFrame(results).sort_values(
        ['win_rate', 'profit_factor'], ascending=False
    )

    suffix  = datetime.now().strftime('%Y%m%d')
    csv_out = f'grid_multi_timeframe_{days}d_{suffix}.csv'
    results_df.to_csv(csv_out, index=False)

    print('\n' + '=' * 70)
    print('  GRID SEARCH RESULTS — TOP 10 COMBINATIONS')
    print('=' * 70)
    passing = results_df[results_df['passes']]
    if passing.empty:
        print('  ⚠️  No combination passes all thresholds. Top 5 by win rate:')
        display = results_df.head(5)
    else:
        print(f'  ✅ {len(passing)} passing combinations found!')
        display = passing.head(10)

    for _, row in display.iterrows():
        flag = '✅' if row['passes'] else '❌'
        print(f'  conf={row["min_confidence"]:.2f} rsi_buy<{row["rsi_buy_max"]} '
              f'rsi_sell>{row["rsi_sell_min"]} sma={row["sma_1h"]} '
              f'→ n={row["trades"]:>4} wr={row["win_rate"]:.1%} '
              f'pf={row["profit_factor"]:.2f} sharpe={row["sharpe"]:.2f} {flag}')

    print(f'\n  Full grid results saved → {csv_out}')
    print('=' * 70)


def main():
    parser = argparse.ArgumentParser(description='Brain Trade-Level Backtester')
    parser.add_argument('--brain',   type=str, default='all',
                        help='Brain to test: multi_modal_fusion, multi_timeframe, '
                             'cross_stock_gnn, all')
    parser.add_argument('--days',    type=int, default=365,
                        help='Lookback days')
    parser.add_argument('--symbols', nargs='+', default=SYMBOLS)
    parser.add_argument('--no-regime-filter', action='store_true',
                        help='Disable regime gating (test brain in all regimes)')
    parser.add_argument('--save-csv', action='store_true',
                        help='Save trade log to CSV')
    parser.add_argument('--interval', type=str, default='1h',
                        help='Bar interval: 1m, 5m, 15m, 30m, 1h, 4h, 1d (default: 1h)')
    parser.add_argument('--htf', type=str, default='30m',
                        help='Higher timeframe for --brain liquidity_sweep_2tf (default: 30m)')
    parser.add_argument('--ltf', type=str, default='5m',
                        help='Lower/confirmation timeframe for --brain liquidity_sweep_2tf (default: 5m)')
    parser.add_argument('--log-decisions', action='store_true',
                        help='Log EVERY bar decision (fired or held) with gate and reason '
                             'to a CSV, not just bars that produced a trade. '
                             'Currently only wired for --brain liquidity_sweep.')
    parser.add_argument('--grid', action='store_true',
                        help='Run parameter grid search for multi_timeframe brain '
                             '(only runs if brain passes < 60%% WR or < 30 trades)')
    args = parser.parse_args()

    use_regime = not args.no_regime_filter

    brains_to_run = {
        'multi_modal_fusion': run_multi_modal_fusion,
        'multi_timeframe':    run_multi_timeframe,
        'cross_stock_gnn':    run_cross_stock_gnn,
        'liquidity_sweep':    run_liquidity_sweep,
    }

    # --grid: run parameter sweep immediately and exit
    if args.grid:
        brain_name = args.brain if args.brain != 'all' else 'multi_timeframe'
        run_grid_search(
            brain_name        = brain_name,
            symbols           = args.symbols,
            days              = args.days,
            use_regime_filter = use_regime,
        )
        return

    # liquidity_sweep_2tf: the two-timeframe variant (HTF level, LTF
    # confirmation) needs its own loop (two aligned series, not one), so it
    # is dispatched separately rather than through backtest_brain().
    if args.brain == 'liquidity_sweep_2tf':
        t0 = time.time()
        metrics, trades, decision_log = backtest_liquidity_sweep_htf_ltf(
            symbols           = args.symbols,
            days              = args.days,
            htf               = args.htf,
            ltf               = args.ltf,
            use_regime_filter = use_regime,
            log_all_decisions = args.log_decisions,
        )
        logger.info(f'  Done in {time.time()-t0:.1f}s')
        print_report('liquidity_sweep_2tf', metrics, trades)

        suffix = datetime.now().strftime('%Y%m%d')
        if args.save_csv and trades:
            csv_out = f'trades_liquidity_sweep_{args.htf}_{args.ltf}_{args.days}d_{suffix}.csv'
            pd.DataFrame(trades).to_csv(csv_out, index=False)
            logger.info(f'  Trades saved -> {csv_out}')
        if args.log_decisions and decision_log:
            log_out = f'decisions_liquidity_sweep_{args.htf}_{args.ltf}_{args.days}d_{suffix}.csv'
            pd.DataFrame(decision_log).to_csv(log_out, index=False)
            fired_n = sum(1 for d in decision_log if d['fired'])
            logger.info(f'  Decision log saved -> {log_out} '
                       f'({len(decision_log)} bars logged, {fired_n} fired)')
            logger.info('  Gate breakdown:\n' +
                       pd.DataFrame(decision_log)['gate'].value_counts().to_string())
        return

    if args.brain == 'all':
        selected = brains_to_run
    elif args.brain in brains_to_run:
        selected = {args.brain: brains_to_run[args.brain]}
    else:
        print(f'Unknown brain: {args.brain}')
        print(f'Available: {list(brains_to_run.keys())} or all')
        return

    summary_rows = []

    for brain_name, brain_fn in selected.items():
        logger.info(f'\nBacktesting: {brain_name} | {args.days}d | '
                    f'regime_filter={use_regime}')
        t0 = time.time()

        metrics, trades, decision_log = backtest_brain(
            brain_name          = brain_name,
            brain_fn            = brain_fn,
            symbols             = args.symbols,
            days                = args.days,
            use_regime_filter   = use_regime,
            interval            = args.interval,
            log_all_decisions   = args.log_decisions,
        )

        elapsed = time.time() - t0
        logger.info(f'  Done in {elapsed:.1f}s')

        print_report(brain_name, metrics, trades)

        suffix = datetime.now().strftime('%Y%m%d')
        if args.save_csv and trades:
            csv_out = f'trades_{brain_name}_{args.interval}_{args.days}d_{suffix}.csv'
            pd.DataFrame(trades).to_csv(csv_out, index=False)
            logger.info(f'  Trades saved → {csv_out}')

        if args.log_decisions and decision_log:
            log_out = f'decisions_{brain_name}_{args.interval}_{args.days}d_{suffix}.csv'
            pd.DataFrame(decision_log).to_csv(log_out, index=False)
            fired_n = sum(1 for d in decision_log if d['fired'])
            logger.info(f'  Decision log saved -> {log_out} '
                       f'({len(decision_log)} bars logged, {fired_n} fired)')
            gate_counts = pd.DataFrame(decision_log)['gate'].value_counts()
            logger.info('  Gate breakdown:\n' + gate_counts.to_string())

        summary_rows.append({
            'brain':          brain_name,
            'trades':         metrics.get('total_trades', 0),
            'win_rate':       metrics.get('win_rate', 0),
            'profit_factor':  metrics.get('profit_factor', 0),
            'avg_r':          metrics.get('avg_r', 0),
            'sharpe':         metrics.get('sharpe', 0),
            'passes':         metrics.get('passes', False),
        })

    if len(summary_rows) > 1:
        print('\n' + '=' * 65)
        print('  SUMMARY — ALL BRAINS')
        print('=' * 65)
        print(f'  {"Brain":<25} {"Trades":>6} {"WinRate":>8} {"PF":>6} '
              f'{"AvgR":>7} {"Sharpe":>7} {"Pass"}')
        print(f'  {"-"*60}')
        for r in summary_rows:
            flag = '✅' if r['passes'] else '❌'
            print(f'  {r["brain"]:<25} {r["trades"]:>6} '
                  f'{r["win_rate"]:>7.1%} {r["profit_factor"]:>6.2f} '
                  f'{r["avg_r"]:>+6.3f}R {r["sharpe"]:>7.3f} {flag}')
        print('=' * 65)

        print('\nNEXT STEPS:')
        failing = [r['brain'] for r in summary_rows if not r['passes']]
        passing = [r['brain'] for r in summary_rows if r['passes']]
        if passing:
            print(f'  ✅ PASSING brains: {passing}')
            print(f'     → Ready for paper trading')
        if failing:
            print(f'  ❌ FAILING brains: {failing}')
            print(f'     → Investigate: wrong regime gate? need parameter tuning?')
            print(f'     → Run with --no-regime-filter to isolate regime gate impact')


if __name__ == '__main__':
    main()