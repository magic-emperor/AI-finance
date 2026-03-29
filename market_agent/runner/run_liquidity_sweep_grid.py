"""
market_agent/runner/run_liquidity_sweep_grid.py
================================================
Liquidity-Sweep Brain v5 — H1 Equity Parameter Grid Backtest (v6 runner)
================================================

V6 RUNNER CHANGES (from 270d analysis):

  CHANGE 1: NVDA added to symbol basket
            DB confirmed 1386 bars available.
            High ATR, US tech sector, different correlation to existing symbols.
            Expected +2-4 signals per combo at vol=1.5 settings.

  CHANGE 2: dist=0.80 replaced with dist=0.60
            270d confirmed: dist=0.80 + vol>=1.5 = WR=0-17%, all losses.
            Reason: far levels + volume spike = breakout, not reversal.
            dist=0.60 explores the safer space between 0.50 and the dead zone.

V5 CHANGES FROM V4 (evidence-backed from v4b gate attrition diagnostic):

  FIX 1: Touch hard gate REMOVED (v4 over-fit — blocked 84% of valid signals)
          v4b: after regime gate 68 signals, touch gate blocked 57 (84%).
          Gate built on n=6 trades — statistically meaningless.
          V5: touch count is a confidence PENALTY only (not a hard gate).
          270d confirms: 3+ touches WR=75% — hard gate would have lost winners.

  FIX 2: SELL_TRENDING_DOWN re-allowed (23 signals blocked with no evidence)
          BUY_TRENDING_DOWN remains blocked (n=4, WR=0%, confirmed to lose).

RETAINED FROM V4:
  - BUY sweeps: RANGING + VOLATILE only
  - Volume ceiling: vol >= 3.0x blocked
  - Volume floor: configurable

GRID (4x3x3x3 = 108 combos):
  vol_spike_mult [0.0, 1.2, 1.5, 2.0], min_level_dist_atr [0.30, 0.40, 0.50],
  pivot_bars [2, 3, 5], min_level_age_bars [5, 10, 15]

SYMBOLS (8 large-cap equities):
  LT.NS, TATASTEEL.NS, AAPL, RELIANCE.NS, ITC.NS, AMD, GOOGL, NVDA

ACCEPTANCE: WR>=55%  EV>0.40R  signals>=20  MaxDD<8R
"""
from __future__ import annotations

import argparse
import itertools
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from market_agent.brain.liquidity_sweep import (
    _VOL_SPIKE_MULT, _VOL_CEILING_MULT,
    _MIN_SWEEP_DEPTH_ATR, _MAX_SWEEP_DEPTH_ATR, _MIN_SWEEP_DEPTH_PCT,
    _MIN_LEVEL_DIST_ATR, _MIN_LEVEL_AGE_BARS, _SWING_LOOKBACK, _PIVOT_BARS,
    _MAX_LEVELS_TO_CHECK, _SWEEP_CANDLE_LOOKBACK, _SL_BUFFER_ATR,
    _RSI_BLOCK_LOW, _RSI_BLOCK_HIGH,
    _RSI_BUY_SWEET_LOW, _RSI_BUY_SWEET_HIGH,
    _RSI_SELL_SWEET_LOW, _RSI_SELL_SWEET_HIGH,
    _MAX_TOUCHES_CLEAN, _LEVEL_TOLERANCE_PCT,
    _TOUCH_LOOKBACK, _TOUCH_EXCLUDE_RECENT, _AGE_CONFIDENCE_DECAY,
    _MIN_CONFIDENCE_GATE, _MIN_HIST_BARS, _VOL_LOOKBACK,
    # close_pct used as confidence boost only — no hard gate constants needed
    _calc_trade_levels, _count_level_touches,
)
from market_agent.brain.regime_ensemble import regime_ensemble_signal
from market_agent.brain.brain_contract  import BrainSignal
from market_agent.brain.brain_utils     import compute_delta_flow
from market_agent.runner.backtester     import (
    load_ohlcv, aggregate_results,
    _evaluate_signal, _build_trade_row,
    MIN_HIST_BARS, MAX_BARS_IN_TRADE,
)
from market_agent.data.storage.postgres import PostgresStorage

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
log = logging.getLogger('run_liquidity_sweep_grid')


# ── Settings ──────────────────────────────────────────────────────────────────

SYMBOLS = [
    'LT.NS',
    'TATASTEEL.NS',
    'AAPL',
    'RELIANCE.NS',
    'ITC.NS',
    'AMD',
    'GOOGL',
    # NVDA added v6: 1386 bars confirmed in DB, high ATR, different sector to existing basket
    'NVDA',
]

TIMEFRAME = '1h'

# V5: Regime gates are directional — not a single allowed set
# BUY  sweeps: RANGING + VOLATILE only
#   TRENDING_DOWN BUY: 180d n=4, WR=0%, avg_R=-1.000. Confirmed breakdown. BLOCKED.
# SELL sweeps: RANGING + VOLATILE + TRENDING_UP + TRENDING_DOWN
#   SELL_TRENDING_DOWN: bearish distribution stop-hunt. No evidence it loses. Re-allowed V5.
#   SELL_TRENDING_UP remains blocked (wrong direction for a reversal).
# CHAOS always blocked at pre-computation stage.
SWEEP_ALLOWED_REGIMES    = {'VOLATILE', 'TRENDING_UP', 'TRENDING_DOWN', 'RANGING'}  # for regime pre-compute
BUY_ALLOWED_REGIMES      = {'RANGING', 'VOLATILE'}
SELL_ALLOWED_REGIMES     = {'RANGING', 'VOLATILE', 'TRENDING_UP', 'TRENDING_DOWN'}

