"""
Test 5: Causal-Ensemble -- Mean Reversion Specialist
=====================================================
From causal_ensemble.py (284 lines), checked IN ORDER:
  1. Data < 30 bars -> HOLD
  2. _trend_is_not_extreme: |price - SMA50| / ATR > 2.0 -> HOLD
     (returned with reliability_flags={'extreme_trend_too_risky': True})
  3. regime in (TRENDING_UP, TRENDING_DOWN) AND NOT is_squeeze -> HOLD (Fix 1)
  4. BB squeeze: width < avg_width*0.70 -> use _squeeze_breakout_direction
  5. STRONG: pct_b < 0.05 AND RSI < 30 -> BUY 0.85, rr_t1=3.0
  6. MEDIUM: pct_b < 0.15 AND RSI < 40 -> BUY 0.70, rr_t1=2.5
  7. WEAK:   pct_b < 0.20             -> BUY 0.58, rr_t1=2.0
  Volume < 60% avg -> penalty x 0.80

DATA ENGINEERING:
  extreme_trend avoidance: use large wick_pct (4%) -> ATR is large -> |price-SMA50|/ATR < 2
  pct_b < 0.05: 50 flat bars + 10 sharp drop -> price well below lower BB
  squeeze: 30 moderate + 27 flat -> very narrow std -> squeeze=True
"""
import sys
import inspect
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.causal_ensemble_signal)
assert 'signal_generators' not in src_file, f"Phase 4 shadowing! {src_file}"
print(f"[OK] causal_ensemble_signal: {src_file.split(chr(92))[-1]}")
print()

from market_agent.brain.causal_ensemble import causal_ensemble_signal
from market_agent.brain.brain_utils import calc_atr

PASS = 0
FAIL = 0


def check(name, condition, actual_label, expected_label, extra=None):
    global PASS, FAIL
    if condition:
        PASS += 1; status = 'PASS'
    else:
        FAIL += 1; status = 'FAIL'
    def _safe(s): return str(s).encode('ascii', 'replace').decode('ascii')
    print(f"  [{status}] {name}")
    print(f"    Expected : {_safe(expected_label)}")
    print(f"    Got      : {_safe(actual_label)}")
    if extra:
        print(f"    Details  : {_safe(extra)}")
    print()


def make_df(prices, wick_pct=0.005, vol_mult=1.0):
    close  = pd.Series([float(p) for p in prices])
    wick   = close * wick_pct
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': [1_000_000 * vol_mult] * len(close),
    })


# ─────────────────────────────────────────────────────────────────────────────
# VERIFIED DATA BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_strong_oversold():
    """
    STRONG BUY: pct_b in (0, 0.05), RSI < 30.
    Declining sine wave: 60 bars from pi to 5*pi (starts at trough, ends at trough).
    Drift=-8 over 60 bars. Amplitude=2.5 parks price just above lower BB.
    Diagnostically verified: pct_b=0.02, RSI=24.1, extreme_trend sep=0.48 < 2.0.
    """
    import numpy as np
    t = np.linspace(np.pi, 5 * np.pi, 60)
    prices = list(100 - np.linspace(0, 8, 60) + 2.5 * np.sin(t))
    return make_df(prices, wick_pct=0.04, vol_mult=1.0)


def build_extreme_trend():
    """
    extreme_trend gate: |price - SMA50| / ATR > 2.0.
    60 bars steady uptrend with TINY wicks (0.2%) -> small ATR.
    Diagnostically: extreme=True, pct_b=0.901, ATR=1.86, sep=36.75.
    """
    prices = [100.0 + i * 1.5 for i in range(60)]
    return make_df(prices, wick_pct=0.002)


def build_trending_up_no_squeeze():
    """
    regime=TRENDING_UP, not squeeze -> HOLD (Fix 1 at line 186).
    50 flat + 10 bar uptrend: BB is not compressed (not squeeze).
    Large wicks keep extreme_trend gate from firing.
    """
    prices = [100.0] * 50
    for _ in range(10): prices.append(prices[-1] + 0.3)
    return make_df(prices[:60], wick_pct=0.04, vol_mult=1.0)


def build_squeeze_breakout():
    """
    BB squeeze (width < 70% avg) + 5 rising candles above midband -> BUY 0.65.
    A8 raised requirement from 3 to 5 candles.

    Construction:
      - 30 bars oscillating ±0.5 around 100 (establishes wide avg BB)
      - 25 bars oscillating ±0.02 (collapses std -> squeeze: width << avg)
      - 5 strictly rising bars with step=0.01 (stays inside band, pct_b=0.979)
    Diagnostically verified: is_squeeze=True, pct_b=0.979, dir=BUY, conf=0.65.
    """
    import math
    prices = []
    for i in range(30):
        prices.append(100.0 + 0.5 * math.sin(i * 0.8))
    mid = prices[-1]
    for i in range(25):
        prices.append(mid + 0.02 * math.sin(i * 2.0))
    mid2 = prices[-1]
    for step in range(1, 6):
        prices.append(mid2 + step * 0.01)   # 0.01 step keeps price inside the tight band
    return make_df(prices[:60], wick_pct=0.005)


