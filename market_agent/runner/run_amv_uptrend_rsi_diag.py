"""
run_amv_uptrend_rsi_diag.py
============================
Diagnostic: Does RSI 55-60 have edge on D1 near SMA20?

Context:
  H1 backtest showed RSI 55-65 = EV=-0.152R on n=64. We used that evidence
  to set the RSI ceiling at 55 on the D1 brain. But:
    - H1 SMA20 = 3-day average. Not a real support level.
    - D1 SMA20 = 1-month average. Respected by institutions.
  The H1 evidence may not transfer to D1. This diagnostic checks directly.

What this measures:
  For every D1 bar where:
    - regime = TRENDING_UP
    - price is near D1 SMA20 (within 1.5×ATR)
    - all other brain gates pass (slope, pullback, close_pct, volume, ATR)
    - RSI falls in one of these zones: [45-50], [50-55], [55-60], [60-65]

  We measure what happens to price over the NEXT 30 D1 bars:
    - Did price hit T1 (entry + 2.5×ATR) before SL (entry - 0.65×ATR)?
    - Win rate and EV per RSI zone
    - Average bars to T1 hit vs SL hit

This is the direct answer to: should we raise the RSI ceiling from 55 → 60?

If RSI 55-60 shows WR >= 30% and EV > 0 on D1 → raise the ceiling.
If RSI 55-60 shows WR < 25% or EV < -0.1R → keep ceiling at 55, look elsewhere.

Run:
  python -X utf8 run_amv_uptrend_rsi_diag.py
"""

import pandas as pd
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from market_agent.brain.regime_ensemble import regime_ensemble_signal
from market_agent.brain.brain_utils     import calc_atr, calc_rsi_float
from market_agent.runner.backtester     import load_ohlcv, MIN_HIST_BARS
from market_agent.data.storage.postgres import PostgresStorage

from market_agent.brain.amv_lstm_uptrend import (
    _MIN_SMA20_SLOPE_PCT,
    _ATR_EXPANSION_FACTOR,
    _MIN_BOUNCE_VOL_RATIO,
    _PULLBACK_ATR_DISTANCE,
    _PULLBACK_LOOKBACK_BARS,
    _MIN_PULLBACK_DROP_ATR,
    _RR_T1_MULT,
    _RR_SL_MULT,
)

logging.basicConfig(level=logging.WARNING)

SYMBOLS        = ['LT.NS', 'TATASTEEL.NS', 'AAPL', 'RELIANCE.NS', 'ITC.NS', 'AMD', 'GOOGL']
TIMEFRAME      = '1d'
END            = datetime.utcnow()
START          = END - timedelta(days=900)
MIN_START      = max(MIN_HIST_BARS, 60)
FORWARD_BARS   = 30    # how many D1 bars forward to check outcome
_SLIPPAGE      = 0.0005

# RSI zones to test — this is the core question
RSI_ZONES = [
    ('45-50  [current gate, deep]',   45, 50),
    ('50-55  [current gate, upper]',  50, 55),
    ('55-60  [proposed extension]',   55, 60),
    ('60-65  [wider extension test]', 60, 65),
]

# close_pct zones to test simultaneously (secondary question)
CLOSE_PCT_ZONES = [
    ('0.35-0.40  [wider]',   0.35, 0.40),
    ('0.40-0.50  [middle]',  0.40, 0.50),
    ('0.50-0.70  [current]', 0.50, 0.70),
    ('>= 0.70    [strong]',  0.70, 1.01),
]

# SMA slope zones to test (fix for Gate 1 — was killing 136 bars)
SLOPE_ZONES = [
    ('0.03-0.05%  [very loose]', 0.03, 0.05),
    ('0.05-0.10%  [proposed]',   0.05, 0.10),
    ('>= 0.10%    [current]',    0.10, 999.0),
]


