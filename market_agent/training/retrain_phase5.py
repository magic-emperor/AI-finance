"""
Phase 5 — Walk-Forward Backtester using Phase 4 BrainSignal functions.

Each brain makes a prediction at every bar. We check if the next 6 candles hit
the ATR-based T1 target (+ATR*0.75) or SL (-ATR*0.50) to score correct/wrong.

Usage:
    python -m market_agent.training.retrain_phase5

Output:
    - Accuracy table per brain (in terminal)
    - retrain_results.json (per-brain, per-symbol accuracy — used by health_monitor)
"""
import json
import time
import traceback
import pandas as pd
import numpy as np
import structlog
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple

from market_agent.data.storage.postgres import PostgresStorage
from market_agent.brain.signal_generators import (
    amv_lstm_signal, regime_ensemble_signal, multi_modal_fusion_signal,
    multi_timeframe_signal, cross_stock_gnn_signal, rl_weighter_signal,
    causal_ensemble_signal,
)
from market_agent.brain.brain_contract import BrainSignal

logger = structlog.get_logger()

# ── Config ───────────────────────────────────────────────────────────────────
SYMBOLS = [
    'ITC.NS', 'HDFCBANK.NS', 'RELIANCE.NS', 'TATASTEEL.NS',
    'LT.NS', 'M&M.NS', 'ADANIENT.NS', 'ADANIPORTS.NS',
    '^NSEBANK', 'NVDA', 'GOOGL', 'AAPL', 'AMD',
    'BTC-USD', 'GC=F', 'CL=F', 'GBPJPY=X', 'USDJPY=X',
]
TIMEFRAME       = '1h'       # primary OHLCV timeframe
WINDOW_SIZE     = 100        # bars fed to each brain (100 candles = 4+ days on 1h)
EVAL_HORIZON    = 6          # check next 6 candles for T1/SL hit
ATR_PERIOD      = 14
# T1_MULT and SL_MULT imported from signal_params — single source of truth
# DO NOT hardcode here: if signal_params changes, training must use the same R:R as live trading
from market_agent.signal_params import BASE_ATR_T1_MULT as T1_MULT, BASE_ATR_SL_MULT as SL_MULT
RESULTS_FILE    = Path(__file__).parent.parent.parent / 'retrain_results.json'
MIN_BARS        = WINDOW_SIZE + EVAL_HORIZON + ATR_PERIOD + 5


# ── Brain registry ───────────────────────────────────────────────────────────
def _run_brain(brain_id: str, window: pd.DataFrame) -> Tuple[str, float]:
    """Call the appropriate Phase 4 brain function. Returns (direction, confidence)."""
    try:
        if brain_id == 'AMV-LSTM':
            bs = amv_lstm_signal(window)
        elif brain_id == 'Regime Ensemble':
            bs = regime_ensemble_signal(window)
        elif brain_id == 'Multi-Modal Fusion':
            bs = multi_modal_fusion_signal(window)
        elif brain_id == 'Multi-Timeframe':
            bs = multi_timeframe_signal('_', window)
        elif brain_id == 'Cross-Stock GNN':
            bs = cross_stock_gnn_signal(window)
        elif brain_id == 'RL Weighter':
            bs = rl_weighter_signal({'direction': 'HOLD', 'confidence': 0.5}, [])
        elif brain_id == 'Causal Ensemble':
            bs = causal_ensemble_signal(window)
        else:
            return 'HOLD', 0.0
        return bs.direction, bs.confidence
    except Exception:
        return 'HOLD', 0.0


BRAIN_IDS = [
    'AMV-LSTM', 'Regime Ensemble', 'Multi-Modal Fusion',
    'Multi-Timeframe', 'Cross-Stock GNN', 'RL Weighter', 'Causal Ensemble',
]


# ── ATR helper ───────────────────────────────────────────────────────────────
def _atr(df: pd.DataFrame, period: int = 14) -> float:
    high, low, prev_close = df['High'], df['Low'], df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    val = tr.rolling(period).mean().iloc[-1]
    return float(val) if pd.notna(val) else float(df['Close'].std() * 0.01)


