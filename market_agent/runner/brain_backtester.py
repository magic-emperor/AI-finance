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

# Regime filtering: only take signals in regimes the brain is designed for
BRAIN_REGIME_GATES = {
    'multi_modal_fusion': ['RANGING', 'SQUEEZE', 'MEAN_REVERTING'],
    'multi_timeframe':    ['TRENDING_UP', 'TRENDING_DOWN',
                           'TRENDING_UP_STRONG', 'TRENDING_DOWN_STRONG',
                           'TRENDING_UP_WEAK', 'TRENDING_DOWN_WEAK'],
    'cross_stock_gnn':    ['VOLATILE', 'RANGING', 'TRENDING_UP', 'TRENDING_DOWN',
                           'TRENDING_UP_WEAK', 'TRENDING_DOWN_WEAK'],
    # Match coordinator gate: Liquidity-Sweep runs in volatility/trend regimes.
    # Note: the brain itself is more selective directionally (e.g., BUY may be blocked in TRENDING_*).
    'liquidity_sweep':    ['VOLATILE', 'TRENDING_UP', 'TRENDING_DOWN'],
    'regime_ensemble':    ['ALL'],   # meta brain — not directional
}


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


def load_symbol_data(symbol: str, days: int = 365) -> Optional[pd.DataFrame]:
    """Load OHLCV from market_data system."""
    try:
        from market_agent.data.ingestion.unified_market_data import market_data

        # Asset-class aware bar count
        asset_bars = {
            'CRYPTO': 24, 'FOREX': 24, 'COMMODITY': 23,
            'EQUITY_INDIA': 6, 'EQUITY_US': 7, 'INDEX': 6, 'UNKNOWN': 7,
        }
        s = symbol.upper()
        if s.endswith('-USD') or 'BTC' in s or 'ETH' in s:
            ac = 'CRYPTO'
        elif s.endswith('.NS') or s.endswith('.BO'):
            ac = 'EQUITY_INDIA'
        elif s.endswith('=X'):
            ac = 'FOREX'
        elif s.endswith('=F'):
            ac = 'COMMODITY'
        else:
            ac = 'EQUITY_US'

        bars = days * asset_bars.get(ac, 7) + WARMUP_BARS
        df = market_data.get_ohlcv(symbol, interval='1h', bars=bars)
        if df is None or len(df) < WARMUP_BARS + 50:
            logger.warning(f'  {symbol}: insufficient data')
            return None
        return df
    except Exception as e:
        logger.error(f'  {symbol}: load failed — {e}')
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


def run_liquidity_sweep(hist: pd.DataFrame, symbol: str, regime: str) -> Optional[dict]:
    """Run Liquidity-Sweep brain (requires regime input)."""
    try:
        from market_agent.brain.liquidity_sweep import liquidity_sweep_signal
        from market_agent.brain.brain_utils import calc_atr

        bs  = liquidity_sweep_signal(hist, symbol=symbol, regime=regime)
        atr = calc_atr(hist, 14)
        if bs.direction in ('BUY', 'SELL') and bs.effective_confidence() >= MIN_CONFIDENCE:
            return {
                'direction':  bs.direction,
                'confidence': bs.effective_confidence(),
                'atr':        atr,
                't1_mult':    bs.rr_t1_mult or 2.0,
                'sl_mult':    bs.rr_sl_mult or 0.75,
                'evidence':   (bs.primary_evidence or '')[:60],
            }
    except Exception as e:
        logger.debug(f'Liquidity-Sweep error: {e}')
    return None


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
) -> dict:
    """
    Run a full walk-forward backtest for one brain across all symbols.

    Returns performance metrics dict.
    """
    allowed_regimes = BRAIN_REGIME_GATES.get(brain_name, ['ALL'])
    all_trades: List[dict] = []
    skipped_signals = 0
    total_signals   = 0

    for symbol in symbols:
        logger.info(f'  {symbol}...')
        df = load_symbol_data(symbol, days=days)
        if df is None:
            continue

        n = len(df)

        for i in range(WARMUP_BARS, n - MAX_HOLD_BARS - 2, STEP_BARS):
            hist_window = df.iloc[:i + 1].copy()

            # Get regime for this bar (used for filtering)
            regime = 'RANGING'
            if use_regime_filter:
                regime = get_regime_at_bar(hist_window, symbol)

            # Check regime gate — only apply when regime filter is active
            if use_regime_filter and 'ALL' not in allowed_regimes and regime not in allowed_regimes:
                continue

            # Run the brain (some brains require regime input)
            if brain_name in ('cross_stock_gnn', 'liquidity_sweep'):
                sig = brain_fn(hist_window, symbol, regime)
            else:
                sig = brain_fn(hist_window, symbol)

            if sig is None:
                continue

            total_signals += 1

            # Get ATR at signal bar
            atr = sig.get('atr', 0.0)

            # Simulate the trade
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
                'bar':        i,
                'date':       str(df.index[i])[:10],
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

    return metrics, all_trades


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

    print(f'\n  CORE METRICS')
    print(f'  {"Total trades:":<25} {t}')
    wr_flag = "✅" if wr >= MIN_WIN_RATE else "❌"
    print(f'  {"Win rate:":<25} {wr:.1%}  {wr_flag}')
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

        metrics, trades = backtest_brain(
            brain_name          = brain_name,
            brain_fn            = brain_fn,
            symbols             = args.symbols,
            days                = args.days,
            use_regime_filter   = use_regime,
        )

        elapsed = time.time() - t0
        logger.info(f'  Done in {elapsed:.1f}s')

        print_report(brain_name, metrics, trades)

        if args.save_csv and trades:
            suffix  = datetime.now().strftime('%Y%m%d')
            csv_out = f'trades_{brain_name}_{args.days}d_{suffix}.csv'
            pd.DataFrame(trades).to_csv(csv_out, index=False)
            logger.info(f'  Trades saved → {csv_out}')

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