def _passes_non_rsi_gates(hist: pd.DataFrame,
                           close_pct_min: float = 0.40,
                           slope_min: float = 0.05) -> tuple:
    """
    Check all gates EXCEPT RSI zone, using loosened thresholds.
    Returns (passes: bool, close_pct: float, vol_ratio: float,
             sma20_slope: float, pullback_method: str, atr_val: float, entry: float)
    """
    close    = hist['Close']
    high     = hist['High']
    low      = hist['Low']
    sma20    = close.rolling(20).mean()
    price    = float(close.iloc[-1])
    atr_val  = float(calc_atr(hist))

    if atr_val <= 0:
        return False, 0, 0, 0, 'NONE', 0, price

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

    # Pullback detection
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

    # Gates (using loosened thresholds for this diagnostic)
    if sma20_slope < slope_min:       return False, close_pct, vol_ratio, sma20_slope, pullback_method, atr_val, price
    if not pullback_touched:          return False, close_pct, vol_ratio, sma20_slope, pullback_method, atr_val, price
    if price <= sma20_curr:           return False, close_pct, vol_ratio, sma20_slope, pullback_method, atr_val, price
    if close_pct < close_pct_min:     return False, close_pct, vol_ratio, sma20_slope, pullback_method, atr_val, price
    if vol_ratio < _MIN_BOUNCE_VOL_RATIO: return False, close_pct, vol_ratio, sma20_slope, pullback_method, atr_val, price
    if not atr_expanding:             return False, close_pct, vol_ratio, sma20_slope, pullback_method, atr_val, price

    return True, close_pct, vol_ratio, sma20_slope, pullback_method, atr_val, price


def _evaluate_forward(entry: float, atr_val: float,
                       future_bars: pd.DataFrame) -> dict:
    """
    Check if T1 or SL was hit in forward bars.
    Returns outcome dict.
    """
    entry_s = entry * (1 + _SLIPPAGE)
    t1      = entry_s + _RR_T1_MULT * atr_val * (1 - _SLIPPAGE)
    sl      = entry_s - _RR_SL_MULT  * atr_val * (1 - _SLIPPAGE)
    r_per_unit = _RR_T1_MULT * atr_val

    for bar_idx, (_, bar) in enumerate(future_bars.iterrows()):
        bar_high = float(bar['High'])
        bar_low  = float(bar['Low'])
        if bar_low <= sl:
            return {'outcome': 'SL', 'r': -1.0, 'bars': bar_idx + 1}
        if bar_high >= t1:
            return {'outcome': 'T1', 'r': _RR_T1_MULT, 'bars': bar_idx + 1}

    # Expired — mark-to-market at last close
    if len(future_bars) > 0:
        last_close = float(future_bars['Close'].iloc[-1])
        r_mtm      = (last_close - entry_s) / (atr_val if atr_val > 0 else 1.0)
        return {'outcome': 'EXPIRED', 'r': round(r_mtm, 3), 'bars': len(future_bars)}
    return {'outcome': 'EXPIRED', 'r': 0.0, 'bars': 0}


def _stats(outcomes: list) -> dict:
    if not outcomes:
        return {'n': 0, 'wr': 0, 'ev': 0, 'avg_r': 0,
                'wins': 0, 'losses': 0, 'expired': 0}
    wins    = sum(1 for o in outcomes if o['outcome'] == 'T1')
    losses  = sum(1 for o in outcomes if o['outcome'] == 'SL')
    expired = sum(1 for o in outcomes if o['outcome'] == 'EXPIRED')
    n       = len(outcomes)
    avg_r   = sum(o['r'] for o in outcomes) / n
    wr      = wins / n
    ev      = wr * _RR_T1_MULT - (1 - wr) * _RR_SL_MULT
    return {'n': n, 'wr': wr, 'ev': ev, 'avg_r': avg_r,
            'wins': wins, 'losses': losses, 'expired': expired}


