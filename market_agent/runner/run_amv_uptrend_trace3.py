"""
run_amv_uptrend_trace3.py
=========================
Traces EXACTLY what happens for the 3 dates identified as false holds:
  LT.NS          2025-09-23
  RELIANCE.NS    2025-03-18
  ITC.NS         2025-04-09

These bars passed every gate in manual check but brain returned HOLD with
reason HTF_W1_ALIGNMENT_GATE. After W1 fix was applied, n is still 3.
This script tells us WHY — either:
  A) Brain is STILL returning HTF_W1_ALIGNMENT_GATE (file not deployed correctly)
  B) Brain is returning a DIFFERENT gate kill reason now
  C) Brain is returning BUY (W1 fix worked but some other bar became a signal)

Run:
  python -X utf8 run_amv_uptrend_trace3.py
"""

import pandas as pd
import logging
from datetime import datetime, timedelta

from market_agent.brain.amv_lstm_uptrend import (
    amv_lstm_uptrend_signal,
    _RSI_PULLBACK_MAX, _RSI_RECOVERY_MIN,
    _MIN_CLOSE_ABOVE_LOW_PCT, _MAX_CLOSE_ABOVE_LOW_PCT,
    _MIN_SMA20_SLOPE_PCT,
)
from market_agent.brain.regime_ensemble   import regime_ensemble_signal
from market_agent.brain.brain_utils       import calc_atr, calc_rsi_float
from market_agent.runner.backtester       import load_ohlcv, MIN_HIST_BARS
from market_agent.data.storage.postgres   import PostgresStorage

logging.basicConfig(level=logging.WARNING)

# The 3 target dates — these MUST become BUY signals after W1 fix
TARGETS = [
    ('LT.NS',       '2025-09-23'),
    ('RELIANCE.NS', '2025-03-18'),
    ('ITC.NS',      '2025-04-09'),
]

TIMEFRAME  = '1d'
END        = datetime.utcnow()
START      = END - timedelta(days=900)
MIN_START  = max(MIN_HIST_BARS, 60)


