"""
market_agent/runner/run_liquidity_sweep_d1_grid.py
===================================================
Liquidity-Sweep Brain v2 — Daily (D1) Parameter Grid Backtest
===================================================

TIMEFRAME: 1d (Daily)
DATA:      614 bars (~2.5 years from Sept 2023)
WARMUP:    200 bars (backtester MIN_HIST_BARS)
USABLE:    ~414 bars (~1.65 years) for signal generation

WHY D1 WORKS FOR LIQUIDITY-SWEEP:
  Daily sweeps are institutional-scale stop-hunts:
  - Market makers push below a swing low to trigger retail stops
  - Price closes back above the level → bullish reversal confirmed
  All brain logic is ATR-relative → self-calibrates for D1 ranges.

D1-SPECIFIC PARAMETER ADJUSTMENTS vs H1:
  _MIN_LEVEL_AGE_BARS=5  → 5 trading DAYS (was 5 hours on H1). Good.
  _SWING_LOOKBACK=80     → 80 trading DAYS (~4 months structure). Good.
  _TOUCH_LOOKBACK=40     → 40 trading DAYS (~2 months). Good.
  _VOL_LOOKBACK=20       → 20 trading DAYS (1 month baseline). Good.
  MAX_BARS_IN_TRADE=20   → 20 trading DAYS (1 month to hit target). Good.
  R:R min 2.0            → More achievable on D1 (daily ranges are wide). Good.

D1 GRID PARAMETERS (3×3×3 = 27 combos):
  vol_spike_mult:     0.0 = gate disabled, 1.5 = permissive, 2.0 = strict
  min_level_dist_atr: 0.30 = permissive, 0.50 = baseline, 0.80 = strict
  pivot_bars:         2 = many levels, 3 = baseline, 5 = strict/clean

SYMBOLS (7):
  LT.NS, TATASTEEL.NS, AAPL, RELIANCE.NS, ITC.NS, AMD, GOOGL

ACCEPTANCE CRITERIA:
  WR >= 55%   (reversal pattern needs high accuracy)
  EV > 0.40R  (positive expected value per trade)
  signals >= 15 (D1 generates fewer signals than H1 — floor lowered to 15)
  MaxDD < 8R  (untradeable if exceeded)

NOTE: D1 signal count will be LOWER than H1 (fewer bars, daily structure).
  H1 target was 20-60 signals per 90d.
  D1 target is 15-50 signals per 365d (7 symbols × ~2-7 per symbol per year).

Usage:
  python -X utf8 -m market_agent.runner.run_liquidity_sweep_d1_grid
  python -X utf8 -m market_agent.runner.run_liquidity_sweep_d1_grid --days 365
  python -X utf8 -m market_agent.runner.run_liquidity_sweep_d1_grid --days 500
"""
from __future__ import annotations