# ── Walk-forward evaluator ───────────────────────────────────────────────────
def evaluate_symbol(hist: pd.DataFrame, symbol: str) -> Dict:
    """
    Walk-forward: at each bar from WINDOW_SIZE to end-EVAL_HORIZON:
      1. Feed last WINDOW_SIZE bars to each brain
      2. Record predicted direction
      3. Check if next EVAL_HORIZON candles hit T1 or SL first
      4. Score: correct if actual_outcome == predicted direction
    Returns accuracy per brain.
    """
    # Scores dict: brain_id -> {correct, wrong, abstain}
    scores = {b: {'correct': 0, 'wrong': 0, 'abstain': 0} for b in BRAIN_IDS}

    total_bars = len(hist)
    evaluated  = 0

    for i in range(WINDOW_SIZE, total_bars - EVAL_HORIZON):
        window  = hist.iloc[i - WINDOW_SIZE : i].copy()
        future  = hist.iloc[i : i + EVAL_HORIZON]

        atr = _atr(window)
        if atr <= 0:
            continue

        entry_price = float(window['Close'].iloc[-1])
        t1_up   = entry_price + atr * T1_MULT
        sl_down = entry_price - atr * SL_MULT
        t1_down = entry_price - atr * T1_MULT
        sl_up   = entry_price + atr * SL_MULT

        # Determine actual outcome from future bars
        actual_outcome = 'HOLD'
        for _, bar in future.iterrows():
            if bar['High'] >= t1_up and bar['Low'] > sl_down:
                actual_outcome = 'BUY'
                break
            if bar['Low'] <= sl_down and bar['High'] < t1_up:
                actual_outcome = 'SELL'
                break
            if bar['Low'] <= t1_down and bar['High'] < sl_up:
                actual_outcome = 'SELL'
                break

        evaluated += 1

        for brain_id in BRAIN_IDS:
            direction, confidence = _run_brain(brain_id, window)

            if direction == 'HOLD' or confidence < 0.35:
                scores[brain_id]['abstain'] += 1
                continue

            if direction == actual_outcome:
                scores[brain_id]['correct'] += 1
            else:
                scores[brain_id]['wrong'] += 1

    # Compute accuracy
    result = {'symbol': symbol, 'evaluated_bars': evaluated, 'brains': {}}
    for brain_id, s in scores.items():
        total = s['correct'] + s['wrong']
        acc   = s['correct'] / total if total > 0 else 0.0
        result['brains'][brain_id] = {
            'correct':  s['correct'],
            'wrong':    s['wrong'],
            'abstain':  s['abstain'],
            'accuracy': round(acc, 4),
        }

    return result


# ── Main runner ──────────────────────────────────────────────────────────────
def main():
    print('\n' + '='*70)
    print('PHASE 5 — Walk-Forward Backtest (Phase 4 BrainSignal functions)')
    print('='*70)

    storage = PostgresStorage()
    all_results = []
    brain_totals = {b: {'correct': 0, 'wrong': 0} for b in BRAIN_IDS}

    for symbol in SYMBOLS:
        t0 = time.time()
        records = storage.get_latest_data(symbol, TIMEFRAME, limit=10000)
        if not records or len(records) < MIN_BARS:
            print(f'  SKIP {symbol}: only {len(records) if records else 0} bars (need {MIN_BARS})')
            continue

        hist = pd.DataFrame([{**r['data'], 'timestamp': r['timestamp']} for r in records])
        hist.set_index('timestamp', inplace=True)
        hist.sort_index(inplace=True)
        hist = hist[['Open', 'High', 'Low', 'Close', 'Volume']].dropna()

        print(f'\n  {symbol} ({len(hist)} bars)...')
        result = evaluate_symbol(hist, symbol)
        all_results.append(result)

        elapsed = time.time() - t0
        print(f'    Evaluated {result["evaluated_bars"]} bars in {elapsed:.1f}s')
        for brain_id, s in result['brains'].items():
            acc_str = f'{s["accuracy"]:.1%}' if (s["correct"] + s["wrong"]) > 0 else '  --  '
            print(f'    [{brain_id:25s}] acc={acc_str} ({s["correct"]}W/{s["wrong"]}L/{s["abstain"]}A)')
            brain_totals[brain_id]['correct'] += s['correct']
            brain_totals[brain_id]['wrong']   += s['wrong']

    # ── Aggregate summary ─────────────────────────────────────────────────
    print('\n' + '='*70)
    print('AGGREGATE ACCURACY (all symbols combined)')
    print('='*70)
    aggregate = {}
    for brain_id, totals in brain_totals.items():
        total = totals['correct'] + totals['wrong']
        acc   = totals['correct'] / total if total > 0 else 0.0
        aggregate[brain_id] = {'accuracy': round(acc, 4), **totals, 'total_trades': total}
        print(f'  [{brain_id:25s}] {acc:.1%}  ({totals["correct"]}W / {totals["wrong"]}L / {total} total)')

    # ── Write results JSON ────────────────────────────────────────────────
    output = {
        'generated_at': datetime.utcnow().isoformat(),
        'timeframe':    TIMEFRAME,
        'window_size':  WINDOW_SIZE,
        'eval_horizon': EVAL_HORIZON,
        'aggregate':    aggregate,
        'per_symbol':   all_results,
    }
    with open(RESULTS_FILE, 'w') as f:
        json.dump(output, f, indent=2)
    print(f'\nResults saved to: {RESULTS_FILE}')
    print('Phase 5 retrain complete.\n')


if __name__ == '__main__':
    main()