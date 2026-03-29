"""
run_amv_uptrend_funnel_diag.py
==============================
WHY does the confirm runner produce n=3 when the RSI diagnostic
found 43 qualifying bars?

The RSI diagnostic used LOOSENED gates (slope >= 0.05%, close_pct >= 0.40)
and called _passes_non_rsi_gates() directly.
The confirm runner calls amv_lstm_uptrend_signal() which has the full gate stack.

This diagnostic traces EVERY bar that the confirm runner rejects and prints
the EXACT gate kill reason and values. We will see precisely where the gap is.

Also checks: does the confirm runner correctly read _MAX_CLOSE_ABOVE_LOW_PCT?
Is the close_pct upper cap actually working in the brain function?

Run:
  python -X utf8 run_amv_uptrend_funnel_diag.py
"""

import pandas as pd
import logging
from datetime import datetime, timedelta
from collections import Counter

from market_agent.brain.regime_ensemble   import regime_ensemble_signal
from market_agent.brain.amv_lstm_uptrend  import (
    amv_lstm_uptrend_signal,
    _MIN_SMA20_SLOPE_PCT, _MIN_CLOSE_ABOVE_LOW_PCT, _MAX_CLOSE_ABOVE_LOW_PCT,
    _RSI_PULLBACK_MAX, _RSI_RECOVERY_MIN,
    _ATR_EXPANSION_FACTOR, _MIN_BOUNCE_VOL_RATIO,
    _PULLBACK_ATR_DISTANCE, _PULLBACK_LOOKBACK_BARS, _MIN_PULLBACK_DROP_ATR,
)
from market_agent.brain.brain_utils       import calc_atr, calc_rsi_float
from market_agent.runner.backtester       import load_ohlcv, MIN_HIST_BARS
from market_agent.data.storage.postgres   import PostgresStorage

logging.basicConfig(level=logging.WARNING)

SYMBOLS        = ['LT.NS', 'TATASTEEL.NS', 'AAPL', 'RELIANCE.NS', 'ITC.NS', 'AMD', 'GOOGL']
TIMEFRAME      = '1d'
END            = datetime.utcnow()
START          = END - timedelta(days=900)
MIN_START      = max(MIN_HIST_BARS, 60)
MAX_BARS_TRADE = 30


