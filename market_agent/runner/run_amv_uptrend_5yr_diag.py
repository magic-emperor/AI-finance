"""
run_amv_uptrend_5yr_diag.py
============================
Two questions answered in one run on the full 5-year dataset:

QUESTION 1: Why did TATASTEEL.NS and RELIANCE.NS produce zero signals
            over 5 years while other symbols produced 1-2 each?
            Is it regime coverage? Gate kill pattern? Or data quality?

QUESTION 2: Does lowering close_pct lower bound from 0.50 → 0.45
            show positive forward edge on the 5-year dataset?
            Previous diagnostic used 2.5yr data (n=5 in 0.40-0.50 zone).
            Now we have ~double the data for a better measurement.

Also reports: full gate kill breakdown per symbol, and the close_pct
forward-outcome measurement for zones [0.40-0.45], [0.45-0.50], [0.50-0.70].

Run:
  python -X utf8 run_amv_uptrend_5yr_diag.py
"""

import pandas as pd
import logging
from datetime import datetime, timedelta
from collections import Counter, defaultdict

from market_agent.brain.regime_ensemble   import regime_ensemble_signal
from market_agent.brain.amv_lstm_uptrend  import (
    _MIN_SMA20_SLOPE_PCT, _MIN_CLOSE_ABOVE_LOW_PCT, _MAX_CLOSE_ABOVE_LOW_PCT,
    _RSI_PULLBACK_MAX, _RSI_RECOVERY_MIN,
    _ATR_EXPANSION_FACTOR, _MIN_BOUNCE_VOL_RATIO,
    _PULLBACK_ATR_DISTANCE, _PULLBACK_LOOKBACK_BARS, _MIN_PULLBACK_DROP_ATR,
    _RR_T1_MULT, _RR_SL_MULT,
    _MIN_W1_BARS,
)
from market_agent.brain.brain_utils       import calc_atr, calc_rsi_float
from market_agent.runner.backtester       import load_ohlcv, MIN_HIST_BARS
from market_agent.data.storage.postgres   import PostgresStorage

logging.basicConfig(level=logging.WARNING)

SYMBOLS        = ['LT.NS', 'TATASTEEL.NS', 'AAPL', 'RELIANCE.NS', 'ITC.NS', 'AMD', 'GOOGL']
ZERO_SYM       = {'TATASTEEL.NS', 'RELIANCE.NS'}   # produced n=0 in confirm
TIMEFRAME      = '1d'
END            = datetime.utcnow()
START          = END - timedelta(days=1825)         # full 5 years
MIN_START      = max(MIN_HIST_BARS, 60)
FORWARD_BARS   = 30
_SLIPPAGE      = 0.0005

# close_pct zones for forward-outcome measurement
CPCT_ZONES = [
    ('0.40-0.45  [below current gate]', 0.40, 0.45),
    ('0.45-0.50  [below current gate]', 0.45, 0.50),
    ('0.50-0.70  [current gate zone]',  0.50, 0.70),
    ('>= 0.70    [exhausted — blocked]',0.70, 1.01),
]