import argparse
import itertools
import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from market_agent.brain.liquidity_sweep import (
    # All constants — imported so grid and brain cannot drift
    _VOL_SPIKE_MULT, _MIN_SWEEP_DEPTH_ATR, _MAX_SWEEP_DEPTH_ATR, _MIN_SWEEP_DEPTH_PCT,
    _MIN_LEVEL_DIST_ATR, _MIN_LEVEL_AGE_BARS, _SWING_LOOKBACK, _PIVOT_BARS,
    _MAX_LEVELS_TO_CHECK, _SWEEP_CANDLE_LOOKBACK, _SL_BUFFER_ATR,
    _RSI_BLOCK_LOW, _RSI_BLOCK_HIGH, _MAX_TOUCHES_CLEAN, _LEVEL_TOLERANCE_PCT,
    _TOUCH_LOOKBACK, _TOUCH_EXCLUDE_RECENT, _AGE_CONFIDENCE_DECAY,
    _MIN_CONFIDENCE_GATE, _MIN_HIST_BARS, _VOL_LOOKBACK,
    # Helper functions reused inline
    _find_swing_levels, _volume_confirmation, _rsi_gate_ok,
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
log = logging.getLogger('run_liquidity_sweep_d1_grid')


# ── Settings ──────────────────────────────────────────────────────────────────

# 7-symbol basket: mix of proven H1 edge symbols + natural D1 candidates
# MSFT and INFY.NS excluded — 0 bars in DB per diagnostic
SYMBOLS = [
    'LT.NS',          # Proven H1 edge — large-cap Indian infrastructure
    'TATASTEEL.NS',   # Proven H1 edge — high volatility, clear sweeps
    'AAPL',           # Proven H1 edge — institutional US large-cap
    'RELIANCE.NS',    # Natural D1 — India's most liquid stock
    'ITC.NS',         # Natural D1 — slow, mean-reverting
    'AMD',            # Checking if D1 fixes H1 noise
    'GOOGL',          # Additional clean US equity
]

TIMEFRAME = '1d'

SWEEP_ALLOWED_REGIMES = {'VOLATILE', 'TRENDING_UP', 'TRENDING_DOWN', 'RANGING'}

# D1 acceptance: signals floor LOWERED to 15 (daily timeframe generates fewer signals)
# WR and EV criteria unchanged from H1
ACCEPTANCE = dict(min_signals=15, min_wr=0.55, min_ev=0.40, max_dd=8.0)

PARAM_GRID = {
    'vol_spike_mult':     [0.0, 1.5, 2.0],
    'min_level_dist_atr': [0.30, 0.50, 0.80],
    'pivot_bars':         [2, 3, 5],
}

BASELINE_PARAMS = dict(
    vol_spike_mult     = _VOL_SPIKE_MULT,
    min_level_dist_atr = _MIN_LEVEL_DIST_ATR,
    pivot_bars         = _PIVOT_BARS,
)


# ═══════════════════════════════════════════════════════════════
# FULL PRE-COMPUTATION — O(n) setup, O(1) lookup in inner loop
# Same pattern as H1 grid runner — eliminates O(n²) inner loop.
# ═══════════════════════════════════════════════════════════════

def _precompute_atr_series(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """ATR as full vectorised series. Returns numpy array for O(1) index access."""
    high       = df['High']
    low        = df['Low']
    prev_close = df['Close'].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().values


def _precompute_rsi_series(df: pd.DataFrame, period: int = 14) -> np.ndarray:
    """RSI as full vectorised series. Returns numpy array for O(1) index access."""
    close    = df['Close']
    delta    = close.diff()
    gain     = delta.where(delta > 0, 0.0)
    loss     = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, float('nan'))
    return (100 - (100 / (1 + rs))).values


def _precompute_regimes(df: pd.DataFrame, warmup: int) -> list:
    """Regime string for every bar, computed once per symbol (not per combo)."""
    n       = len(df)
    regimes = [None] * n
    for i in range(warmup, n - MAX_BARS_IN_TRADE):
        try:
            sig        = regime_ensemble_signal(df.iloc[:i])
            regimes[i] = sig.measurements.get('computed_regime', 'RANGING')
        except Exception:
            regimes[i] = None
    return regimes


def precompute_indicators(df: pd.DataFrame, warmup: int) -> dict:
    """
    Pre-compute all O(n) indicators once per symbol.
    Returns numpy arrays for O(1) per-bar lookup in inner loop.
    On D1: 7 symbols × ~414 bars = ~2,898 regime calls total (vs 78,246 per-combo).
    """
    return {
        'atr':    _precompute_atr_series(df),
        'rsi':    _precompute_rsi_series(df),
        'regime': _precompute_regimes(df, warmup),
    }


# ═══════════════════════════════════════════════════════════════
# INLINE SWEEP DETECTORS (with grid-param injection)
# ═══════════════════════════════════════════════════════════════

def _detect_bullish_sweep_grid(
    hist: pd.DataFrame,
    swing_lows: list,
    atr: float,
    current_price: float,
    min_level_dist_atr: float,   # GRID PARAM
) -> dict | None:
    """Inline bullish sweep detector with injected min_level_dist_atr.
    Identical to H1 version — ATR-relative so D1 adapts automatically.
    """
    if not swing_lows or len(hist) < 5:
        return None

    min_depth      = max(atr * _MIN_SWEEP_DEPTH_ATR, current_price * _MIN_SWEEP_DEPTH_PCT)
    max_depth      = atr * _MAX_SWEEP_DEPTH_ATR
    min_level_dist = atr * min_level_dist_atr

    for age in range(min(_SWEEP_CANDLE_LOOKBACK, len(hist))):
        candle  = hist.iloc[-(1 + age)]
        c_high  = float(candle['High'])
        c_low   = float(candle['Low'])
        c_close = float(candle['Close'])
        c_range = c_high - c_low

        for level_price, level_age in swing_lows[:_MAX_LEVELS_TO_CHECK]:
            if level_age < _MIN_LEVEL_AGE_BARS:
                continue
            if (current_price - level_price) < min_level_dist:
                continue
            below_by = level_price - c_low
            if below_by < min_depth or below_by > max_depth:
                continue
            if c_close <= level_price:
                continue
            close_pct = (c_close - c_low) / c_range if c_range > 0 else 0.5
            if close_pct < 0.50:
                continue
            return {
                'type': 'BULLISH_SWEEP', 'swept_level': level_price, 'level_age': level_age,
                'wick_depth_atr': round(below_by / atr, 3) if atr > 0 else 0,
                'close_pct': round(close_pct, 3), 'entry': float(hist['Close'].iloc[-1]),
                'candles_since_sweep': age,
            }
    return None


def _detect_bearish_sweep_grid(
    hist: pd.DataFrame,
    swing_highs: list,
    atr: float,
    current_price: float,
    min_level_dist_atr: float,   # GRID PARAM
) -> dict | None:
    """Inline bearish sweep detector with injected min_level_dist_atr.
    Identical to H1 version — ATR-relative so D1 adapts automatically.
    """
    if not swing_highs or len(hist) < 5:
        return None

    min_depth      = max(atr * _MIN_SWEEP_DEPTH_ATR, current_price * _MIN_SWEEP_DEPTH_PCT)
    max_depth      = atr * _MAX_SWEEP_DEPTH_ATR
    min_level_dist = atr * min_level_dist_atr

    for age in range(min(_SWEEP_CANDLE_LOOKBACK, len(hist))):
        candle  = hist.iloc[-(1 + age)]
        c_high  = float(candle['High'])
        c_low   = float(candle['Low'])
        c_close = float(candle['Close'])
        c_range = c_high - c_low

        for level_price, level_age in swing_highs[:_MAX_LEVELS_TO_CHECK]:
            if level_age < _MIN_LEVEL_AGE_BARS:
                continue
            if (level_price - current_price) < min_level_dist:
                continue
            above_by = c_high - level_price
            if above_by < min_depth or above_by > max_depth:
                continue
            if c_close >= level_price:
                continue
            close_pct = (c_close - c_low) / c_range if c_range > 0 else 0.5
            if close_pct > 0.50:
                continue
            return {
                'type': 'BEARISH_SWEEP', 'swept_level': level_price, 'level_age': level_age,
                'wick_height_atr': round(above_by / atr, 3) if atr > 0 else 0,
                'close_pct': round(close_pct, 3), 'entry': float(hist['Close'].iloc[-1]),
                'candles_since_sweep': age,
            }
    return None


# ═══════════════════════════════════════════════════════════════
# PATCHED SIGNAL FACTORY
# ═══════════════════════════════════════════════════════════════

def _make_sweep_fn(
    vol_spike_mult:     float,
    min_level_dist_atr: float,
    pivot_bars:         int,
) -> callable:
    """
    Returns a patched v2 signal function with grid params injected.
    Logic mirrors liquidity_sweep.py gate-for-gate.
    ANY gate change in liquidity_sweep.py MUST be reflected here.
    """

    def _fn(
        hist:    pd.DataFrame,
        regime:  str   = 'VOLATILE',
        symbol:  str   = '',
        atr_val: float = 0.0,   # PRE-COMPUTED scalar — O(1) from cache
        rsi_val: float = 50.0,  # PRE-COMPUTED scalar — O(1) from cache
    ) -> BrainSignal:
        method_str = f'Sweeps-D1-v2(vol={vol_spike_mult} dist={min_level_dist_atr} piv={pivot_bars})'

        def _hold(reason, conf=0.30, df_key='GATE', meas=None):
            price = float(hist['Close'].iloc[-1]) if len(hist) > 0 else 0.0
            m = {'decision_factor': df_key, 'price_at_signal': round(price, 6), 'bars_used': len(hist)}
            if meas:
                m.update(meas)
            return BrainSignal(
                brain_name='Liquidity-Sweep-D1', specialization='Stop-Hunt Reversal Detector (Daily)',
                method=method_str, direction='HOLD', confidence=conf,
                signal_strength=0.0, signal_age_candles=0, primary_evidence=reason,
                supporting_factors=[], contra_factors=[],
                method_confidence=0.0, regime_suitability='LOW', measurements=m,
                rr_t1_mult=2.0, rr_t2_mult=3.5, rr_sl_mult=_SL_BUFFER_ATR,
            )

        if len(hist) < _MIN_HIST_BARS:
            return _hold('Insufficient data', 0.25, 'GATE_DATA')
        if regime == 'CHAOS':
            return _hold('CHAOS', 0.25, 'GATE_CHAOS')

        price = float(hist['Close'].iloc[-1])

        # Use pre-computed ATR — O(1), not O(n)
        atr = atr_val
        if atr <= 0:
            return _hold('ATR=0', 0.25, 'GATE_ATR')

        atr_pct = atr / price if price > 0 else 0.0

        # Use pre-computed RSI — O(1), not O(n)
        if np.isnan(rsi_val):
            rsi_val = 50.0

        swing_levels = _find_swing_levels(hist, _SWING_LOOKBACK, pivot_bars, price)

        bull_sweep = _detect_bullish_sweep_grid(hist, swing_levels['lows'],  atr, price, min_level_dist_atr)
        bear_sweep = _detect_bearish_sweep_grid(hist, swing_levels['highs'], atr, price, min_level_dist_atr)

        if not bull_sweep and not bear_sweep:
            return _hold('No sweep', 0.40, 'GATE_NO_SWEEP', {
                'rsi': round(rsi_val, 1), 'atr_at_signal': round(atr, 6),
                'n_swing_highs': len(swing_levels['highs']),
                'n_swing_lows':  len(swing_levels['lows']),
            })

        if bull_sweep and bear_sweep:
            if bull_sweep.get('wick_depth_atr', 0) >= bear_sweep.get('wick_height_atr', 0):
                bear_sweep = None
            else:
                bull_sweep = None

        sweep     = bull_sweep or bear_sweep
        direction = 'BUY' if bull_sweep else 'SELL'
        age       = sweep.get('candles_since_sweep', 0)

        # Regime directional filter (FIX-H from v2)
        if regime == 'TRENDING_DOWN' and direction == 'SELL':
            return _hold('SELL blocked in TRENDING_DOWN', 0.38, 'GATE_REGIME_DIR')
        if regime == 'TRENDING_UP' and direction == 'BUY':
            return _hold('BUY blocked in TRENDING_UP', 0.38, 'GATE_REGIME_DIR')

        # RSI neutral gate (FIX-G from v2) — scalar, no series needed
        if rsi_val < _RSI_BLOCK_LOW or rsi_val > _RSI_BLOCK_HIGH:
            return _hold(f'RSI extreme ({rsi_val:.1f})', 0.38, 'GATE_RSI', {'rsi': round(rsi_val, 1)})

        # Volume hard gate (FIX-E from v2)
        vol_passes, vol_ratio = _volume_confirmation(hist, vol_spike_mult, age=age)
        if vol_spike_mult > 0 and not vol_passes:
            return _hold(f'Vol {vol_ratio:.1f}x < {vol_spike_mult}x', 0.38, 'GATE_VOL', {'vol_ratio': vol_ratio})

        close_pct    = sweep.get('close_pct', 0.5)
        strong_close = (direction == 'BUY' and close_pct > 0.70) or (direction == 'SELL' and close_pct < 0.30)
        touches      = _count_level_touches(hist, sweep['swept_level'])
        clean_level  = touches <= _MAX_TOUCHES_CLEAN
        delta_flow   = compute_delta_flow(hist)
        delta_ok     = (delta_flow is not None and (
            (direction == 'BUY' and delta_flow > 0.15) or (direction == 'SELL' and delta_flow < -0.15)
        ))

        target_1, stop_loss, actual_rr = _calc_trade_levels(
            direction=direction, entry=price, swept_level=sweep['swept_level'],
            swing_levels=swing_levels, atr=atr, min_rr=2.0,
        )

        base_conf = 0.65
        cs = 0.0
        conf_list, contra = [], []

        if vol_ratio > 0:
            cs += 0.08; conf_list.append(f'Vol={vol_ratio:.1f}x')
        if strong_close:
            cs += 0.08; conf_list.append(f'Close={close_pct:.0%}')
        else:
            contra.append(f'Weak close')
        if clean_level:
            cs += 0.06; conf_list.append(f'Clean({touches}t)')
        else:
            contra.append(f'Used({touches}t)')
        if delta_ok and delta_flow is not None:
            cs += 0.05; conf_list.append(f'Delta={delta_flow:+.2f}')
        if 35.0 <= rsi_val <= 65.0:
            cs += 0.03; conf_list.append(f'RSI={rsi_val:.1f}')
        level_age = sweep.get('level_age', _MIN_LEVEL_AGE_BARS)
        # On D1: level_age >= 20 bars = 1 month of structural significance
        if level_age >= 20:
            cs += 0.03; conf_list.append(f'Mature({level_age}d)')

        confidence = min(0.92, base_conf + cs)
        if age > 0:
            confidence = max(0.50, confidence - age * _AGE_CONFIDENCE_DECAY)
        if regime == 'SQUEEZE':
            confidence = round(confidence * 0.90, 3)

        # Confidence hard gate (FIX-I from v2)
        if confidence < _MIN_CONFIDENCE_GATE:
            return _hold(f'Conf {confidence:.0%} < {_MIN_CONFIDENCE_GATE:.0%}', confidence, 'GATE_CONF')

        risk_in_atr = abs(price - stop_loss) / atr if atr > 0 else 1.0
        t1_in_atr   = abs(target_1 - price)  / atr if atr > 0 else 2.0
        wick_key    = 'wick_depth_atr' if direction == 'BUY' else 'wick_height_atr'
        wick_size   = sweep.get(wick_key, 0)

        return BrainSignal(
            brain_name='Liquidity-Sweep-D1', specialization='Stop-Hunt Reversal Detector (Daily)',
            method=method_str, direction=direction,
            confidence=round(confidence, 3), signal_strength=round(cs, 3),
            signal_age_candles=age,
            primary_evidence=f'{sweep["type"]} at {sweep["swept_level"]:.6g} | ' + ' | '.join(conf_list),
            supporting_factors=conf_list, contra_factors=contra,
            method_confidence=0.85,
            regime_suitability='HIGH' if regime in SWEEP_ALLOWED_REGIMES else 'MEDIUM',
            reliability_flags={'overused_level': not clean_level, 'aged_signal': age > 0},
            measurements={
                'entry_price':        round(price, 6), 'target_1': round(target_1, 6),
                'stop_loss':          round(stop_loss, 6), 'swept_level': round(sweep['swept_level'], 6),
                'wick_depth_atr':     round(wick_size, 3), 'close_pct':  round(close_pct, 3),
                'level_age':          level_age, 'sweep_age': age,
                'vol_ratio':          round(vol_ratio, 2), 'rsi': round(rsi_val, 1),
                'touches':            touches, 'rr_achieved': round(actual_rr, 2),
                'delta_flow':         round(delta_flow, 3) if delta_flow is not None else 0.0,
                'n_swing_highs':      len(swing_levels['highs']),
                'n_swing_lows':       len(swing_levels['lows']),
                'decision_factor':    f'LIQUIDITY_SWEEP_D1_{sweep["type"]}',
                'price_at_signal':    round(price, 6), 'atr_at_signal': round(atr, 6),
                'atr_pct_at_signal':  round(atr_pct * 100, 3), 'bars_used': len(hist),
                'indicator_1_name':   'vol_ratio',      'indicator_1_value': round(vol_ratio, 3),
                'indicator_2_name':   'rsi',            'indicator_2_value': round(rsi_val, 1),
                'indicator_3_name':   'wick_depth_atr', 'indicator_3_value': round(wick_size, 3),
            },
            rr_t1_mult=round(t1_in_atr, 2),
            rr_t2_mult=round(t1_in_atr * 1.5, 2),
            rr_sl_mult=round(risk_in_atr, 2),
        )

    return _fn


# ═══════════════════════════════════════════════════════════════
# PER-COMBO BACKTEST (uses pre-computed regimes)
# ═══════════════════════════════════════════════════════════════

def run_combo(
    params:          dict,
    data_cache:      dict,
    indicator_cache: dict,   # pre-computed atr, rsi, regime per symbol
) -> pd.DataFrame:
    """
    Bar-by-bar backtest for one parameter combo.
    indicator_cache[sym]['atr'][i]    = ATR scalar at bar i  (O(1))
    indicator_cache[sym]['rsi'][i]    = RSI scalar at bar i  (O(1))
    indicator_cache[sym]['regime'][i] = regime string at bar i (O(1))
    """
    brain_fn = _make_sweep_fn(**params)
    rows     = []
    warmup   = max(MIN_HIST_BARS, _MIN_HIST_BARS)

    for sym, df in data_cache.items():
        n          = len(df)
        cache      = indicator_cache.get(sym, {})
        atr_arr    = cache.get('atr',    np.zeros(n))
        rsi_arr    = cache.get('rsi',    np.full(n, 50.0))
        regime_arr = cache.get('regime', [None] * n)

        for i in range(warmup, n - MAX_BARS_IN_TRADE):
            regime  = regime_arr[i]
            atr_val = float(atr_arr[i])
            rsi_val = float(rsi_arr[i])

            if regime is None or regime == 'CHAOS' or regime not in SWEEP_ALLOWED_REGIMES:
                continue
            if np.isnan(atr_val) or atr_val <= 0:
                continue

            hist        = df.iloc[:i]
            future_bars = df.iloc[i: i + MAX_BARS_IN_TRADE]

            try:
                brain_sig = brain_fn(
                    hist, regime=regime, symbol=sym,
                    atr_val=atr_val, rsi_val=rsi_val,
                )
            except Exception as e:
                log.debug(f"brain_fn {sym} bar {i}: {e}")
                continue

            if brain_sig.direction == 'HOLD':
                continue

            m     = brain_sig.measurements or {}
            entry = float(m.get('entry_price', 0) or 0)
            t1    = float(m.get('target_1',    0) or 0)
            sl    = float(m.get('stop_loss',   0) or 0)
            if entry <= 0 or t1 <= 0 or sl <= 0:
                continue

            risk = abs(entry - sl)
            gain = abs(t1 - entry)
            if risk <= 0 or (gain / risk) < 1.5:
                continue

            eval_res = _evaluate_signal(entry, t1, sl, future_bars)

            # Build regime signal proxy for _build_trade_row
            from market_agent.brain.brain_contract import BrainSignal as BS
            regime_sig_proxy = BS(
                brain_name='Regime-Ensemble', specialization='classifier',
                method='pre-computed', direction='HOLD', confidence=0.80,
                signal_strength=0.0, signal_age_candles=0, primary_evidence=f'Regime: {regime}',
                supporting_factors=[], contra_factors=[], method_confidence=0.80,
                regime_suitability='HIGH', measurements={'computed_regime': regime},
            )

            row = _build_trade_row(
                symbol=sym, bar_time=df.index[i],
                brain_signal=brain_sig, regime_signal=regime_sig_proxy,
                eval_result=eval_res, extra_params=params,
            )
            row.update({
                'swept_level':    m.get('swept_level'), 'wick_depth_atr': m.get('wick_depth_atr'),
                'sweep_age':      m.get('sweep_age', 0), 'vol_ratio':      m.get('vol_ratio'),
                'rsi':            m.get('rsi'), 'touches':         m.get('touches'),
                'delta_flow':     m.get('delta_flow'), 'close_pct':       m.get('close_pct'),
                'level_age':      m.get('level_age'),
            })
            rows.append(row)

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════
# EXTENDED BREAKDOWN (D1-adapted labels)
# ═══════════════════════════════════════════════════════════════

def _extended_breakdown(df: pd.DataFrame) -> None:
    if df.empty:
        return
    decided = df[df['outcome'].isin(['WIN', 'LOSS'])]
    if decided.empty:
        print("  No decided trades in breakdown (all expired?).")
        return

    def _bucket(label, sub, indent='    '):
        if len(sub) == 0:
            return
        wins = (sub['outcome'] == 'WIN').sum()
        wr   = wins / len(sub)
        avgr = sub['r_achieved'].mean()
        print(f"{indent}{label:<35} n={len(sub):4d}  WR={wr:.1%}  avg_R={avgr:+.3f}  {'EV+' if avgr > 0 else 'EV-'}")

    print("\n  -- Regime Breakdown --")
    for reg in sorted(decided['regime_used'].dropna().unique()):
        _bucket(reg, decided[decided['regime_used'] == reg])

    print("\n  -- Symbol Breakdown --")
    for sym in sorted(decided['symbol'].dropna().unique()):
        _bucket(sym, decided[decided['symbol'] == sym])

    if 'rsi' in decided.columns and decided['rsi'].notna().any():
        print("\n  -- RSI at Sweep --")
        for label, mask in [
            ('RSI 25-35', (decided['rsi'] >= 25) & (decided['rsi'] < 35)),
            ('RSI 35-50', (decided['rsi'] >= 35) & (decided['rsi'] < 50)),
            ('RSI 50-65', (decided['rsi'] >= 50) & (decided['rsi'] <= 65)),
            ('RSI 65-75', (decided['rsi'] > 65)  & (decided['rsi'] <= 75)),
        ]:
            _bucket(label, decided[mask])

    if 'level_age' in decided.columns and decided['level_age'].notna().any():
        # D1 buckets: days instead of bars (1 bar = 1 trading day)
        print("\n  -- Level Age (trading days since pivot formed) --")
        for label, mask in [
            ('Age  5-10d',  (decided['level_age'] >= 5)  & (decided['level_age'] < 10)),
            ('Age 10-20d',  (decided['level_age'] >= 10) & (decided['level_age'] < 20)),
            ('Age 20-40d',  (decided['level_age'] >= 20) & (decided['level_age'] < 40)),
            ('Age 40-80d',  (decided['level_age'] >= 40) & (decided['level_age'] < 80)),
            ('Age 80d+',    decided['level_age'] >= 80),
        ]:
            _bucket(label, decided[mask])

    if 'sweep_age' in decided.columns and decided['sweep_age'].notna().any():
        print("\n  -- Sweep Age (days since sweep candle) --")
        for age in sorted(decided['sweep_age'].dropna().unique()):
            _bucket(f'Sweep age={int(age)}d', decided[decided['sweep_age'] == age])

    if 'vol_ratio' in decided.columns and decided['vol_ratio'].notna().any():
        print("\n  -- Volume Ratio --")
        for label, mask in [
            ('Gate off (vol=0)', decided['vol_ratio'] == 0),
            ('Vol < 1.5x',       (decided['vol_ratio'] > 0) & (decided['vol_ratio'] < 1.5)),
            ('Vol 1.5-2.0x',     (decided['vol_ratio'] >= 1.5) & (decided['vol_ratio'] < 2.0)),
            ('Vol 2.0-3.0x',     (decided['vol_ratio'] >= 2.0) & (decided['vol_ratio'] < 3.0)),
            ('Vol 3.0x+',        decided['vol_ratio'] >= 3.0),
        ]:
            _bucket(label, decided[mask])

    if 'touches' in decided.columns and decided['touches'].notna().any():
        print("\n  -- Level Cleanliness --")
        for label, mask in [
            ('0 touches',  decided['touches'] == 0),
            ('1 touch',    decided['touches'] == 1),
            ('2 touches',  decided['touches'] == 2),
            ('3+ touches', decided['touches'] >= 3),
        ]:
            _bucket(label, decided[mask])

    # D1-specific: outcome timing (how many days to hit target/stop)
    if 'bars_to_outcome' in decided.columns and decided['bars_to_outcome'].notna().any():
        print("\n  -- Days to Outcome (D1 — 1 bar = 1 trading day) --")
        for label, mask in [
            ('1-3 days',   (decided['bars_to_outcome'] >= 1)  & (decided['bars_to_outcome'] <= 3)),
            ('4-7 days',   (decided['bars_to_outcome'] >= 4)  & (decided['bars_to_outcome'] <= 7)),
            ('8-14 days',  (decided['bars_to_outcome'] >= 8)  & (decided['bars_to_outcome'] <= 14)),
            ('15-20 days', (decided['bars_to_outcome'] >= 15) & (decided['bars_to_outcome'] <= 20)),
        ]:
            _bucket(label, decided[mask])


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main(days: int = 500) -> None:
    """
    Default to 500 days to maximize use of available 614-bar history.
    This gives ~414 usable signal bars (after 200 warmup).

    You can also run:
      --days 365 : 1 year (standard annual backtest)
      --days 500 : full history available (~2 years)
    """
    end   = datetime.utcnow()
    start = end - timedelta(days=days)
    DATE_TAG   = end.strftime('%Y%m%d')
    GRID_CSV   = f'sweep_d1_grid_v2_{days}d_{DATE_TAG}.csv'
    TRADES_CSV = f'sweep_d1_trades_v2_{days}d_{DATE_TAG}.csv'

    n_combos = len(list(itertools.product(*PARAM_GRID.values())))

    log.info("=" * 75)
    log.info("Liquidity-Sweep v2 — D1 Parameter Grid Backtest")
    log.info(f"Symbols   : {SYMBOLS}")
    log.info(f"Timeframe : {TIMEFRAME}")
    log.info(f"Period    : {start.date()} -> {end.date()} ({days} days)")
    log.info(f"Grid      : {n_combos} combos x {len(SYMBOLS)} symbols")
    log.info(f"Baseline  : {BASELINE_PARAMS}")
    log.info(f"Acceptance: WR>={ACCEPTANCE['min_wr']:.0%}  EV>{ACCEPTANCE['min_ev']}R  "
             f"signals>={ACCEPTANCE['min_signals']}  MaxDD<{ACCEPTANCE['max_dd']}R")
    log.info(f"Note: D1 signal floor = {ACCEPTANCE['min_signals']} (lower than H1 due to fewer bars)")
    log.info("Regime pre-computed once per bar per symbol (speedup vs per-combo)")
    log.info("=" * 75)

    storage = PostgresStorage()

    log.info("Loading OHLCV data (1d interval)...")
    data_cache: dict = {}
    for sym in SYMBOLS:
        df = load_ohlcv(sym, TIMEFRAME, start, end, storage)
        if df.empty:
            log.warning(f"  {sym}: NO DATA — skipping (check DB)")
        else:
            data_cache[sym] = df
            log.info(f"  {sym}: {len(df)} bars ({df.index[0]:%Y-%m-%d} -> {df.index[-1]:%Y-%m-%d})")

    if not data_cache:
        log.error("No data loaded. Check DB / SYMBOLS list.")
        return

    # Warn if any symbol has fewer than 300 bars (results unreliable below this)
    for sym, df in data_cache.items():
        if len(df) < 300:
            log.warning(f"  {sym}: only {len(df)} bars — results may be unreliable (need 300+)")

    # ── Pre-compute all indicators ONCE per symbol ────────────────────────────
    warmup = max(MIN_HIST_BARS, _MIN_HIST_BARS)
    log.info(f"\nPre-computing ATR, RSI, regime per symbol (warmup={warmup})...")
    indicator_cache: dict = {}
    for sym, df in data_cache.items():
        log.info(f"  {sym}: computing indicators ({len(df)} bars)...")
        indicator_cache[sym] = precompute_indicators(df, warmup)
        regime_counts: dict = {}
        for r in indicator_cache[sym]['regime']:
            if r:
                regime_counts[r] = regime_counts.get(r, 0) + 1
        usable_bars = sum(regime_counts.values())
        atr_vals = [v for v in indicator_cache[sym]['atr'] if not np.isnan(v) and v > 0]
        log.info(f"  {sym}: {usable_bars} usable bars | regimes={regime_counts} | "
                 f"ATR [{min(atr_vals):.2f}, {max(atr_vals):.2f}]")

    # ── Baseline ──────────────────────────────────────────────────────────────
    log.info(f"\nBaseline: {BASELINE_PARAMS}")
    base_df    = run_combo(BASELINE_PARAMS, data_cache, indicator_cache)
    base_stats = aggregate_results(base_df)
    baseline_ev = base_stats['ev']
    log.info(f"  Baseline -> n={base_stats['total']} WR={base_stats['wr']:.1%} "
             f"EV={baseline_ev:.3f} MaxDD={base_stats['max_dd_r']:.2f}R")

    if base_stats['total'] == 0:
        log.warning("  Baseline 0 signals. Possible causes:")
        log.warning("  1. Vol data missing: try vol_spike_mult=0.0")
        log.warning("  2. No levels pass min_level_dist_atr: try 0.30")
        log.warning("  3. Indian equities have different price scales — check ATR values")

    # ── Grid ──────────────────────────────────────────────────────────────────
    keys   = list(PARAM_GRID.keys())
    combos = list(itertools.product(*PARAM_GRID.values()))
    log.info(f"\nRunning {len(combos)} combos (regime pre-cached)...")

    grid_rows:  list = []
    all_trades: list = []

    for combo in combos:
        params   = dict(zip(keys, combo))
        log.info(f"  {params}")

        combo_df = run_combo(params, data_cache, indicator_cache)
        stats    = aggregate_results(combo_df)
        ev_delta = stats['ev'] - baseline_ev

        regime_evs: dict = {}
        if not combo_df.empty and 'regime_used' in combo_df.columns:
            for reg in SWEEP_ALLOWED_REGIMES:
                sub     = combo_df[combo_df['regime_used'] == reg]
                sub_dec = sub[sub['outcome'].isin(['WIN', 'LOSS'])]
                key     = reg.lower().replace('_', '') + '_ev'
                regime_evs[key] = round(float(sub_dec['r_achieved'].mean()), 4) if len(sub_dec) > 0 else 0.0

        # Symbol-level EV breakdown
        symbol_evs: dict = {}
        if not combo_df.empty and 'symbol' in combo_df.columns:
            for sym in SYMBOLS:
                sub     = combo_df[combo_df['symbol'] == sym]
                sub_dec = sub[sub['outcome'].isin(['WIN', 'LOSS'])]
                safe_key = sym.replace('.', '_').lower() + '_ev'
                symbol_evs[safe_key] = round(float(sub_dec['r_achieved'].mean()), 4) if len(sub_dec) > 0 else 0.0

        meets = (
            stats['total']    >= ACCEPTANCE['min_signals'] and
            stats['wr']       >= ACCEPTANCE['min_wr']      and
            stats['ev']       >  ACCEPTANCE['min_ev']      and
            stats['max_dd_r'] <  ACCEPTANCE['max_dd']
        )

        grid_rows.append({
            **params, 'total_signals': stats['total'],
            'wins': stats['wins'], 'losses': stats['losses'], 'expired': stats['expired'],
            'win_rate': stats['wr'], 'avg_r': stats['avg_r'], 'ev': stats['ev'],
            'ev_vs_baseline': round(ev_delta, 4), 'max_dd_r': stats['max_dd_r'],
            'meets_criteria': meets, **regime_evs, **symbol_evs,
        })

        log.info(f"    n={stats['total']:3d}  WR={stats['wr']:.1%}  EV={stats['ev']:.3f}  "
                 f"D={ev_delta:+.3f}  MaxDD={stats['max_dd_r']:.2f}R  {'OK' if meets else '--'}")

        if not combo_df.empty:
            for k, v in params.items():
                combo_df[f'param_{k}'] = v
            all_trades.append(combo_df)

    # ── Save ──────────────────────────────────────────────────────────────────
    grid_df = pd.DataFrame(grid_rows).sort_values('ev', ascending=False)
    grid_df.to_csv(GRID_CSV, index=False)
    log.info(f"\nGrid  -> {GRID_CSV}")

    if all_trades:
        all_df = pd.concat(all_trades, ignore_index=True)
        all_df.to_csv(TRADES_CSV, index=False)
        log.info(f"Trades -> {TRADES_CSV} ({len(all_df)} rows)")

    # ── Print ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 75)
    print(f"LIQUIDITY-SWEEP v2 D1 GRID | {days}d | 7 symbols")
    print(f"Baseline EV: {baseline_ev:.3f}R  |  Acceptance: "
          f"WR>={ACCEPTANCE['min_wr']:.0%} EV>{ACCEPTANCE['min_ev']}R "
          f"n>={ACCEPTANCE['min_signals']} MaxDD<{ACCEPTANCE['max_dd']}R")
    print("=" * 75)
    cols  = ['vol_spike_mult', 'min_level_dist_atr', 'pivot_bars',
             'total_signals', 'win_rate', 'avg_r', 'ev', 'ev_vs_baseline', 'max_dd_r', 'meets_criteria']
    avail = [c for c in cols if c in grid_df.columns]
    print(grid_df[avail].head(10).to_string(index=False))
    print("=" * 75)

    accepted = grid_df[grid_df['meets_criteria'] == True]
    if not accepted.empty:
        best = accepted.iloc[0]
        print(f"\nACCEPTED (D1):")
        print(f"  vol_spike_mult     = {best['vol_spike_mult']}")
        print(f"  min_level_dist_atr = {best['min_level_dist_atr']}")
        print(f"  pivot_bars         = {int(best['pivot_bars'])}")
        print(f"  EV={best['ev']:.3f}R  WR={best['win_rate']:.1%}  "
              f"n={int(best['total_signals'])}  MaxDD={best['max_dd_r']:.2f}R")
        print(f"\n  These are D1-only params. Do NOT copy to liquidity_sweep.py")
        print(f"  (H1 and D1 use the same brain but different optimal params)")
        print(f"  Store D1 params in a separate config or D1-specific constants block.")
        print(f"\n  Next steps:")
        print(f"    1. Compare H1 and D1 results — which has higher EV?")
        print(f"    2. Consider running both timeframes in parallel (different signals)")
        print(f"    3. Paper trade D1 for 2 weeks (fewer signals — needs more time)")
    else:
        best_row = grid_df.iloc[0]
        print(f"\nNo D1 config meets criteria.")
        print(f"Best: vol={best_row['vol_spike_mult']} dist={best_row['min_level_dist_atr']} "
              f"pivot={int(best_row['pivot_bars'])} | EV={best_row['ev']:.3f} "
              f"WR={best_row['win_rate']:.1%} n={int(best_row['total_signals'])} "
              f"MaxDD={best_row['max_dd_r']:.2f}R")

        if best_row['total_signals'] == 0:
            print("\n  ZERO SIGNALS — D1-specific likely causes:")
            print("  1. Vol data missing for Indian equities: set vol_spike_mult=0.0")
            print("  2. ATR scale: Indian stocks priced in INR (₹50-₹3000) — check ATR is not 0")
            print("  3. Level dist too strict for D1 daily ranges: try min_level_dist_atr=0.30")
            print("  4. Check if load_ohlcv returns volume column for .NS symbols")
        elif best_row['total_signals'] < ACCEPTANCE['min_signals']:
            print(f"  Too few signals ({int(best_row['total_signals'])} < {ACCEPTANCE['min_signals']}): "
                  f"try vol_spike_mult=0.0 or min_level_dist_atr=0.30")
        if best_row['max_dd_r'] >= ACCEPTANCE['max_dd']:
            print("  MaxDD too high: increase vol_spike_mult or min_level_dist_atr")

    # Extended breakdown for best combo
    if not grid_df.empty:
        best_p = {
            'vol_spike_mult':     float(grid_df.iloc[0]['vol_spike_mult']),
            'min_level_dist_atr': float(grid_df.iloc[0]['min_level_dist_atr']),
            'pivot_bars':         int(grid_df.iloc[0]['pivot_bars']),
        }
        print(f"\nExtended breakdown for best D1 {best_p}:")
        best_df = run_combo(best_p, data_cache, indicator_cache)
        _extended_breakdown(best_df)

    print(f"\nD1 NEXT STEPS:")
    print(f"  Run this script first: python -X utf8 -m market_agent.runner.run_liquidity_sweep_d1_grid")
    print(f"  H1 grid (separate):    python -X utf8 -m market_agent.runner.run_liquidity_sweep_grid")
    print(f"  If D1 accepted → paper trade 2 weeks before live (fewer signals = more days needed)")
    print(f"  H1 and D1 params are INDEPENDENT — do not mix them")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=500,
                        help='Lookback days (default: 500 = full ~2yr history)')
    args = parser.parse_args()
    main(days=args.days)