def main():
    storage = PostgresStorage()

    print('=' * 72)
    print('AMV-LSTM-UPTREND v4.2 — TARGET DATE TRACE')
    print(f'Brain constants loaded:')
    print(f'  _RSI_PULLBACK_MAX        = {_RSI_PULLBACK_MAX}   (expected 60)')
    print(f'  _MIN_SMA20_SLOPE_PCT     = {_MIN_SMA20_SLOPE_PCT}  (expected 0.05)')
    print(f'  _MAX_CLOSE_ABOVE_LOW_PCT = {_MAX_CLOSE_ABOVE_LOW_PCT}  (expected 0.70)')
    print('=' * 72)

    for sym, target_date_str in TARGETS:
        target_date = pd.Timestamp(target_date_str)
        print(f'\n{"─"*72}')
        print(f'TARGET: {sym}  {target_date_str}')
        print(f'{"─"*72}')

        df = load_ohlcv(sym, TIMEFRAME, START, END, storage)
        if df.empty:
            print(f'  ERROR: No data loaded for {sym}')
            continue

        # Find the bar index for target date
        bar_idx = None
        for i, ts in enumerate(df.index):
            if ts.date() == target_date.date():
                bar_idx = i
                break

        if bar_idx is None:
            # Try to find nearest date
            print(f'  WARNING: Exact date {target_date_str} not found in index.')
            print(f'  Available dates around that period:')
            target_ts = pd.Timestamp(target_date_str)
            nearby = [(i, ts) for i, ts in enumerate(df.index)
                      if abs((ts - target_ts).days) <= 5]
            for i, ts in nearby:
                print(f'    bar {i}: {ts.date()}')
            if nearby:
                bar_idx = nearby[0][0]
                print(f'  Using nearest: bar {bar_idx} = {df.index[bar_idx].date()}')
            else:
                print(f'  No bars within 5 days. Skipping.')
                continue

        hist        = df.iloc[:bar_idx]
        future_bars = df.iloc[bar_idx: bar_idx + 30]

        print(f'  Bar index    : {bar_idx}  ({df.index[bar_idx-1].date()})')
        print(f'  hist length  : {len(hist)} bars')
        print(f'  future bars  : {len(future_bars)}')

        # Step 1: Check regime
        try:
            reg    = regime_ensemble_signal(hist)
            regime = reg.measurements.get('computed_regime', 'RANGING')
        except Exception as e:
            print(f'  REGIME ERROR: {e}')
            continue
        print(f'  Regime       : {regime}  (need: TRENDING_UP)')

        if regime != 'TRENDING_UP':
            print(f'  ⚠ REGIME GATE kills this bar — not TRENDING_UP')
            print(f'  This bar cannot produce a BUY regardless of brain gates.')
            continue

        # Step 2: Call brain signal
        try:
            bs = amv_lstm_uptrend_signal(hist, regime='TRENDING_UP')
        except Exception as e:
            print(f'  BRAIN EXCEPTION: {e}')
            continue

        print(f'  Brain result : {bs.direction}')

        if bs.direction == 'BUY':
            print(f'  ✅ BUY SIGNAL — W1 fix is working for this bar')
            m = bs.measurements or {}
            print(f'     RSI        : {m.get("rsi", "?")}')
            print(f'     close_pct  : {m.get("close_pct", "?")}')
            print(f'     sma_slope  : {m.get("sma20_slope", "?")}')
            print(f'     vol_ratio  : {m.get("vol_ratio", "?")}')
            print(f'     confidence : {bs.confidence}')
        else:
            print(f'  ❌ STILL HOLD')
            # Extract reason
            factor  = bs.measurements.get('decision_factor', '?') if bs.measurements else '?'
            evidence = bs.primary_evidence[:120] if bs.primary_evidence else '?'
            print(f'     Kill gate  : {factor}')
            print(f'     Reason     : {evidence}')

            # Additional manual check to understand the values
            close  = hist['Close']
            high   = hist['High']
            low    = hist['Low']
            sma20  = close.rolling(20).mean()
            price  = float(close.iloc[-1])
            atr    = float(calc_atr(hist))
            rsi    = calc_rsi_float(hist)
            sma20c = float(sma20.iloc[-1])
            sma20_slope = float(
                (sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100
            ) if len(sma20) >= 5 and sma20.iloc[-5] else 0.0
            candle_range = float(high.iloc[-1]) - float(low.iloc[-1])
            close_pct = (price - float(low.iloc[-1])) / candle_range if candle_range > 0 else 0
            vol_series = hist['Volume']
            vol_avg   = float(vol_series.iloc[-20:].mean())
            vol_ratio = float(vol_series.iloc[-1]) / vol_avg if vol_avg > 0 else 0

            print(f'')
            print(f'     Manual gate values at this bar:')
            print(f'       price      : {price:.4f}')
            print(f'       sma20      : {sma20c:.4f}  (price above: {price > sma20c})')
            print(f'       rsi        : {rsi:.1f}  (gate: [{_RSI_RECOVERY_MIN},{_RSI_PULLBACK_MAX}]  pass: {_RSI_RECOVERY_MIN <= rsi <= _RSI_PULLBACK_MAX})')
            print(f'       close_pct  : {close_pct:.3f}  (gate: [{_MIN_CLOSE_ABOVE_LOW_PCT},{_MAX_CLOSE_ABOVE_LOW_PCT}]  pass: {_MIN_CLOSE_ABOVE_LOW_PCT <= close_pct <= _MAX_CLOSE_ABOVE_LOW_PCT})')
            print(f'       sma_slope  : {sma20_slope:.4f}%  (gate: >={_MIN_SMA20_SLOPE_PCT}%  pass: {sma20_slope >= _MIN_SMA20_SLOPE_PCT})')
            print(f'       vol_ratio  : {vol_ratio:.3f}  (gate: >=1.2  pass: {vol_ratio >= 1.2})')
            print(f'       atr        : {atr:.4f}')

            # Check W1 slope manually
            try:
                weekly = hist.resample('W').agg(
                    Close=('Close', 'last'),
                    bar_count=('Close', 'count'),
                ).dropna(subset=['Close'])
                weekly = weekly[weekly['bar_count'] >= 3]
                if len(weekly) > 1:
                    weekly = weekly.iloc[:-1]
                if len(weekly) >= 15:
                    w1_sma10 = weekly['Close'].rolling(10).mean()
                    if not pd.isna(w1_sma10.iloc[-5]):
                        w1_slope = float(
                            (w1_sma10.iloc[-1] - w1_sma10.iloc[-5])
                            / w1_sma10.iloc[-5] * 100
                        )
                        print(f'       W1 slope   : {w1_slope:+.4f}%  (gate: >-0.20%  pass: {w1_slope >= -0.20})')
                    else:
                        print(f'       W1 slope   : NaN — insufficient W1 data')
                else:
                    print(f'       W1 bars    : {len(weekly)} (need >=15, gate fails open)')
            except Exception as e:
                print(f'       W1 error   : {e}')

    print(f'\n{"="*72}')
    print('INTERPRETATION:')
    print('  If all 3 show ✅ BUY → W1 fix worked. n=3 means 3 OTHER signals')
    print('    were lost between runs. Check if data period shifted slightly.')
    print('  If any show ❌ STILL HOLD:')
    print('    - Kill gate = HTF_W1_ALIGNMENT_GATE → brain file not updated')
    print('    - Kill gate = something else → new gate killing these bars')
    print('    - Regime = not TRENDING_UP → regime shifted, these bars no longer qualify')
    print(f'{"="*72}')


if __name__ == '__main__':
    main()