def _manual_gates(hist: pd.DataFrame, close_pct_min: float = 0.40) -> dict:
    """Run all gates except close_pct lower bound — return values and first fail."""
    close  = hist['Close']
    high   = hist['High']
    low    = hist['Low']
    sma20  = close.rolling(20).mean()
    price  = float(close.iloc[-1])
    atr    = float(calc_atr(hist))
    rsi    = calc_rsi_float(hist)

    if atr <= 0:
        return {'pass': False, 'first_fail': 'ATR_ZERO'}

    sma20c     = float(sma20.iloc[-1])
    sma20_slope = float(
        (sma20.iloc[-1] - sma20.iloc[-5]) / sma20.iloc[-5] * 100
    ) if len(sma20) >= 5 and sma20.iloc[-5] else 0.0

    vol_series = hist['Volume']
    vol_avg    = float(vol_series.iloc[-20:].mean()) if len(vol_series) >= 20 \
                 else float(vol_series.mean())
    vol_ratio  = float(vol_series.iloc[-1]) / vol_avg if vol_avg > 0 else 1.0

    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr_avg_14   = float(tr.iloc[-14:].mean()) if len(hist) >= 14 else atr
    atr_expanding = atr >= atr_avg_14 * _ATR_EXPANSION_FACTOR

    pullback_touched = False
    pullback_method  = 'NONE'
    for j in range(1, min(_PULLBACK_LOOKBACK_BARS + 1, len(hist))):
        if abs(float(low.iloc[-j]) - sma20c) <= _PULLBACK_ATR_DISTANCE * atr:
            pullback_touched = True
            pullback_method  = 'METHOD_A'
            break
    if not pullback_touched and len(hist) >= 5:
        rc = close.iloc[-4:]
        bc = rc.diff().dropna()
        if int((bc < 0).sum()) >= 2 and float(rc.max() - rc.iloc[-1]) >= _MIN_PULLBACK_DROP_ATR * atr:
            pullback_touched = True
            pullback_method  = 'METHOD_B'

    candle_range = float(high.iloc[-1]) - float(low.iloc[-1])
    close_pct    = (price - float(low.iloc[-1])) / candle_range \
                   if candle_range > 0 else 0.0

    # W1 slope
    w1_slope = None
    try:
        weekly = hist.resample('W').agg(
            Close=('Close', 'last'), bar_count=('Close', 'count')
        ).dropna(subset=['Close'])
        weekly = weekly[weekly['bar_count'] >= 3]
        if len(weekly) > 1:
            weekly = weekly.iloc[:-1]
        if len(weekly) >= _MIN_W1_BARS:
            w1_sma10 = weekly['Close'].rolling(10).mean()
            if not pd.isna(w1_sma10.iloc[-5]) and float(w1_sma10.iloc[-5]) != 0:
                w1_slope = float(
                    (w1_sma10.iloc[-1] - w1_sma10.iloc[-5])
                    / w1_sma10.iloc[-5] * 100
                )
    except Exception:
        pass

    # Evaluate gates in order
    gates = {
        'G1_SMA_SLOPE':   sma20_slope >= _MIN_SMA20_SLOPE_PCT,
        'G2_PULLBACK':    pullback_touched,
        'G3_ABOVE_SMA20': price > sma20c,
        'G4_CLOSE_MIN':   close_pct >= close_pct_min,
        'G4_CLOSE_MAX':   close_pct <= _MAX_CLOSE_ABOVE_LOW_PCT,
        'G5A_RSI_OB':     rsi <= 70,
        'G5B_RSI_LOW':    rsi >= _RSI_RECOVERY_MIN,
        'G5C_RSI_ZONE':   rsi <= _RSI_PULLBACK_MAX,
        'G6_VOLUME':      vol_ratio >= _MIN_BOUNCE_VOL_RATIO,
        'G7_ATR':         atr_expanding,
        'G8_W1':          (w1_slope is None or w1_slope >= -0.20),
    }
    first_fail = next((k for k, v in gates.items() if not v), None)

    return {
        'pass':           first_fail is None,
        'first_fail':     first_fail,
        'close_pct':      round(close_pct, 3),
        'rsi':            round(rsi, 1),
        'sma20_slope':    round(sma20_slope, 4),
        'vol_ratio':      round(vol_ratio, 3),
        'w1_slope':       round(w1_slope, 4) if w1_slope is not None else None,
        'pullback_method': pullback_method,
        'price':          round(price, 4),
        'atr':            round(atr, 4),
    }


def _forward_outcome(entry: float, atr: float, future: pd.DataFrame) -> dict:
    es = entry * (1 + _SLIPPAGE)
    t1 = es + _RR_T1_MULT * atr
    sl = es - _RR_SL_MULT  * atr
    for idx, (_, bar) in enumerate(future.iterrows()):
        if float(bar['Low'])  <= sl: return {'outcome': 'SL',      'r': -1.0,         'bars': idx+1}
        if float(bar['High']) >= t1: return {'outcome': 'T1',      'r': _RR_T1_MULT,  'bars': idx+1}
    lc   = float(future['Close'].iloc[-1]) if len(future) > 0 else es
    r_mtm = (lc - es) / atr if atr > 0 else 0
    return {'outcome': 'EXPIRED', 'r': round(r_mtm, 3), 'bars': len(future)}


def _stats(outcomes):
    if not outcomes:
        return {'n': 0, 'wr': 0, 'ev': 0, 'avg_r': 0}
    n    = len(outcomes)
    wins = sum(1 for o in outcomes if o['outcome'] == 'T1')
    ev   = (wins/n) * _RR_T1_MULT - (1 - wins/n) * _RR_SL_MULT
    return {'n': n, 'wr': wins/n, 'ev': ev, 'avg_r': sum(o['r'] for o in outcomes)/n}