def main():
    storage = PostgresStorage()

    # Collect all qualifying bars with their RSI, close_pct, slope values
    # Using LOOSENED non-RSI gates: slope >= 0.05%, close_pct >= 0.40
    # This gives us the maximum pool to measure RSI zone effects cleanly

    # Structure: zone_label → list of outcome dicts
    rsi_outcomes      = defaultdict(list)
    close_pct_outcomes = defaultdict(list)
    slope_outcomes    = defaultdict(list)

    # Crossed zone analysis: RSI zone × close_pct zone (2D)
    crossed_outcomes  = defaultdict(list)

    total_qualifying  = 0

    print('=' * 72)
    print('AMV-LSTM-UPTREND D1  —  RSI ZONE FORWARD-OUTCOME DIAGNOSTIC')
    print(f'Period : {START.date()} → {END.date()}')
    print(f'R:R    : T1={_RR_T1_MULT}×ATR  SL={_RR_SL_MULT}×ATR  '
          f'Forward={FORWARD_BARS} D1 bars')
    print(f'Gates  : ALL non-RSI gates applied at loosened thresholds')
    print(f'         slope >= 0.05%  |  close_pct >= 0.40  |  vol >= {_MIN_BOUNCE_VOL_RATIO}×')
    print(f'         pullback confirmed  |  above SMA20  |  ATR expanding')
    print('=' * 72)

    for sym in SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, START, END, storage)
        if df.empty:
            print(f'  {sym}: no data')
            continue
        print(f'  {sym}: {len(df)} bars')

        n = len(df)
        for i in range(MIN_START, n - FORWARD_BARS):
            hist        = df.iloc[:i]
            future_bars = df.iloc[i: i + FORWARD_BARS]

            # Regime gate
            try:
                reg    = regime_ensemble_signal(hist)
                regime = reg.measurements.get('computed_regime', 'RANGING')
            except Exception:
                continue
            if regime != 'TRENDING_UP':
                continue

            # Non-RSI gates with loosened thresholds
            passes, close_pct, vol_ratio, sma20_slope, pm, atr_val, entry = \
                _passes_non_rsi_gates(hist, close_pct_min=0.40, slope_min=0.05)
            if not passes:
                continue

            rsi = calc_rsi_float(hist)
            if rsi > 70:   # still block hard overbought
                continue
            if rsi < 40:   # still block deep trend-break territory
                continue

            total_qualifying += 1
            ev_result = _evaluate_forward(entry, atr_val, future_bars)

            # RSI zone classification
            for label, lo, hi in RSI_ZONES:
                if lo <= rsi < hi:
                    rsi_outcomes[label].append(ev_result)
                    break

            # close_pct zone classification
            for label, lo, hi in CLOSE_PCT_ZONES:
                if lo <= close_pct < hi:
                    close_pct_outcomes[label].append(ev_result)
                    break

            # SMA slope zone
            for label, lo, hi in SLOPE_ZONES:
                if lo <= sma20_slope < hi:
                    slope_outcomes[label].append(ev_result)
                    break

            # Crossed: RSI zone + close_pct zone
            rsi_zone_lbl    = next((l for l, lo, hi in RSI_ZONES    if lo <= rsi      < hi), 'other')
            cpct_zone_lbl   = next((l for l, lo, hi in CLOSE_PCT_ZONES if lo <= close_pct < hi), 'other')
            crossed_outcomes[f'RSI:{rsi_zone_lbl[:5]} × CP:{cpct_zone_lbl[:8]}'].append(ev_result)

    print(f'\n  Total qualifying bars (all non-RSI gates pass, RSI 40-70): {total_qualifying}')

    if total_qualifying == 0:
        print('\n  No qualifying bars found.')
        print('  Check: is regime_ensemble_signal returning TRENDING_UP for any bars?')
        print('  Check: is load_ohlcv returning data correctly?')
        return

    be_wr = _RR_SL_MULT / (_RR_T1_MULT + _RR_SL_MULT)
    print(f'  Break-even WR at T1={_RR_T1_MULT}× SL={_RR_SL_MULT}×: {be_wr:.1%}')

    # ── SECTION 1: RSI Zone Results ───────────────────────────────────────────
    print()
    print('─' * 72)
    print('SECTION 1 — RSI Zone Forward Outcomes  (THE CORE QUESTION)')
    print('  Does RSI 55-60 have edge on D1 near SMA20?')
    print('  All non-RSI brain gates are applied. Only RSI zone varies.')
    print('─' * 72)
    print(f'  {"RSI Zone":<40} {"n":>4}  {"WR":>7}  {"EV":>8}  {"avg_R":>7}  Verdict')
    print()

    rsi_zone_verdicts = {}
    for label, lo, hi in RSI_ZONES:
        outcomes = rsi_outcomes.get(label, [])
        s        = _stats(outcomes)
        if s['n'] == 0:
            print(f'  {label:<40} n=0  — no data')
            rsi_zone_verdicts[label] = 'NO_DATA'
            continue
        verdict = ('✅ EDGE' if s['ev'] > 0 and s['wr'] >= 0.28
                   else '⚠ MARGINAL' if s['ev'] > -0.1
                   else '❌ NO EDGE')
        rsi_zone_verdicts[label] = verdict
        print(f'  {label:<40} n={s["n"]:3d}  WR={s["wr"]:.1%}  '
              f'EV={s["ev"]:+.3f}R  avg_R={s["avg_r"]:+.3f}  {verdict}')

    print()
    print('  Interpretation:')
    print(f'  Current gate ceiling = 55. Gate was based on H1 evidence (RSI 55-65 = EV=-0.152R).')
    print(f'  H1 SMA20 = 3-day MA (not real support). D1 SMA20 = 1-month MA (real support).')
    print(f'  If RSI 55-60 shows EV > 0 above → the H1 evidence does NOT transfer to D1.')
    print(f'  If RSI 55-60 shows EV < 0 above → keep ceiling at 55.')

    # ── SECTION 2: close_pct Zone Results ────────────────────────────────────
    print()
    print('─' * 72)
    print('SECTION 2 — close_pct Zone Forward Outcomes')
    print('  Current gate = 0.50. Question: does 0.40-0.50 have edge?')
    print('  (close_pct = where closing price sits in the day\'s candle range)')
    print('─' * 72)
    print(f'  {"close_pct Zone":<40} {"n":>4}  {"WR":>7}  {"EV":>8}  {"avg_R":>7}  Verdict')
    print()

    for label, lo, hi in CLOSE_PCT_ZONES:
        outcomes = close_pct_outcomes.get(label, [])
        s        = _stats(outcomes)
        if s['n'] == 0:
            print(f'  {label:<40} n=0  — no data')
            continue
        verdict = ('✅ EDGE' if s['ev'] > 0 and s['wr'] >= 0.28
                   else '⚠ MARGINAL' if s['ev'] > -0.1
                   else '❌ NO EDGE')
        print(f'  {label:<40} n={s["n"]:3d}  WR={s["wr"]:.1%}  '
              f'EV={s["ev"]:+.3f}R  avg_R={s["avg_r"]:+.3f}  {verdict}')

    print()
    print('  Interpretation:')
    print('  If 0.40-0.50 shows EV > 0 → lower gate to 0.40.')
    print('  If 0.40-0.50 shows EV < 0 → keep gate at 0.50. G4 was right.')

    # ── SECTION 3: SMA Slope Zone Results ────────────────────────────────────
    print()
    print('─' * 72)
    print('SECTION 3 — SMA20 Slope Zone Forward Outcomes')
    print('  Current gate = 0.10%/5d. Question: does 0.05-0.10% have edge?')
    print('─' * 72)
    print(f'  {"Slope Zone":<40} {"n":>4}  {"WR":>7}  {"EV":>8}  {"avg_R":>7}  Verdict')
    print()

    for label, lo, hi in SLOPE_ZONES:
        outcomes = slope_outcomes.get(label, [])
        s        = _stats(outcomes)
        if s['n'] == 0:
            print(f'  {label:<40} n=0  — no data')
            continue
        verdict = ('✅ EDGE' if s['ev'] > 0 and s['wr'] >= 0.28
                   else '⚠ MARGINAL' if s['ev'] > -0.1
                   else '❌ NO EDGE')
        print(f'  {label:<40} n={s["n"]:3d}  WR={s["wr"]:.1%}  '
              f'EV={s["ev"]:+.3f}R  avg_R={s["avg_r"]:+.3f}  {verdict}')

    # ── SECTION 4: Summary and direct answers ─────────────────────────────────
    print()
    print('=' * 72)
    print('DIRECT ANSWERS')
    print('=' * 72)

    # RSI answer
    rsi_55_60_label = next((l for l, lo, hi in RSI_ZONES if lo == 55), None)
    if rsi_55_60_label:
        verdict = rsi_zone_verdicts.get(rsi_55_60_label, 'NO_DATA')
        s       = _stats(rsi_outcomes.get(rsi_55_60_label, []))
        print(f'\n  Q1: Should we raise RSI ceiling from 55 → 60?')
        if verdict == 'NO_DATA' or s['n'] < 5:
            print(f'  A: Insufficient data (n={s["n"]}). Cannot conclude. Need n >= 5.')
        elif s['ev'] > 0 and s['wr'] >= 0.28:
            print(f'  A: YES — RSI 55-60 shows EV={s["ev"]:+.3f}R, WR={s["wr"]:.1%} on n={s["n"]}.')
            print(f'     H1 evidence does NOT transfer to D1. Raise ceiling to 60.')
            print(f'     Estimated additional signals: ~{s["n"]} per 2.5 years across 7 symbols.')
        elif s['ev'] > -0.1:
            print(f'  A: MARGINAL — RSI 55-60 shows EV={s["ev"]:+.3f}R on n={s["n"]}.')
            print(f'     Edge is small. Raising ceiling adds volume but not strong edge.')
            print(f'     Recommended: raise to 58 (not 60) and monitor.')
        else:
            print(f'  A: NO — RSI 55-60 shows EV={s["ev"]:+.3f}R on n={s["n"]}.')
            print(f'     H1 evidence DOES transfer to D1. Keep ceiling at 55.')

    # close_pct answer
    cp_40_50_label = next((l for l, lo, hi in CLOSE_PCT_ZONES if lo == 0.40), None)
    if cp_40_50_label:
        s = _stats(close_pct_outcomes.get(cp_40_50_label, []))
        print(f'\n  Q2: Should we lower close_pct gate from 0.50 → 0.40?')
        if s['n'] < 5:
            print(f'  A: Insufficient data (n={s["n"]}). Cannot conclude.')
        elif s['ev'] > 0 and s['wr'] >= 0.28:
            print(f'  A: YES — close_pct 0.40-0.50 shows EV={s["ev"]:+.3f}R on n={s["n"]}.')
            print(f'     Lower gate to 0.40.')
        else:
            print(f'  A: NO — close_pct 0.40-0.50 shows EV={s["ev"]:+.3f}R on n={s["n"]}.')
            print(f'     Keep gate at 0.50. Weak close days do not have edge.')

    # Slope answer
    slope_05_10_label = next((l for l, lo, hi in SLOPE_ZONES if lo == 0.05), None)
    if slope_05_10_label:
        s = _stats(slope_outcomes.get(slope_05_10_label, []))
        print(f'\n  Q3: Should we lower SMA slope gate from 0.10% → 0.05%?')
        if s['n'] < 5:
            print(f'  A: Insufficient data (n={s["n"]}). Cannot conclude.')
        elif s['ev'] > 0:
            print(f'  A: YES — slope 0.05-0.10% shows EV={s["ev"]:+.3f}R on n={s["n"]}.')
            print(f'     Lower gate to 0.05%.')
        else:
            print(f'  A: NO — slope 0.05-0.10% shows EV={s["ev"]:+.3f}R on n={s["n"]}.')
            print(f'     Keep slope gate at 0.10%.')

    print()
    print('  NEXT STEP:')
    print('  Paste this output. We will update brain constants and run confirm.')
    print('  Do NOT change any constants before reviewing these results.')
    print('=' * 72)


if __name__ == '__main__':
    main()