# [NF-1-LIQSWEEP] Mirror Liquidity-Sweep's post-detection wick-quality gate.
# 730d baseline: wick_depth_atr < 0.3 had WR=0% (0W/6L). Blocking these reduces
# low-quality "micro-sweeps" that do not meaningfully run liquidity.
_MIN_WICK_SIZE_ATR = 0.30

PARAM_GRID = {
    # vol=1.2 added: explores between "no filter" (0.0, WR=33%) and "quality filter" (1.5, WR=58%)
    # vol=0.0 kept: reference baseline and catch any signal the vol filter misses
    # vol=2.0 kept: ultra-high-quality reference even if n stays low
    'vol_spike_mult':      [0.0, 1.2, 1.5, 2.0],
    # dist=0.40 added: unexplored gap between 0.30 and 0.50
    # dist=0.60 removed: confirmed dead zone at vol>=1.5 (WR=16-27%)
    'min_level_dist_atr':  [0.30, 0.40, 0.50],
    'pivot_bars':          [2, 3, 5],
    'min_level_age_bars':  [5, 10, 15],
}

BASELINE_PARAMS = dict(
    vol_spike_mult     = _VOL_SPIKE_MULT,
    min_level_dist_atr = _MIN_LEVEL_DIST_ATR,
    pivot_bars         = _PIVOT_BARS,
    min_level_age_bars = _MIN_LEVEL_AGE_BARS,
)


# ═══════════════════════════════════════════════════════════════
# VECTORIZED PRE-COMPUTATION
# ═══════════════════════════════════════════════════════════════

def _precompute_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    high  = df['High'].values.astype(float)
    low   = df['Low'].values.astype(float)
    close = df['Close'].values.astype(float)
    n     = len(df)
    tr    = np.zeros(n)
    atr   = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(high[i] - low[i],
                    abs(high[i] - close[i-1]),
                    abs(low[i]  - close[i-1]))
    if n > period:
        atr[period] = np.mean(tr[1:period+1])
        alpha = 1.0 / period
        for i in range(period+1, n):
            atr[i] = atr[i-1] * (1-alpha) + tr[i] * alpha
    return atr