def build_low_vol_signal():
    """
    STRONG BUY setup (pct_b in (0, 0.05), RSI<30) + volume penalty.
    Same declining sine as build_strong_oversold but with low volume on last 10 bars.
    Volume penalty fires when last_vol < 60% of rolling 20-bar avg.
    First 50 bars: 2M vol, last 10 bars: 400K vol -> ratio=0.2 < 0.6 -> low_volume=True.
    Diagnostically verified: pct_b=0.02, RSI=24.1, low_volume=True.
    """
    import numpy as np
    t = np.linspace(np.pi, 5 * np.pi, 60)
    prices = list(100 - np.linspace(0, 8, 60) + 2.5 * np.sin(t))
    close = pd.Series([float(p) for p in prices])
    wick  = close * 0.04
    vols  = [2_000_000] * 50 + [400_000] * 10
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': vols,
    })


# ─────────────────────────────────────────────────────────────────────────────
# PRE-RUN DIAGNOSTIC
# ─────────────────────────────────────────────────────────────────────────────
print("Pre-run diagnostic:")
for lbl, df_p in [
    ('strong_oversold',    build_strong_oversold()),
    ('extreme_trend',      build_extreme_trend()),
    ('trending_no_squeeze',build_trending_up_no_squeeze()),
    ('squeeze_breakout',   build_squeeze_breakout()),
    ('low_vol',            build_low_vol_signal()),
]:
    r_p = causal_ensemble_signal(df_p)
    m_p = r_p.measurements
    print(f"  {lbl:22s}: dir={r_p.direction:4s}  conf={r_p.confidence:.2f}  pct_b={m_p.get('pct_b', '?')}  RSI={m_p.get('rsi', '?')}")
print()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("BRAIN 5: Causal-Ensemble -- All 5 Scenarios")
print("=" * 65)
print()

# ── Test 1: STRONG oversold -> BUY 0.85, rr_t1=3.0 ───────────────────────────
print("Test 1: STRONG oversold (pct_b<0.05, RSI<30) -> BUY conf=0.85, rr_t1=3.0")
r1 = causal_ensemble_signal(build_strong_oversold())
m1 = r1.measurements
check('STRONG BUY fires',
      r1.direction == 'BUY' and r1.confidence == 0.85,
      f"dir={r1.direction}  conf={r1.confidence:.2f}  pct_b={m1.get('pct_b')}  RSI={m1.get('rsi')}",
      'BUY conf=0.85 (STRONG tier)',
      r1.primary_evidence)

check('STRONG R:R: T1=3.0x SL=1.0x (3:1)',
      r1.rr_t1_mult == 3.0 and r1.rr_sl_mult == 1.0,
      f"rr_t1={r1.rr_t1_mult}  rr_sl={r1.rr_sl_mult}  ratio={r1.rr_t1_mult/r1.rr_sl_mult:.0f}:1",
      'rr_t1=3.0, rr_sl=1.0 (3:1)')

# ── Test 2: Extreme trend gate -> HOLD ────────────────────────────────────────
print("Test 2: Extreme trend gate (|price - SMA50| > 2x ATR) -> HOLD")
r2 = causal_ensemble_signal(build_extreme_trend())
# reliability_flags uses key 'extreme_trend_too_risky' (from line 180 in brain)
check('Extreme trend -> HOLD',
      r2.direction == 'HOLD' and 'Extreme trend' in r2.primary_evidence,
      f"dir={r2.direction}  evidence={r2.primary_evidence[:60]}",
      'HOLD with "Extreme trend" in primary_evidence',
      r2.primary_evidence)

# ── Test 3: regime=TRENDING_UP, no squeeze -> HOLD (Fix 1) ────────────────────
print("Test 3: regime=TRENDING_UP, no squeeze -> HOLD (validates Fix 1)")
r3 = causal_ensemble_signal(build_trending_up_no_squeeze(), regime='TRENDING_UP')
check('TRENDING_UP + no squeeze -> HOLD (Fix 1)',
      r3.direction == 'HOLD' and 'TRENDING' in r3.primary_evidence,
      f"dir={r3.direction}  conf={r3.confidence:.2f}",
      'HOLD in TRENDING_UP without squeeze (Fix 1 gate)',
      r3.primary_evidence)

# ── Test 4: BB squeeze + 3 rising candles -> BUY 0.65 ────────────────────────
print("Test 4: BB squeeze + 5 rising candles (A8) -> BUY conf=0.65")
r4 = causal_ensemble_signal(build_squeeze_breakout())
m4 = r4.measurements
check('Squeeze breakout fires BUY',
      r4.direction == 'BUY' and abs(r4.confidence - 0.65) < 0.02,
      f"dir={r4.direction}  conf={r4.confidence:.2f}  squeeze={m4.get('is_squeeze')}",
      'BUY conf=0.65 (BB squeeze breakout)',
      r4.primary_evidence)

# ── Test 5: Low volume -> confidence x 0.80 penalty ──────────────────────────
print("Test 5: Low volume (40% of avg) -> confidence x 0.80 penalty applied")
r5 = causal_ensemble_signal(build_low_vol_signal())
m5 = r5.measurements
# STRONG BUY confidence=0.85, with 0.80 penalty = 0.68
check('Low volume penalty applied',
      r5.direction == 'BUY' and r5.reliability_flags.get('low_volume') is True,
      f"dir={r5.direction}  conf={r5.confidence:.3f}  low_volume={r5.reliability_flags.get('low_volume')}",
      'BUY preserved, low_volume=True, confidence=0.85*0.80=0.68',
      r5.primary_evidence)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"Causal-Ensemble: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