def _manual_gate_check(hist: pd.DataFrame) -> dict:
    """
    Manually re-run every gate in order using the SAME logic as the brain.
    Returns dict with each gate result AND raw values.
    This lets us compare against what amv_lstm_uptrend_signal() returns.
    """
    close    = hist['Close']
    high     = hist['High']
    low      = hist['Low']
    sma20    = close.rolling(20).mean()
    price    = float(close.iloc[-1])
    atr_val  = float(calc_atr(hist))
    rsi      = calc_rsi_float(hist)

    sma20_curr  = float(sma20.iloc[-1])
    sma20_slope = (
        float((sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100)
        if len(sma20) >= 5 and sma20.iloc[-5] else 0.0
    )

    vol_series = hist['Volume']
    vol_avg    = float(vol_series.iloc[-20:].mean()) if len(vol_series) >= 20 \
                 else float(vol_series.mean())
    vol_ratio  = float(vol_series.iloc[-1]) / vol_avg if vol_avg > 0 else 1.0

    if len(hist) >= 15:
        tr = pd.concat([
            high - low,
            (high - close.shift(1)).abs(),
            (low  - close.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr_avg_14 = float(tr.iloc[-14:].mean())
    else:
        atr_avg_14 = atr_val
    atr_expanding = atr_val >= atr_avg_14 * _ATR_EXPANSION_FACTOR

    pullback_touched = False
    pullback_method  = 'NONE'
    for j in range(1, min(_PULLBACK_LOOKBACK_BARS + 1, len(hist))):
        if abs(float(low.iloc[-j]) - sma20_curr) <= _PULLBACK_ATR_DISTANCE * atr_val:
            pullback_touched = True
            pullback_method  = 'METHOD_A'
            break
    if not pullback_touched and len(hist) >= 5:
        recent_closes = close.iloc[-4:]
        bar_changes   = recent_closes.diff().dropna()
        down_bars     = int((bar_changes < 0).sum())
        total_drop    = float(recent_closes.max() - recent_closes.iloc[-1])
        if down_bars >= 2 and total_drop >= _MIN_PULLBACK_DROP_ATR * atr_val:
            pullback_touched = True
            pullback_method  = 'METHOD_B'

    candle_low   = float(low.iloc[-1])
    candle_high  = float(high.iloc[-1])
    candle_range = candle_high - candle_low if candle_high > candle_low else atr_val * 0.1
    close_pct    = (price - candle_low) / candle_range

    # Gate evaluation in exact brain order
    gates = {}
    gates['G1_SMA_SLOPE']    = {'pass': sma20_slope >= _MIN_SMA20_SLOPE_PCT,
                                 'val': sma20_slope,
                                 'threshold': f'>= {_MIN_SMA20_SLOPE_PCT}%'}
    gates['G2_PULLBACK']     = {'pass': pullback_touched,
                                 'val': pullback_method,
                                 'threshold': 'METHOD_A or METHOD_B'}
    gates['G3_ABOVE_SMA20']  = {'pass': price > sma20_curr,
                                 'val': round((price - sma20_curr) / sma20_curr * 100, 3),
                                 'threshold': 'price > SMA20 (val = % above)'}
    gates['G4_CLOSE_MIN']    = {'pass': close_pct >= _MIN_CLOSE_ABOVE_LOW_PCT,
                                 'val': round(close_pct, 3),
                                 'threshold': f'>= {_MIN_CLOSE_ABOVE_LOW_PCT}'}
    gates['G4_CLOSE_MAX']    = {'pass': close_pct <= _MAX_CLOSE_ABOVE_LOW_PCT,
                                 'val': round(close_pct, 3),
                                 'threshold': f'<= {_MAX_CLOSE_ABOVE_LOW_PCT}'}
    gates['G5A_RSI_OB']      = {'pass': rsi <= 70,
                                 'val': round(rsi, 1),
                                 'threshold': '<= 70'}
    gates['G5B_RSI_LOW']     = {'pass': rsi >= _RSI_RECOVERY_MIN,
                                 'val': round(rsi, 1),
                                 'threshold': f'>= {_RSI_RECOVERY_MIN}'}
    gates['G5C_RSI_ZONE']    = {'pass': rsi <= _RSI_PULLBACK_MAX,
                                 'val': round(rsi, 1),
                                 'threshold': f'<= {_RSI_PULLBACK_MAX}'}
    gates['G6_VOLUME']       = {'pass': vol_ratio >= _MIN_BOUNCE_VOL_RATIO,
                                 'val': round(vol_ratio, 3),
                                 'threshold': f'>= {_MIN_BOUNCE_VOL_RATIO}'}
    gates['G7_ATR']          = {'pass': atr_expanding,
                                 'val': f'{atr_val:.4f} vs avg {atr_avg_14:.4f}',
                                 'threshold': f'>= {_ATR_EXPANSION_FACTOR}× avg'}

    first_fail = next((k for k, v in gates.items() if not v['pass']), None)
    return {
        'gates':          gates,
        'first_fail':     first_fail,
        'all_pass':       first_fail is None,
        'rsi':            round(rsi, 1),
        'close_pct':      round(close_pct, 3),
        'sma20_slope':    round(sma20_slope, 4),
        'vol_ratio':      round(vol_ratio, 3),
        'pullback_method': pullback_method,
        'price':          round(price, 4),
        'sma20_curr':     round(sma20_curr, 4),
    }


def main():
    storage = PostgresStorage()

    print('=' * 72)
    print('AMV-LSTM-UPTREND v4.1 — DEEP FUNNEL DIAGNOSTIC')
    print(f'Period: {START.date()} → {END.date()}')
    print(f'Brain constants:')
    print(f'  RSI gate     : [{_RSI_RECOVERY_MIN}, {_RSI_PULLBACK_MAX}]')
    print(f'  close_pct    : [{_MIN_CLOSE_ABOVE_LOW_PCT}, {_MAX_CLOSE_ABOVE_LOW_PCT}]')
    print(f'  SMA slope    : >= {_MIN_SMA20_SLOPE_PCT}%')
    print(f'  Vol ratio    : >= {_MIN_BOUNCE_VOL_RATIO}×')
    print(f'  ATR factor   : {_ATR_EXPANSION_FACTOR}')
    print('=' * 72)

    # PART 1: Verify constants are actually loaded correctly from brain
    print()
    print('── PART 1: Constant Verification ──────────────────────────────────────')
    print(f'  _RSI_PULLBACK_MAX        = {_RSI_PULLBACK_MAX}   (expected: 60)')
    print(f'  _RSI_RECOVERY_MIN        = {_RSI_RECOVERY_MIN}   (expected: 45)')
    print(f'  _MIN_CLOSE_ABOVE_LOW_PCT = {_MIN_CLOSE_ABOVE_LOW_PCT}  (expected: 0.50)')
    print(f'  _MAX_CLOSE_ABOVE_LOW_PCT = {_MAX_CLOSE_ABOVE_LOW_PCT}  (expected: 0.70)')
    print(f'  _MIN_SMA20_SLOPE_PCT     = {_MIN_SMA20_SLOPE_PCT}  (expected: 0.05)')
    print(f'  _MIN_BOUNCE_VOL_RATIO    = {_MIN_BOUNCE_VOL_RATIO}   (expected: 1.2)')
    print(f'  _ATR_EXPANSION_FACTOR    = {_ATR_EXPANSION_FACTOR}  (expected: 0.85)')

    errors = []
    if _RSI_PULLBACK_MAX != 60:  errors.append(f'RSI_PULLBACK_MAX={_RSI_PULLBACK_MAX} should be 60')
    if _MIN_SMA20_SLOPE_PCT != 0.05: errors.append(f'SMA_SLOPE={_MIN_SMA20_SLOPE_PCT} should be 0.05')
    if _MAX_CLOSE_ABOVE_LOW_PCT != 0.70: errors.append(f'MAX_CLOSE={_MAX_CLOSE_ABOVE_LOW_PCT} should be 0.70')

    if errors:
        print()
        print('  ⚠⚠ CONSTANT MISMATCH DETECTED:')
        for e in errors:
            print(f'     {e}')
        print('  The brain file in your environment does not match the updated v4.1.')
        print('  You may have deployed the old file. Check file paths and re-deploy.')
    else:
        print('  ✅ All constants match v4.1 expected values.')

    # PART 2: Full per-bar trace — TRENDING_UP bars, gate kill reason
    print()
    print('── PART 2: Full Gate Kill Trace (all TRENDING_UP bars) ─────────────────')
    print('  This shows EVERY bar the confirm runner sees and what kills it.')

    gate_kills      = Counter()
    brain_signal_n  = 0   # signals from amv_lstm_uptrend_signal()
    manual_pass_n   = 0   # bars that pass manual gate check
    mismatch_n      = 0   # bars where brain and manual disagree

    # Collect mismatch examples for inspection
    mismatches = []
    # Collect all TRENDING_UP bars that pass manual check but brain says HOLD
    false_holds = []

    for sym in SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, START, END, storage)
        if df.empty:
            continue

        n = len(df)
        sym_brain_n  = 0
        sym_manual_n = 0

        for i in range(MIN_START, n - MAX_BARS_TRADE):
            hist = df.iloc[:i]

            try:
                reg    = regime_ensemble_signal(hist)
                regime = reg.measurements.get('computed_regime', 'RANGING')
            except Exception:
                continue
            if regime != 'TRENDING_UP':
                continue

            # Manual gate check
            manual = _manual_gate_check(hist)

            # Brain signal
            try:
                bs = amv_lstm_uptrend_signal(hist, regime='TRENDING_UP')
            except Exception as e:
                print(f'  {sym} bar {i}: brain exception — {e}')
                continue

            brain_pass  = bs.direction == 'BUY'
            manual_pass = manual['all_pass']

            gate_kills[manual['first_fail'] or 'ALL_PASS'] += 1

            if manual_pass:
                manual_pass_n += 1
                sym_manual_n  += 1

            if brain_pass:
                brain_signal_n += 1
                sym_brain_n    += 1

            # Mismatch detection
            if manual_pass and not brain_pass:
                false_holds.append({
                    'sym':         sym,
                    'date':        df.index[i-1].date(),
                    'brain_factor': bs.measurements.get('decision_factor', '?')
                                    if bs.measurements else bs.primary_evidence[:60],
                    'rsi':         manual['rsi'],
                    'close_pct':   manual['close_pct'],
                    'sma_slope':   manual['sma20_slope'],
                    'vol_ratio':   manual['vol_ratio'],
                    'pullback':    manual['pullback_method'],
                })
                mismatch_n += 1

            if not manual_pass and brain_pass:
                mismatches.append({
                    'sym':      sym,
                    'date':     df.index[i-1].date(),
                    'type':     'BRAIN_PASS_MANUAL_FAIL',
                    'fail_gate': manual['first_fail'],
                    'rsi':      manual['rsi'],
                    'close_pct': manual['close_pct'],
                })
                mismatch_n += 1

        print(f'  {sym:<16}: brain_signals={sym_brain_n}  manual_pass={sym_manual_n}')

    print()
    print(f'  TOTAL:')
    print(f'    Brain BUY signals  : {brain_signal_n}  (confirm runner sees this as n=3)')
    print(f'    Manual gate passes : {manual_pass_n}')
    print(f'    Mismatches         : {mismatch_n}')

    # PART 3: Gate kill breakdown
    print()
    print('── PART 3: Gate Kill Breakdown (TRENDING_UP bars, manual gates) ────────')
    tu_total = sum(gate_kills.values())
    print(f'  Total TRENDING_UP bars processed: {tu_total}')
    print()
    print(f'  {"Gate / Result":<35} {"Count":>7}  {"% of TU":>9}')
    all_pass_n = gate_kills.get('ALL_PASS', 0)
    for gate, cnt in sorted(gate_kills.items(), key=lambda x: -x[1]):
        pct = cnt / tu_total * 100 if tu_total > 0 else 0
        marker = '  ← SIGNALS' if gate == 'ALL_PASS' else ''
        print(f'  {gate:<35} {cnt:>7}  {pct:>8.1f}%{marker}')

    # PART 4: False hold analysis
    print()
    print('── PART 4: Bars Manual-Pass But Brain Says HOLD ────────────────────────')
    if not false_holds:
        print('  Zero false holds — brain and manual agree on all bars.')
        print('  The n=3 count is correct — only 3 bars pass ALL gates.')
        print()
        print('  THIS MEANS: The gate constants in the brain are working correctly')
        print('  BUT the remaining gates (G2_PULLBACK, G4_CLOSE, G6_VOLUME, G7_ATR,')
        print('  G8_W1) are still killing most bars after the RSI/slope loosening.')
        print()
        print('  SEE PART 3 for the exact gate kill distribution.')
    else:
        print(f'  Found {len(false_holds)} bars where manual check passes but brain says HOLD.')
        print('  This means the brain file has a bug or is not the v4.1 version.')
        print()
        print(f'  {"Symbol":<14} {"Date":<12} {"Brain reason":<35} '
              f'{"RSI":>5} {"CP":>6} {"Slope":>7}')
        for fh in false_holds[:20]:
            print(f'  {fh["sym"]:<14} {str(fh["date"]):<12} {str(fh["brain_factor"]):<35} '
                  f'{fh["rsi"]:>5.1f} {fh["close_pct"]:>6.2f} {fh["sma_slope"]:>7.3f}')

    # PART 5: The key question — which gate is the new primary killer?
    print()
    print('── PART 5: Dominant Kill Gate Analysis ─────────────────────────────────')
    print('  After RSI ceiling 55→60 and slope 0.10→0.05, which gate kills most?')
    print()

    # Show sorted kills excluding ALL_PASS
    kills_only = {k: v for k, v in gate_kills.items() if k != 'ALL_PASS'}
    if kills_only:
        top_killer = max(kills_only, key=kills_only.get)
        total_kills = sum(kills_only.values())
        print(f'  Dominant killer: {top_killer} ({kills_only[top_killer]} bars, '
              f'{kills_only[top_killer]/tu_total*100:.1f}% of TRENDING_UP)')
        print()
        print('  Full kill distribution:')
        for gate, cnt in sorted(kills_only.items(), key=lambda x: -x[1]):
            bar = '█' * int(cnt / max(kills_only.values()) * 30)
            print(f'  {gate:<30} {cnt:>5}  {bar}')

        print()
        # Give targeted advice based on dominant killer
        if top_killer == 'G2_PULLBACK':
            print('  PRIMARY PROBLEM: Pullback detection is too strict.')
            print('  Only bars where price was within 1.0×ATR of SMA20 in last 5 days')
            print('  OR had 2+ consecutive down closes pass this gate.')
            print('  FIX OPTIONS:')
            print('    A) Widen ATR distance: 1.0 → 1.5×ATR')
            print('    B) Extend lookback window: 5 → 7 bars')
            print('    C) Lower Method B drop threshold: 0.4 → 0.3×ATR')
        elif top_killer == 'G4_CLOSE_MIN':
            print('  PRIMARY PROBLEM: Bounce candle close_pct still too strict.')
            print('  Close in 0.40-0.50 range was negative in diagnostic.')
            print('  But if this is the top killer, worth checking if n=5 was enough.')
            print('  Consider running more data (extend to 5 years) before changing.')
        elif top_killer == 'G4_CLOSE_MAX':
            print('  PRIMARY PROBLEM: Upper cap 0.70 is blocking too many signals.')
            print('  Consider raising to 0.75 and re-running RSI diagnostic on close_pct zone.')
        elif top_killer == 'G6_VOLUME':
            print('  PRIMARY PROBLEM: Volume requirement 1.2× is too strict.')
            print('  Consider lowering to 1.0× (above average, not 20% above).')
        elif top_killer == 'G7_ATR':
            print('  PRIMARY PROBLEM: ATR expansion factor 0.85 still blocking too many bars.')
            print('  Consider lowering to 0.75 or removing this gate entirely.')
            print('  On D1, a bounce can be real without ATR expansion.')
        elif top_killer == 'G8_W1':
            print('  PRIMARY PROBLEM: W1 alignment gate is blocking signals.')
            print('  Check: is W1 SMA10 declining while D1 is trending up?')
            print('  This could be a period mismatch — W1 trend lags D1 by weeks.')

    print()
    print('=' * 72)
    print('NEXT STEPS based on findings:')
    print('  1. If Part 1 shows constant mismatch → redeploy brain file, re-run confirm.')
    print('  2. If Part 4 shows false holds → brain has a bug, review gate code.')
    print('  3. If Part 3/5 shows a clear dominant killer → discuss that specific gate.')
    print('  4. Post this full output before changing anything.')
    print('=' * 72)


if __name__ == '__main__':
    main()