def _precompute_rsi(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    close = df['Close'].values.astype(float)
    n     = len(df)
    rsi   = np.full(n, 50.0)
    if n < period + 2:
        return rsi
    deltas   = np.diff(close)
    gains    = np.where(deltas > 0, deltas, 0.0)
    losses   = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    for i in range(period, n-1):
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else 1e9
        rsi[i+1] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _precompute_vol_ratio(df: pd.DataFrame, lookback: int = 20) -> np.ndarray:
    vol = df['Volume'].values.astype(float)
    n   = len(df)
    vr  = np.zeros(n)
    for i in range(lookback, n):
        base  = vol[i-lookback:i]
        mean_ = np.mean(base[base > 0]) if np.any(base > 0) else 0.0
        vr[i] = vol[i] / mean_ if mean_ > 0 else 0.0
    return vr


def _precompute_regimes(df: pd.DataFrame, warmup: int) -> list:
    n       = len(df)
    regimes = [None] * n
    for i in range(warmup, n - MAX_BARS_IN_TRADE):
        try:
            sig        = regime_ensemble_signal(df.iloc[:i])
            regimes[i] = sig.measurements.get('computed_regime', 'RANGING')
        except Exception:
            regimes[i] = None
    return regimes


def _precompute_swings(
    df: pd.DataFrame,
    warmup: int,
    pivot_bars: int,
    lookback: int = _SWING_LOOKBACK,
) -> list:
    """
    Pre-compute swing levels once per (symbol, pivot_bars).
    result[i] = {'highs': [(price, age),...], 'lows': [(price, age),...]}
    """
    n         = len(df)
    result    = [None] * n
    high_vals = df['High'].values.astype(float)
    low_vals  = df['Low'].values.astype(float)

    for i in range(warmup, n - MAX_BARS_IN_TRADE):
        start = max(0, i - lookback)
        seg_h = high_vals[start:i]
        seg_l = low_vals[start:i]
        seg_n = len(seg_h)
        highs: list = []
        lows:  list = []

        for j in range(pivot_bars, seg_n - pivot_bars):
            ch = seg_h[j]
            cl = seg_l[j]
            if (ch >= np.max(seg_h[j-pivot_bars:j]) and
                    ch >= np.max(seg_h[j+1:j+pivot_bars+1])):
                highs.append((ch, (i-1) - (start+j)))
            if (cl <= np.min(seg_l[j-pivot_bars:j]) and
                    cl <= np.min(seg_l[j+1:j+pivot_bars+1])):
                lows.append((cl, (i-1) - (start+j)))

        seen_h: dict = {}
        for price, age in highs:
            k = round(price, 4)
            if k not in seen_h or age < seen_h[k][1]:
                seen_h[k] = (price, age)

        seen_l: dict = {}
        for price, age in lows:
            k = round(price, 4)
            if k not in seen_l or age < seen_l[k][1]:
                seen_l[k] = (price, age)

        result[i] = {
            'highs': sorted(seen_h.values(), key=lambda x: x[1]),
            'lows':  sorted(seen_l.values(), key=lambda x: x[1]),
        }

    return result


def precompute_all(df: pd.DataFrame, warmup: int, pivot_bars_list: list) -> dict:
    """Pre-compute all indicators once per symbol."""
    log.info("    ATR + RSI + vol ratio (vectorized)...")
    atr_arr = _precompute_atr(df)
    rsi_arr = _precompute_rsi(df)
    vol_arr = _precompute_vol_ratio(df, _VOL_LOOKBACK)

    log.info(f"    Regime series ({len(df)} bars)...")
    regime_lst = _precompute_regimes(df, warmup)

    swing_cache: dict = {}
    for piv in pivot_bars_list:
        log.info(f"    Swing levels (pivot_bars={piv})...")
        swing_cache[piv] = _precompute_swings(df, warmup, piv, _SWING_LOOKBACK)

    rc: dict = {}
    for r in regime_lst:
        if r:
            rc[r] = rc.get(r, 0) + 1
    log.info(f"    Regimes: {rc}")

    atr_ok = [v for v in atr_arr if v > 0]
    if atr_ok:
        log.info(f"    ATR range: {min(atr_ok):.4f} - {max(atr_ok):.4f}")

    return {
        'atr':       atr_arr,
        'rsi':       rsi_arr,
        'vol_ratio': vol_arr,
        'regime':    regime_lst,
        'swings':    swing_cache,
    }


# ═══════════════════════════════════════════════════════════════
# SWEEP DETECTORS (use pre-computed swings, O(k) per call)
# ═══════════════════════════════════════════════════════════════

def _detect_bull_sweep(
    df: pd.DataFrame, bar_idx: int, swing_lows: list,
    atr: float, price: float,
    min_level_dist_atr: float, min_level_age_bars: int,
) -> dict | None:
    if not swing_lows:
        return None
    min_depth = max(atr * _MIN_SWEEP_DEPTH_ATR, price * _MIN_SWEEP_DEPTH_PCT)
    max_depth = atr * _MAX_SWEEP_DEPTH_ATR
    min_dist  = atr * min_level_dist_atr

    for age in range(min(_SWEEP_CANDLE_LOOKBACK, bar_idx)):
        ci = bar_idx - 1 - age
        if ci < 0:
            break
        ch = float(df['High'].iloc[ci])
        cl = float(df['Low'].iloc[ci])
        cc = float(df['Close'].iloc[ci])
        cr = ch - cl

        for lp, la in swing_lows[:_MAX_LEVELS_TO_CHECK]:
            if la < min_level_age_bars:
                continue
            if (price - lp) < min_dist:
                continue
            below = lp - cl
            if below < min_depth or below > max_depth:
                continue
            if cc <= lp:
                continue
            cpct = (cc - cl) / cr if cr > 0 else 0.5
            # close_pct filtering NOT done here — v4 gate is in the inner loop
            return {
                'type': 'BULLISH_SWEEP', 'swept_level': lp, 'level_age': la,
                'wick_depth_atr': round(below / atr, 3) if atr > 0 else 0,
                'close_pct': round(cpct, 3), 'entry': price,
                'candles_since_sweep': age,
            }
    return None


def _detect_bear_sweep(
    df: pd.DataFrame, bar_idx: int, swing_highs: list,
    atr: float, price: float,
    min_level_dist_atr: float, min_level_age_bars: int,
) -> dict | None:
    if not swing_highs:
        return None
    min_depth = max(atr * _MIN_SWEEP_DEPTH_ATR, price * _MIN_SWEEP_DEPTH_PCT)
    max_depth = atr * _MAX_SWEEP_DEPTH_ATR
    min_dist  = atr * min_level_dist_atr

    for age in range(min(_SWEEP_CANDLE_LOOKBACK, bar_idx)):
        ci = bar_idx - 1 - age
        if ci < 0:
            break
        ch = float(df['High'].iloc[ci])
        cl = float(df['Low'].iloc[ci])
        cc = float(df['Close'].iloc[ci])
        cr = ch - cl

        for lp, la in swing_highs[:_MAX_LEVELS_TO_CHECK]:
            if la < min_level_age_bars:
                continue
            if (lp - price) < min_dist:
                continue
            above = ch - lp
            if above < min_depth or above > max_depth:
                continue
            if cc >= lp:
                continue
            cpct = (cc - cl) / cr if cr > 0 else 0.5
            # close_pct filtering NOT done here — v4 gate is in the inner loop
            return {
                'type': 'BEARISH_SWEEP', 'swept_level': lp, 'level_age': la,
                'wick_height_atr': round(above / atr, 3) if atr > 0 else 0,
                'close_pct': round(cpct, 3), 'entry': price,
                'candles_since_sweep': age,
            }
    return None


# ═══════════════════════════════════════════════════════════════
# PER-COMBO BACKTEST — O(n) INNER LOOP
# ═══════════════════════════════════════════════════════════════

def run_combo(params: dict, data_cache: dict, cache_map: dict) -> pd.DataFrame:
    vol_spike_mult     = params['vol_spike_mult']
    min_level_dist_atr = params['min_level_dist_atr']
    pivot_bars         = params['pivot_bars']
    min_level_age_bars = params['min_level_age_bars']

    method_str = (f'Sweeps-v5(vol={vol_spike_mult} dist={min_level_dist_atr} '
                  f'piv={pivot_bars} age={min_level_age_bars})')
    rows   = []
    warmup = max(MIN_HIST_BARS, _MIN_HIST_BARS)

    for sym, df in data_cache.items():
        n     = len(df)
        cache = cache_map.get(sym, {})

        atr_arr    = cache.get('atr',       np.zeros(n))
        rsi_arr    = cache.get('rsi',       np.full(n, 50.0))
        vol_arr    = cache.get('vol_ratio', np.zeros(n))
        regime_lst = cache.get('regime',    [None] * n)
        swing_map  = cache.get('swings',    {})
        swing_lst  = swing_map.get(pivot_bars, [None] * n)
        close_vals = df['Close'].values.astype(float)

        for i in range(warmup, n - MAX_BARS_IN_TRADE):
            regime    = regime_lst[i]
            atr_val   = float(atr_arr[i])
            rsi_val   = float(rsi_arr[i])
            vol_ratio = float(vol_arr[i])
            swings    = swing_lst[i]

            # ── Fast gates — all O(1) ─────────────────────────────────────
            if regime is None or regime not in SWEEP_ALLOWED_REGIMES:
                continue
            if np.isnan(atr_val) or atr_val <= 0:
                continue
            if np.isnan(rsi_val):
                rsi_val = 50.0
            if swings is None:
                continue
            # RSI outer block (30/70)
            if rsi_val < _RSI_BLOCK_LOW or rsi_val > _RSI_BLOCK_HIGH:
                continue
            # V4: Volume ceiling gate — panic volume = all losses
            if vol_ratio >= _VOL_CEILING_MULT:
                continue
            # V4: Volume floor gate (disabled when vol_spike_mult=0)
            if vol_spike_mult > 0 and vol_ratio < vol_spike_mult:
                continue

            price = close_vals[i-1]

            # ── Sweep detection — O(k) ────────────────────────────────────
            bull = _detect_bull_sweep(
                df, i, swings['lows'], atr_val, price,
                min_level_dist_atr, min_level_age_bars)
            bear = _detect_bear_sweep(
                df, i, swings['highs'], atr_val, price,
                min_level_dist_atr, min_level_age_bars)

            if not bull and not bear:
                continue

            if bull and bear:
                if bull.get('wick_depth_atr', 0) >= bear.get('wick_height_atr', 0):
                    bear = None
                else:
                    bull = None

            sweep     = bull or bear
            direction = 'BUY' if bull else 'SELL'
            age       = sweep.get('candles_since_sweep', 0)

            # [NF-1-LIQSWEEP] Wick-size quality gate (keep in sync with brain).
            wick_key = 'wick_depth_atr' if direction == 'BUY' else 'wick_height_atr'
            wick_atr = float(sweep.get(wick_key, 0.0) or 0.0)
            if wick_atr < _MIN_WICK_SIZE_ATR:
                continue

            # ── V4: Directional regime gate ───────────────────────────────
            # TRENDING_DOWN BUY: 180d n=4, WR=0%, avg_R=-1.000 — hard block
            if direction == 'BUY'  and regime not in BUY_ALLOWED_REGIMES:
                continue
            if direction == 'SELL' and regime not in SELL_ALLOWED_REGIMES:
                continue

            # ── V5: Touch count — soft penalty only, no hard gate ─────────
            # V4 hard gate blocked 84% of valid signals (57/68) on n=6 evidence.
            # Hard gate requires min n=20 per bucket. Demoted to penalty in V5.
            hist    = df.iloc[:i]
            touches = _count_level_touches(hist, sweep['swept_level'])

            close_pct = sweep.get('close_pct', 0.5)
            delta   = compute_delta_flow(hist)

            target_1, stop_loss, actual_rr = _calc_trade_levels(
                direction=direction, entry=price,
                swept_level=sweep['swept_level'],
                swing_levels=swings, atr=atr_val, min_rr=2.0,
            )

            risk = abs(price - stop_loss)
            gain = abs(target_1 - price)
            if risk <= 0 or (gain / risk) < 1.5:
                continue

            # ── V5 confidence scoring ─────────────────────────────────────
            delta_ok = (delta is not None and (
                (direction == 'BUY'  and delta > 0.15) or
                (direction == 'SELL' and delta < -0.15)
            ))

            cs = 0.0
            conf_list: list = []
            contra:    list = []
            level_age = sweep.get('level_age', min_level_age_bars)

            # Volume quality — institutional sweet spot 1.5-2.5x
            if 0 < vol_ratio < _VOL_CEILING_MULT:
                if vol_ratio >= 1.5:
                    vol_score = 0.08 if vol_ratio < 2.5 else 0.04
                    cs += vol_score; conf_list.append(f'Vol={vol_ratio:.1f}x')

            # Strong close confirms reversal (direction-specific geometry)
            if direction == 'BUY':
                if close_pct >= 0.80:
                    cs += 0.08; conf_list.append(f'Strong({close_pct:.0%})')
                elif close_pct >= 0.65:
                    cs += 0.04; conf_list.append(f'Moderate({close_pct:.0%})')
                else:
                    contra.append(f'Weak close({close_pct:.0%})')
            else:  # SELL
                if close_pct <= 0.20:
                    cs += 0.08; conf_list.append(f'Strong({close_pct:.0%})')
                elif close_pct <= 0.35:
                    cs += 0.04; conf_list.append(f'Moderate({close_pct:.0%})')
                else:
                    contra.append(f'Weak close({close_pct:.0%})')

            # Touch count: informational only — no cs impact above 2 touches
            # Evidence base for penalty was n=6 — too small. Collect data first.
            if touches == 0:
                cs += 0.06; conf_list.append(f'Virgin(0t)')
            elif touches == 1:
                cs += 0.04; conf_list.append(f'Clean(1t)')
            elif touches == 2:
                cs += 0.01; conf_list.append(f'Tested(2t)')
            else:
                # 3+ touches: logged for breakdown analysis, NOT penalised
                contra.append(f'Overused({touches}t)')

            if delta_ok and delta is not None:
                cs += 0.05; conf_list.append(f'Delta={delta:+.2f}')
            elif delta is not None:
                contra.append(f'Delta neutral')

            # V4: RSI directional sweet spot (55-70 BUY, 30-45 SELL)
            if direction == 'BUY':
                if _RSI_BUY_SWEET_LOW <= rsi_val <= _RSI_BUY_SWEET_HIGH:
                    cs += 0.05; conf_list.append(f'RSI={rsi_val:.1f}(BUY zone)')
                else:
                    contra.append(f'RSI={rsi_val:.1f} outside BUY zone')
            else:
                if _RSI_SELL_SWEET_LOW <= rsi_val <= _RSI_SELL_SWEET_HIGH:
                    cs += 0.05; conf_list.append(f'RSI={rsi_val:.1f}(SELL zone)')
                else:
                    contra.append(f'RSI={rsi_val:.1f} outside SELL zone')

            if level_age >= 20:
                cs += 0.03; conf_list.append(f'Mature({level_age}b)')
            elif level_age >= 10:
                cs += 0.01

            confidence = min(0.92, 0.65 + cs)
            if age > 0:
                confidence = max(0.50, confidence - age * _AGE_CONFIDENCE_DECAY)
            if regime == 'SQUEEZE':
                confidence = round(confidence * 0.90, 3)
            if confidence < _MIN_CONFIDENCE_GATE:
                continue

            future_bars = df.iloc[i: i + MAX_BARS_IN_TRADE]
            eval_res    = _evaluate_signal(price, target_1, stop_loss, future_bars)

            wick_key  = 'wick_depth_atr' if direction == 'BUY' else 'wick_height_atr'
            wick_size = sweep.get(wick_key, 0)

            brain_sig = BrainSignal(
                brain_name='Liquidity-Sweep', specialization='Stop-Hunt Reversal Detector',
                method=method_str, direction=direction,
                confidence=round(confidence, 3), signal_strength=round(cs, 3),
                signal_age_candles=age,
                primary_evidence=f'{sweep["type"]} @ {sweep["swept_level"]:.6g} | ' + ' | '.join(conf_list),
                supporting_factors=conf_list, contra_factors=contra,
                method_confidence=0.85, regime_suitability='HIGH',
                reliability_flags={'panic_volume': vol_ratio >= _VOL_CEILING_MULT, 'aged_signal': age > 0},
                measurements={
                    'entry_price': round(price, 6), 'target_1': round(target_1, 6),
                    'stop_loss':   round(stop_loss, 6), 'swept_level': round(sweep['swept_level'], 6),
                    'wick_depth_atr': round(wick_size, 3), 'close_pct': round(close_pct, 3),
                    'level_age': level_age, 'sweep_age': age,
                    'vol_ratio': round(vol_ratio, 2), 'rsi': round(rsi_val, 1),
                    'touches': touches, 'rr_achieved': round(actual_rr, 2),
                    'delta_flow': round(delta, 3) if delta is not None else 0.0,
                    'decision_factor': f'LIQUIDITY_SWEEP_{sweep["type"]}',
                    'price_at_signal': round(price, 6), 'atr_at_signal': round(atr_val, 6),
                    'bars_used': i,
                },
                rr_t1_mult=round(gain / atr_val, 2) if atr_val > 0 else 2.0,
                rr_t2_mult=round(gain / atr_val * 1.5, 2) if atr_val > 0 else 3.0,
                rr_sl_mult=round(risk / atr_val, 2) if atr_val > 0 else 0.3,
            )

            regime_proxy = BrainSignal(
                brain_name='Regime-Ensemble', specialization='classifier',
                method='pre-computed', direction='HOLD', confidence=0.80,
                signal_strength=0.0, signal_age_candles=0,
                primary_evidence=f'Regime: {regime}',
                supporting_factors=[], contra_factors=[],
                method_confidence=0.80, regime_suitability='HIGH',
                measurements={'computed_regime': regime},
            )

            row = _build_trade_row(
                symbol=sym, bar_time=df.index[i],
                brain_signal=brain_sig, regime_signal=regime_proxy,
                eval_result=eval_res, extra_params=params,
            )
            row.update({
                'swept_level':  sweep.get('swept_level'), 'wick_depth_atr': wick_size,
                'sweep_age':    age, 'vol_ratio': round(vol_ratio, 2),
                'rsi':          round(rsi_val, 1), 'touches': touches,
                'delta_flow':   round(delta, 3) if delta is not None else 0.0,
                'close_pct':    round(close_pct, 3), 'level_age': level_age,
            })
            rows.append(row)

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════
# EXTENDED BREAKDOWN
# ═══════════════════════════════════════════════════════════════

def _extended_breakdown(df: pd.DataFrame) -> None:
    if df.empty:
        return
    decided = df[df['outcome'].isin(['WIN', 'LOSS'])]
    if decided.empty:
        print("  No decided trades.")
        return

    def _b(label, sub, indent='    '):
        if len(sub) == 0:
            return
        wins = (sub['outcome'] == 'WIN').sum()
        wr   = wins / len(sub)
        avgr = sub['r_achieved'].mean()
        print(f"{indent}{label:<32} n={len(sub):4d}  WR={wr:.1%}  avg_R={avgr:+.3f}  {'EV+' if avgr > 0 else 'EV-'}")

    print("\n  -- Regime --")
    for reg in sorted(decided['regime_used'].dropna().unique()):
        _b(reg, decided[decided['regime_used'] == reg])

    print("\n  -- Symbol --")
    for sym in sorted(decided['symbol'].dropna().unique()):
        _b(sym, decided[decided['symbol'] == sym])

    if 'rsi' in decided.columns and decided['rsi'].notna().any():
        print("\n  -- RSI (v3 block: <30 and >70) --")
        for label, mask in [
            ('RSI 30-40', (decided['rsi'] >= 30) & (decided['rsi'] < 40)),
            ('RSI 40-50', (decided['rsi'] >= 40) & (decided['rsi'] < 50)),
            ('RSI 50-60', (decided['rsi'] >= 50) & (decided['rsi'] < 60)),
            ('RSI 60-70', (decided['rsi'] >= 60) & (decided['rsi'] <= 70)),
        ]:
            _b(label, decided[mask])

    if 'level_age' in decided.columns and decided['level_age'].notna().any():
        print("\n  -- Level Age (H1 bars) --")
        for label, mask in [
            ('Age  5-10b', (decided['level_age'] >= 5)  & (decided['level_age'] < 10)),
            ('Age 10-20b', (decided['level_age'] >= 10) & (decided['level_age'] < 20)),
            ('Age 20-40b', (decided['level_age'] >= 20) & (decided['level_age'] < 40)),
            ('Age 40b+',   decided['level_age'] >= 40),
        ]:
            _b(label, decided[mask])

    if 'sweep_age' in decided.columns and decided['sweep_age'].notna().any():
        print("\n  -- Sweep Age --")
        for age in sorted(decided['sweep_age'].dropna().unique()):
            _b(f'Sweep age={int(age)}', decided[decided['sweep_age'] == age])

    if 'vol_ratio' in decided.columns and decided['vol_ratio'].notna().any():
        print("\n  -- Volume Ratio --")
        for label, mask in [
            ('Gate off (0)', decided['vol_ratio'] == 0),
            ('Vol <1.5x',    (decided['vol_ratio'] > 0)   & (decided['vol_ratio'] < 1.5)),
            ('Vol 1.5-2x',   (decided['vol_ratio'] >= 1.5) & (decided['vol_ratio'] < 2.0)),
            ('Vol 2-3x',     (decided['vol_ratio'] >= 2.0) & (decided['vol_ratio'] < 3.0)),
            ('Vol 3x+',      decided['vol_ratio'] >= 3.0),
        ]:
            _b(label, decided[mask])

    if 'touches' in decided.columns and decided['touches'].notna().any():
        print("\n  -- Level Cleanliness --")
        for label, mask in [
            ('0 touches',  decided['touches'] == 0),
            ('1 touch',    decided['touches'] == 1),
            ('2 touches',  decided['touches'] == 2),
            ('3+ touches', decided['touches'] >= 3),
        ]:
            _b(label, decided[mask])

    if 'close_pct' in decided.columns and decided['close_pct'].notna().any():
        print("\n  -- Close Strength --")
        for label, mask in [
            ('<50% weak',   decided['close_pct'] < 0.50),
            ('50-65%',      (decided['close_pct'] >= 0.50) & (decided['close_pct'] < 0.65)),
            ('65-80%',      (decided['close_pct'] >= 0.65) & (decided['close_pct'] < 0.80)),
            ('80%+ strong', decided['close_pct'] >= 0.80),
        ]:
            _b(label, decided[mask])


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main(days: int = 90) -> None:
    end        = datetime.utcnow()
    start      = end - timedelta(days=days)
    DATE_TAG   = end.strftime('%Y%m%d')
    GRID_CSV   = f'sweep_grid_v3_{days}d_{DATE_TAG}.csv'
    TRADES_CSV = f'sweep_trades_v3_{days}d_{DATE_TAG}.csv'

    n_combos        = len(list(itertools.product(*PARAM_GRID.values())))
    pivot_bars_list = PARAM_GRID['pivot_bars']

    log.info("=" * 75)
    log.info("Liquidity-Sweep v5 — H1 Equity Grid Backtest (v6 runner)")
    log.info(f"Symbols   : {SYMBOLS}")
    log.info(f"Timeframe : {TIMEFRAME}")
    log.info(f"Period    : {start.date()} -> {end.date()} ({days} days)")
    log.info(f"Grid      : {n_combos} combos x {len(SYMBOLS)} symbols")
    log.info(f"Baseline  : {BASELINE_PARAMS}")
    log.info("Acceptance: WR>=55%  EV>0.40R  signals>=20  MaxDD<8R")
    log.info("=" * 75)

    storage = PostgresStorage()

    log.info("Loading OHLCV data (1h)...")
    data_cache: dict = {}
    for sym in SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, start, end, storage)
        if df.empty:
            log.warning(f"  {sym}: NO DATA — skipping")
        else:
            data_cache[sym] = df
            log.info(f"  {sym}: {len(df)} bars ({df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d})")

    if not data_cache:
        log.error("No data. Check DB / SYMBOLS list.")
        return

    warmup = max(MIN_HIST_BARS, _MIN_HIST_BARS)
    log.info(f"\nPre-computing all indicators (warmup={warmup})...")
    cache_map: dict = {}
    for sym, df in data_cache.items():
        log.info(f"  {sym} ({len(df)} bars):")
        cache_map[sym] = precompute_all(df, warmup, pivot_bars_list)

    log.info(f"\nBaseline: {BASELINE_PARAMS}")
    base_df    = run_combo(BASELINE_PARAMS, data_cache, cache_map)
    base_stats = aggregate_results(base_df)
    baseline_ev = base_stats['ev']
    log.info(f"  Baseline -> n={base_stats['total']} WR={base_stats['wr']:.1%} "
             f"EV={baseline_ev:.3f} MaxDD={base_stats['max_dd_r']:.2f}R")

    if base_stats['total'] == 0:
        log.warning("  Baseline 0 signals — possible causes:")
        log.warning("  1. No H1 data for .NS symbols: check load_ohlcv('LT.NS', '1h', ...)")
        log.warning("  2. Volume gate too strict: try vol_spike_mult=0.0 first")

    keys   = list(PARAM_GRID.keys())
    combos = list(itertools.product(*PARAM_GRID.values()))
    log.info(f"\nRunning {len(combos)} combos...")

    grid_rows:  list = []
    all_trades: list = []

    for combo in combos:
        params   = dict(zip(keys, combo))
        combo_df = run_combo(params, data_cache, cache_map)
        stats    = aggregate_results(combo_df)
        ev_delta = stats['ev'] - baseline_ev

        regime_evs: dict = {}
        if not combo_df.empty and 'regime_used' in combo_df.columns:
            for reg in SWEEP_ALLOWED_REGIMES:
                sub     = combo_df[combo_df['regime_used'] == reg]
                sub_dec = sub[sub['outcome'].isin(['WIN', 'LOSS'])]
                k       = reg.lower().replace('_', '') + '_ev'
                regime_evs[k] = round(float(sub_dec['r_achieved'].mean()), 4) if len(sub_dec) > 0 else 0.0

        symbol_evs: dict = {}
        if not combo_df.empty and 'symbol' in combo_df.columns:
            for sym in SYMBOLS:
                sub     = combo_df[combo_df['symbol'] == sym]
                sub_dec = sub[sub['outcome'].isin(['WIN', 'LOSS'])]
                sk      = sym.replace('.', '_').lower() + '_ev'
                symbol_evs[sk] = round(float(sub_dec['r_achieved'].mean()), 4) if len(sub_dec) > 0 else 0.0

        meets = (stats['total'] >= 20 and stats['wr'] >= 0.55 and
                 stats['ev'] > 0.40 and stats['max_dd_r'] < 8.0)

        grid_rows.append({
            **params,
            'total_signals': stats['total'], 'wins': stats['wins'],
            'losses': stats['losses'], 'expired': stats['expired'],
            'win_rate': stats['wr'], 'avg_r': stats['avg_r'], 'ev': stats['ev'],
            'ev_vs_baseline': round(ev_delta, 4), 'max_dd_r': stats['max_dd_r'],
            'meets_criteria': meets, **regime_evs, **symbol_evs,
        })

        log.info(f"  vol={params['vol_spike_mult']} dist={params['min_level_dist_atr']} "
                 f"piv={params['pivot_bars']} age={params['min_level_age_bars']} | "
                 f"n={stats['total']:3d}  WR={stats['wr']:.1%}  EV={stats['ev']:.3f}  "
                 f"MaxDD={stats['max_dd_r']:.2f}R  {'MEETS' if meets else '--'}")

        if not combo_df.empty:
            for k, v in params.items():
                combo_df[f'param_{k}'] = v
            all_trades.append(combo_df)

    grid_df = pd.DataFrame(grid_rows).sort_values('ev', ascending=False)
    grid_df.to_csv(GRID_CSV, index=False)
    log.info(f"\nGrid  -> {GRID_CSV}")

    if all_trades:
        all_df = pd.concat(all_trades, ignore_index=True)
        all_df.to_csv(TRADES_CSV, index=False)
        log.info(f"Trades -> {TRADES_CSV} ({len(all_df)} rows)")

    print("\n" + "=" * 75)
    print(f"LIQUIDITY-SWEEP v5 GRID | {days}d | {len(SYMBOLS)} equity symbols")
    print(f"Baseline EV: {baseline_ev:.3f}R  |  Acceptance: WR>=55% EV>0.40R n>=20 MaxDD<8R")
    print("=" * 75)
    cols  = ['vol_spike_mult', 'min_level_dist_atr', 'pivot_bars', 'min_level_age_bars',
             'total_signals', 'win_rate', 'avg_r', 'ev', 'ev_vs_baseline', 'max_dd_r', 'meets_criteria']
    avail = [c for c in cols if c in grid_df.columns]
    print(grid_df[avail].head(15).to_string(index=False))
    print("=" * 75)

    accepted = grid_df[grid_df['meets_criteria'] == True]
    if not accepted.empty:
        best = accepted.iloc[0]
        print(f"\nACCEPTED:")
        print(f"  vol_spike_mult     = {best['vol_spike_mult']}")
        print(f"  min_level_dist_atr = {best['min_level_dist_atr']}")
        print(f"  pivot_bars         = {int(best['pivot_bars'])}")
        print(f"  min_level_age_bars = {int(best['min_level_age_bars'])}")
        print(f"  EV={best['ev']:.3f}R  WR={best['win_rate']:.1%}  "
              f"n={int(best['total_signals'])}  MaxDD={best['max_dd_r']:.2f}R")
        print(f"\n  Update liquidity_sweep.py constants with these values.")
        print(f"\n  Next: 180d confirmation:")
        print(f"    python -X utf8 -m market_agent.runner.run_liquidity_sweep_grid --days 180")
    else:
        best_row = grid_df.iloc[0]
        print(f"\nNo config meets criteria.")
        print(f"Best: vol={best_row['vol_spike_mult']} dist={best_row['min_level_dist_atr']} "
              f"pivot={int(best_row['pivot_bars'])} age={int(best_row['min_level_age_bars'])} | "
              f"EV={best_row['ev']:.3f} WR={best_row['win_rate']:.1%} "
              f"n={int(best_row['total_signals'])} MaxDD={best_row['max_dd_r']:.2f}R")
        if int(best_row['total_signals']) == 0:
            print("\n  ZERO SIGNALS — check:")
            print("  1. H1 data available for .NS symbols? run load_ohlcv('LT.NS','1h',...)")
            print("  2. Volume data present? Check DB for Volume column on equity H1")
            print("  3. Start with vol_spike_mult=0.0 to isolate volume gate")
        elif int(best_row['total_signals']) < 20:
            print("  Too few signals: try vol_spike_mult=0.0 or min_level_dist_atr=0.30")
        if best_row['max_dd_r'] >= 8.0:
            print("  MaxDD too high: increase vol_spike_mult or min_level_age_bars")

    if not grid_df.empty:
        best_p = {
            'vol_spike_mult':     float(grid_df.iloc[0]['vol_spike_mult']),
            'min_level_dist_atr': float(grid_df.iloc[0]['min_level_dist_atr']),
            'pivot_bars':         int(grid_df.iloc[0]['pivot_bars']),
            'min_level_age_bars': int(grid_df.iloc[0]['min_level_age_bars']),
        }
        print(f"\nExtended breakdown for best combo {best_p}:")
        best_df = run_combo(best_p, data_cache, cache_map)
        _extended_breakdown(best_df)

    print(f"\nNEXT STEPS:")
    print(f"  90d pass  -> run 180d: python -X utf8 -m market_agent.runner.run_liquidity_sweep_grid --days 180")
    print(f"  180d pass -> lock constants -> paper trade 1 week -> live trade")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=90)
    parser.add_argument(
        '--baseline-only',
        action='store_true',
        help='Run baseline params only (skip full grid).',
    )
    args = parser.parse_args()

    # Baseline-only mode: keep iteration tight when diagnosing poor results.
    # This does NOT change any signal logic; it only avoids running 108 grid combos.
    if args.baseline_only:
        # Inline baseline run: reuse main() flow but exit after baseline + breakdown.
        end        = datetime.utcnow()
        start      = end - timedelta(days=args.days)
        DATE_TAG   = end.strftime('%Y%m%d')
        TRADES_CSV = f'sweep_trades_baseline_{args.days}d_{DATE_TAG}.csv'

        pivot_bars_list = PARAM_GRID['pivot_bars']

        log.info("=" * 75)
        log.info("Liquidity-Sweep v5 — H1 Baseline Backtest (structure targets)")
        log.info(f"Symbols   : {SYMBOLS}")
        log.info(f"Timeframe : {TIMEFRAME}")
        log.info(f"Period    : {start.date()} -> {end.date()} ({args.days} days)")
        log.info(f"Baseline  : {BASELINE_PARAMS}")
        log.info("=" * 75)

        storage = PostgresStorage()

        log.info("Loading OHLCV data (1h)...")
        data_cache: dict = {}
        for sym in SYMBOLS:
            df = load_ohlcv(sym, TIMEFRAME, start, end, storage)
            if df.empty:
                log.warning(f"  {sym}: NO DATA — skipping")
            else:
                data_cache[sym] = df
                log.info(f"  {sym}: {len(df)} bars ({df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d})")

        if not data_cache:
            log.error("No data. Check DB / SYMBOLS list.")
            raise SystemExit(2)

        warmup = max(MIN_HIST_BARS, _MIN_HIST_BARS)
        log.info(f"\nPre-computing all indicators (warmup={warmup})...")
        cache_map: dict = {}
        for sym, df in data_cache.items():
            log.info(f"  {sym} ({len(df)} bars):")
            cache_map[sym] = precompute_all(df, warmup, pivot_bars_list)

        log.info(f"\nBaseline: {BASELINE_PARAMS}")
        base_df    = run_combo(BASELINE_PARAMS, data_cache, cache_map)
        base_stats = aggregate_results(base_df)
        log.info(f"  Baseline -> n={base_stats['total']} WR={base_stats['wr']:.1%} "
                 f"EV={base_stats['ev']:.3f} MaxDD={base_stats['max_dd_r']:.2f}R")

        if base_df.empty:
            log.warning("Baseline produced 0 signals.")
            raise SystemExit(0)

        base_df.to_csv(TRADES_CSV, index=False)
        log.info(f"Baseline trades -> {TRADES_CSV} ({len(base_df)} rows)")

        print(f"\nExtended breakdown for BASELINE {BASELINE_PARAMS}:")
        _extended_breakdown(base_df)
        raise SystemExit(0)

    main(days=args.days)