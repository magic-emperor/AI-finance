"""
Test 7: Liquidity-Sweep -- Stop-Hunt Reversal Detector
=======================================================
This test enforces a small set of invariants for Liquidity-Sweep v5:
  - Builders must satisfy the brain's minimum bars gate (_MIN_HIST_BARS=100).
  - A clean bullish sweep + institutional (but not panic) volume can produce BUY.
  - CHAOS regime must return HOLD.
  - No sweep detected must return HOLD.

DATA ENGINEERING:
  Swing lows via pivot_bars=3: a bar is a pivot low if its Low <= all 3 bars before and after.
  Sweep: last bar Low < swing_low by 0.1-1.5x ATR, Close > swing_low, Close in upper range half.
  Build: 60+ bars establishing a clear pivot low, then 1 final sweep candle.
"""
import sys
import inspect
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.liquidity_sweep_signal)
assert 'signal_generators' not in src_file, f"Phase 4 shadowing! {src_file}"
print(f"[OK] liquidity_sweep_signal: {src_file.split(chr(92))[-1]}")
print()

from market_agent.brain.liquidity_sweep import liquidity_sweep_signal

PASS = 0
FAIL = 0


def check(name, condition, actual_label, expected_label, extra=None):
    global PASS, FAIL
    if condition:
        PASS += 1; status = 'PASS'
    else:
        FAIL += 1; status = 'FAIL'
    print(f"  [{status}] {name}")
    print(f"    Expected : {expected_label}")
    print(f"    Got      : {actual_label}")
    if extra:
        print(f"    Details  : {extra}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# DATA BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_sweep_df(base_price=100.0, atr_natural=1.0, n_base=120):
    """
    Build a 'clean' oscillating base series to create real pivot highs/lows via
    _find_swing_levels (pivot_bars=3 means the pivot bar is lower/higher than 3 on each side).

    Strategy: use a sine wave with clear peaks and troughs, then append a sweep candle.
    The sine creates definite pivot lows (troughs) that _find_swing_levels will detect.
    The last bar will be crafted to sweep below the most recent pivot low.
    """
    t = np.linspace(0, 4 * np.pi, n_base)
    prices_close = base_price + 3.0 * np.sin(t)  # oscillates between 97 and 103
    prices_close = list(prices_close)

    # Natural wick: +/- atr_natural on each bar
    df = pd.DataFrame({
        'Open':   prices_close,
        'High':   [p + atr_natural for p in prices_close],
        'Low':    [p - atr_natural for p in prices_close],
        'Close':  prices_close,
        'Volume': [1_000_000.0] * n_base,
    })
    return df, prices_close


def build_bullish_sweep_confirmed():
    """
    BUY: wick below swing low + close above + vol spike + RSI extreme.
    Expected: dir=BUY, conf >= 0.78 (base 0.60 + vol 0.10 + rsi 0.08)

    Steps:
    1. 80 bars sine wave to build real pivot lows. Swing low at ~97 (bottom of sine).
    2. Final sweep bar: Low = pivot_low - 0.5*ATR (sweep depth), Close = pivot_low + 1.0 (above),
       High = pivot_low + 1.5, vol = 3x avg (spike), RSI on declining bars = very low.
    """
    # Build a deterministic structure that v5 pivot logic (pivot_bars=2) will detect
    n = 120
    # Keep RSI non-extreme by introducing mild, alternating closes.
    base_close = [100.0] * n
    for k in range(n - 30, n - 1):
        base_close[k] = 100.0 + (0.2 if (k % 2 == 0) else -0.2)
    df = pd.DataFrame({
        "Open":   base_close,
        "High":   [101.0] * n,
        "Low":    [99.0] * n,
        "Close":  base_close,
        "Volume": [1_000_000.0] * n,
    })

    # Create a pivot low ~10 bars ago (level_age ~= 10 >= min 5)
    pivot_i = n - 1 - 10
    pivot_low = 96.0
    df.loc[pivot_i, "Low"]   = pivot_low
    df.loc[pivot_i, "Close"] = 99.8
    df.loc[pivot_i, "Open"]  = 100.0
    df.loc[pivot_i, "High"]  = 100.5

    # Ensure surrounding bars have higher lows so pivot is detected
    for j in [pivot_i - 2, pivot_i - 1, pivot_i + 1, pivot_i + 2]:
        df.loc[j, "Low"]   = 97.0
        df.loc[j, "Close"] = 99.0
        df.loc[j, "Open"]  = 99.0
        df.loc[j, "High"]  = 100.0

    # Final sweep candle (current bar): wick below pivot_low, close above it.
    # ATR ~2.69 -> _MIN_WICK_SIZE_ATR=0.30 -> min below_by = 0.30*2.69 = 0.808.
    # Use 0.9 to clear the boundary with margin.
    sweep_low   = pivot_low - 0.9
    sweep_close = pivot_low + 1.2   # closes above swept level
    sweep_high  = pivot_low + 2.0
    df.loc[n - 1, "Low"]    = sweep_low
    df.loc[n - 1, "Close"]  = sweep_close
    df.loc[n - 1, "Open"]   = pivot_low + 0.5
    df.loc[n - 1, "High"]   = sweep_high
    # IMPORTANT: keep below panic ceiling (3.0x); v5 blocks ratio >= 3.0
    df.loc[n - 1, "Volume"] = 2_700_000.0

    return df.reset_index(drop=True)


def build_ranging_regime():
    """regime=CHAOS -> HOLD (brain must never trade CHAOS)."""
    df, _ = _make_sweep_df(n_base=120)
    return df


def build_no_sweep_detected():
    """
    No sweep: last bar has normal wick (no penetration of swing low).
    -> HOLD at conf=0.45 with 'No structural sweeps detected'.
    """
    df, _ = _make_sweep_df(n_base=120)
    # Last bar: completely normal candle (close at midband, no extreme wick)
    df.loc[df.index[-1], 'Close'] = 100.0
    df.loc[df.index[-1], 'Open']  = 100.0
    df.loc[df.index[-1], 'High']  = 101.0
    df.loc[df.index[-1], 'Low']   = 99.0
    return df


# ─────────────────────────────────────────────────────────────────────────────
# PRE-RUN DIAGNOSTIC
# ─────────────────────────────────────────────────────────────────────────────
print("Pre-run diagnostic:")
for lbl, df_p, regime_p in [
    ('bullish_sweep',   build_bullish_sweep_confirmed(), 'VOLATILE'),
    ('chaos_regime',    build_ranging_regime(),          'CHAOS'),
    ('no_sweep',        build_no_sweep_detected(),       'VOLATILE'),
]:
    r_p = liquidity_sweep_signal(df_p, symbol='BTC-USD', regime=regime_p)
    m_p = r_p.measurements
    print(f"  {lbl:20s}: dir={r_p.direction:4s}  conf={r_p.confidence:.3f}  RSI={m_p.get('rsi','?')}  evidence={r_p.primary_evidence[:50]}")
print()

# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("BRAIN 7: Liquidity-Sweep -- All 3 Core Scenarios")
print("=" * 65)
print()

# ── Test 1: Bullish sweep (wick below swing low, close above) -> BUY ──────────
print("Test 1: Bullish sweep + vol spike -> BUY, conf >= 0.65")
df1 = build_bullish_sweep_confirmed()
r1  = liquidity_sweep_signal(df1, symbol='BTC-USD', regime='VOLATILE')
m1  = r1.measurements
check('Bullish sweep detected -> BUY',
      r1.direction == 'BUY' and r1.confidence >= 0.65,
      f"dir={r1.direction}  conf={r1.confidence:.3f}  swept={m1.get('swept_level')}  vol={m1.get('vol_ratio')}",
      'BUY conf >= 0.65 (bullish sweep + vol spike confirmation)',
      r1.primary_evidence)

check('Minimum R:R >= 2.0 (structural target)',
      m1.get('rr_achieved', 0) >= 2.0,
      f"rr_achieved={m1.get('rr_achieved')}",
      'rr_achieved >= 2.0 (target at next structural level)')

# ── Test 2: RANGING regime -> HOLD ────────────────────────────────────────────
print("Test 2: regime=CHAOS -> HOLD (never trade CHAOS)")
df2 = build_ranging_regime()
r2  = liquidity_sweep_signal(df2, symbol='BTC-USD', regime='CHAOS')
check('CHAOS regime -> HOLD',
      r2.direction == 'HOLD' and 'chaos' in r2.primary_evidence.lower(),
      f"dir={r2.direction}  conf={r2.confidence:.2f}",
      'HOLD with CHAOS evidence',
      r2.primary_evidence)

# ── Test 3: No sweep found -> HOLD ────────────────────────────────────────────
print("Test 3: Normal candle (no wick through swing low) -> HOLD")
df3 = build_no_sweep_detected()
r3  = liquidity_sweep_signal(df3, symbol='BTC-USD', regime='VOLATILE')
check('No sweep -> HOLD',
      r3.direction == 'HOLD',
      f"dir={r3.direction}  conf={r3.confidence:.2f}",
      'HOLD (no structural sweeps detected)',
      r3.primary_evidence)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"Liquidity-Sweep: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