def main():
    storage = PostgresStorage()
    be_wr   = _RR_SL_MULT / (_RR_T1_MULT + _RR_SL_MULT)

    print('=' * 72)
    print('AMV-LSTM-UPTREND D1  —  5-YEAR DIAGNOSTIC')
    print(f'Period: {START.date()} → {END.date()}')
    print(f'Brain gates: RSI [{_RSI_RECOVERY_MIN},{_RSI_PULLBACK_MAX}] | '
          f'close_pct [{_MIN_CLOSE_ABOVE_LOW_PCT},{_MAX_CLOSE_ABOVE_LOW_PCT}] | '
          f'slope >={_MIN_SMA20_SLOPE_PCT}%')
    print(f'Break-even WR: {be_wr:.1%}')
    print('=' * 72)

    # Per-symbol gate kill counters
    sym_gate_kills   = {}   # sym → Counter
    sym_tu_bars      = {}   # sym → int
    sym_bars_loaded  = {}   # sym → int

    # close_pct forward outcomes (all symbols, gates EXCEPT close_pct lower bound)
    cpct_outcomes    = defaultdict(list)

    for sym in SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, START, END, storage)
        if df.empty:
            print(f'  {sym}: NO DATA')
            continue
        sym_bars_loaded[sym] = len(df)
        sym_gate_kills[sym]  = Counter()
        sym_tu_bars[sym]     = 0

        n = len(df)
        for i in range(MIN_START, n - FORWARD_BARS):
            hist        = df.iloc[:i]
            future_bars = df.iloc[i: i + FORWARD_BARS]

            try:
                reg    = regime_ensemble_signal(hist)
                regime = reg.measurements.get('computed_regime', 'RANGING')
            except Exception:
                continue
            if regime != 'TRENDING_UP':
                continue

            sym_tu_bars[sym] += 1

            # Run gates with close_pct_min=0.40 to capture the zone we want to test
            g = _manual_gates(hist, close_pct_min=0.40)
            sym_gate_kills[sym][g['first_fail'] or 'ALL_PASS'] += 1

            # For close_pct forward outcome: bars that pass ALL gates except close_pct lower bound
            # i.e. pass with min=0.40 → now classify by actual close_pct zone
            if g['pass']:
                price = float(hist['Close'].iloc[-1])
                atr   = g['atr']
                ev_r  = _forward_outcome(price, atr, future_bars)
                cp    = g['close_pct']
                for label, lo, hi in CPCT_ZONES:
                    if lo <= cp < hi:
                        cpct_outcomes[label].append(ev_r)
                        break

    # ── SECTION 1: Symbol-level regime and gate coverage ─────────────────────
    print()
    print('─' * 72)
    print('SECTION 1 — Per-Symbol Coverage (5 years)')
    print('  Focus: why TATASTEEL.NS and RELIANCE.NS produced zero signals')
    print('─' * 72)
    print(f'  {"Symbol":<16} {"D1 bars":>8} {"TU bars":>8} {"TU%":>7} '
          f'{"ALL_PASS":>9} {"Top killer"}')
    print()
    for sym in SYMBOLS:
        if sym not in sym_bars_loaded:
            print(f'  {sym:<16}  NO DATA')
            continue
        n_bars  = sym_bars_loaded[sym]
        tu      = sym_tu_bars.get(sym, 0)
        tu_pct  = tu / n_bars * 100 if n_bars > 0 else 0
        kills   = sym_gate_kills.get(sym, Counter())
        ap      = kills.get('ALL_PASS', 0)
        # top killer = most frequent non-ALL_PASS gate
        top_k   = max(
            ((k, v) for k, v in kills.items() if k != 'ALL_PASS'),
            key=lambda x: x[1], default=('NONE', 0)
        )
        zero_flag = ' ← ZERO SIGNALS' if sym in ZERO_SYM else ''
        print(f'  {sym:<16} {n_bars:>8} {tu:>8} {tu_pct:>6.1f}%  '
              f'{ap:>8}  {top_k[0]} ({top_k[1]}){zero_flag}')

    # ── SECTION 2: Detailed gate kill breakdown for zero-signal symbols ───────
    print()
    print('─' * 72)
    print('SECTION 2 — Gate Kill Detail for TATASTEEL.NS and RELIANCE.NS')
    print('  Shows exactly what kills TRENDING_UP bars in these two symbols')
    print('─' * 72)
    for sym in ['TATASTEEL.NS', 'RELIANCE.NS']:
        if sym not in sym_gate_kills:
            print(f'  {sym}: no data')
            continue
        kills = sym_gate_kills[sym]
        tu    = sym_tu_bars.get(sym, 0)
        print(f'\n  {sym}  (TRENDING_UP bars: {tu})')
        for gate, cnt in sorted(kills.items(), key=lambda x: -x[1]):
            pct = cnt / tu * 100 if tu > 0 else 0
            bar = '█' * int(cnt / max(kills.values()) * 25)
            marker = '  ← SIGNALS' if gate == 'ALL_PASS' else ''
            print(f'    {gate:<30} {cnt:>5}  {pct:>5.1f}%  {bar}{marker}')

    # ── SECTION 3: close_pct forward outcomes on 5-year data ─────────────────
    print()
    print('─' * 72)
    print('SECTION 3 — close_pct Forward Outcomes (5-year data)')
    print('  All gates pass EXCEPT close_pct lower bound (tested at 0.40 floor)')
    print('  Forward: 30 D1 bars, T1=2.5×ATR, SL=0.65×ATR')
    print(f'  Break-even WR: {be_wr:.1%}')
    print('─' * 72)
    print(f'  {"Zone":<45} {"n":>4}  {"WR":>7}  {"EV":>8}  Verdict')
    print()

    cpct_verdicts = {}
    for label, lo, hi in CPCT_ZONES:
        outcomes = cpct_outcomes.get(label, [])
        s        = _stats(outcomes)
        if s['n'] == 0:
            print(f'  {label:<45}  n=0 — no data')
            cpct_verdicts[label] = 'NO_DATA'
            continue
        verdict = ('✅ EDGE'    if s['ev'] > 0 and s['wr'] >= be_wr
                   else '⚠ MARGINAL' if s['ev'] > -0.1
                   else '❌ NO EDGE')
        cpct_verdicts[label] = verdict
        print(f'  {label:<45}  n={s["n"]:3d}  WR={s["wr"]:.1%}  '
              f'EV={s["ev"]:+.3f}R  {verdict}')

    # ── SECTION 4: Direct answers ─────────────────────────────────────────────
    print()
    print('=' * 72)
    print('DIRECT ANSWERS')
    print('=' * 72)

    # Q1: Zero signal symbols
    print()
    print('  Q1: Why do TATASTEEL.NS and RELIANCE.NS produce zero signals?')
    for sym in ['TATASTEEL.NS', 'RELIANCE.NS']:
        if sym not in sym_gate_kills:
            print(f'  {sym}: no data available')
            continue
        kills = sym_gate_kills[sym]
        tu    = sym_tu_bars.get(sym, 0)
        ap    = kills.get('ALL_PASS', 0)
        tu_pct = tu / sym_bars_loaded.get(sym, 1) * 100
        top_k  = max(
            ((k, v) for k, v in kills.items() if k != 'ALL_PASS'),
            key=lambda x: x[1], default=('NONE', 0)
        )
        print(f'\n  {sym}:')
        print(f'    TRENDING_UP coverage : {tu_pct:.1f}% of D1 bars')
        if tu_pct < 20:
            print(f'    → LOW TRENDING_UP coverage. Symbol trends less than others.')
            print(f'    → Consider removing from basket — fundamentally range-bound.')
        elif ap == 0:
            print(f'    → TRENDING_UP coverage adequate ({tu_pct:.1f}%) but ALL_PASS = 0.')
            print(f'    → All signals killed by brain gates.')
            print(f'    → Top killer: {top_k[0]} ({top_k[1]} bars)')
            if top_k[0] == 'G4_CLOSE_MIN':
                print(f'    → close_pct gate is the bottleneck for this symbol.')
                print(f'    → If Section 3 shows 0.45-0.50 has edge → lowering gate helps.')
            elif top_k[0] == 'G2_PULLBACK':
                print(f'    → Pullback detection missing. Symbol may pull back shallowly.')
                print(f'    → Consider widening ATR distance to 1.25 for this symbol.')
            elif top_k[0] == 'G8_W1':
                print(f'    → W1 alignment gate blocking. Weekly trend falling.')
                print(f'    → Symbol may have had prolonged weekly downtrends (2022 bear).')

    # Q2: close_pct lower bound
    print()
    print('  Q2: Should we lower close_pct lower bound from 0.50 → 0.45?')
    zone_45_50 = next((l for l, lo, hi in CPCT_ZONES if lo == 0.45), None)
    if zone_45_50:
        s = _stats(cpct_outcomes.get(zone_45_50, []))
        if s['n'] < 5:
            print(f'  A: Insufficient data (n={s["n"]}). Cannot conclude.')
            print(f'     Consider adding more symbols before changing this gate.')
        elif s['ev'] > 0 and s['wr'] >= be_wr:
            print(f'  A: YES — close_pct 0.45-0.50 shows EV={s["ev"]:+.3f}R '
                  f'WR={s["wr"]:.1%} on n={s["n"]}.')
            print(f'     Lower gate from 0.50 → 0.45.')
        elif s['ev'] > -0.1:
            print(f'  A: MARGINAL — close_pct 0.45-0.50 shows EV={s["ev"]:+.3f}R '
                  f'on n={s["n"]}. Edge is weak.')
            print(f'     Do NOT lower the gate yet. Add more symbols first.')
            print(f'     More data needed before this decision is reliable.')
        else:
            print(f'  A: NO — close_pct 0.45-0.50 shows EV={s["ev"]:+.3f}R '
                  f'on n={s["n"]}. Negative.')
            print(f'     Keep gate at 0.50.')

    print()
    print('  NEXT STEPS:')
    print('  1. Read Section 2 to understand zero-signal symbols.')
    print('  2. Read Section 3 for close_pct verdict.')
    print('  3. Decide: remove zero-signal symbols from basket? Add new ones?')
    print('  4. Paste full output before changing anything.')
    print('=' * 72)


if __name__ == '__main__':
    main()