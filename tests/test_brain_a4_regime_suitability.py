"""
Test A4: Regime-Ensemble -- regime_suitability='MEDIUM' for ADX 22-25
=====================================================================
A4 spec: ADX 22-25 (developing trend) -> regime_suitability='MEDIUM'
         ADX > 25 (confirmed trend)   -> regime_suitability='HIGH' (unchanged)
         All other regimes             -> regime_suitability='HIGH'

APPROACH: Monkeypatch calc_adx in regime_ensemble to return exact ADX values.
This tests the BRANCH LOGIC directly rather than trying to engineer
exact ADX values through data (ADX formula collapses to 100 when
only one DI direction fires -- making it impossible to land exactly in 22-25
via synthetic H/L data alone).
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.regime_ensemble as re_module
from market_agent.brain.regime_ensemble import regime_ensemble_signal

PASS = 0
FAIL = 0


def check(name, condition, actual, expected):
    global PASS, FAIL
    if condition:
        PASS += 1; status = 'PASS'
    else:
        FAIL += 1; status = 'FAIL'
    print(f"  [{status}] {name}")
    print(f"    Expected : {expected}")
    print(f"    Got      : {actual}")
    print()


def build_uptrend_hist(n=80, base=100.0):
    """Flat-close uptrend. ATR% ~1% (safe below volatile threshold)."""
    closes = np.array([base + i * 0.1 for i in range(n)])
    return pd.DataFrame({
        'Open': closes, 'High': closes + 0.5,
        'Low':  closes - 0.5, 'Close': closes,
        'Volume': [1_000_000] * n,
    })


def build_ranging_hist(n=80, base=100.0):
    """Flat close. ATR% ~1%. ADX real value ~15 (DM=0 fallback)."""
    closes = np.full(n, base)
    return pd.DataFrame({
        'Open': closes, 'High': closes + 0.5,
        'Low':  closes - 0.5, 'Close': closes,
        'Volume': [1_000_000] * n,
    })


print("=" * 65)
print("A4: Regime-Ensemble -- regime_suitability MEDIUM for ADX 22-25")
print("=" * 65)
print()

hist_up  = build_uptrend_hist()
hist_rng = build_ranging_hist()

# --- T1: ADX=23 -> TRENDING with MEDIUM suitability ---
print("T1: ADX=23 (patched) -> TRENDING with regime_suitability='MEDIUM'")
original_adx = re_module.calc_adx
re_module.calc_adx = lambda hist, period=14: 23.0   # patch: borderline
try:
    r1 = regime_ensemble_signal(hist_up)
    m1 = r1.measurements
    check('ADX=23 -> MEDIUM suitability',
          r1.regime_suitability == 'MEDIUM' and 'TRENDING' in m1.get('computed_regime', ''),
          f"regime_suitability={r1.regime_suitability!r}  regime={m1.get('computed_regime')}  conf={r1.confidence:.2f}",
          "regime_suitability='MEDIUM', regime=TRENDING_UP/DOWN, confidence=0.55")
    check('ADX=23 branch confidence=0.55',
          r1.confidence == 0.55,
          f"confidence={r1.confidence:.2f}",
          "confidence=0.55 (below strong trend 0.65+)")
finally:
    re_module.calc_adx = original_adx   # always restore

# --- T2: ADX=30 (strong confirmed) -> HIGH ---
print("T2: ADX=30 (patched) -> TRENDING with regime_suitability='HIGH'")
re_module.calc_adx = lambda hist, period=14: 30.0
try:
    r2 = regime_ensemble_signal(hist_up)
    m2 = r2.measurements
    check('ADX=30 -> HIGH suitability',
          r2.regime_suitability == 'HIGH' and 'TRENDING' in m2.get('computed_regime', ''),
          f"regime_suitability={r2.regime_suitability!r}  regime={m2.get('computed_regime')}  conf={r2.confidence:.2f}",
          "regime_suitability='HIGH' (ADX > 25 = confirmed trend)")
finally:
    re_module.calc_adx = original_adx

# --- T3: RANGING (unpatch, real ADX=15) -> HIGH ---
print("T3: RANGING (real ADX ~15) -> regime_suitability='HIGH'")
r3 = regime_ensemble_signal(hist_rng)
m3 = r3.measurements
check('RANGING -> HIGH suitability',
      m3.get('computed_regime') == 'RANGING' and r3.regime_suitability == 'HIGH',
      f"regime_suitability={r3.regime_suitability!r}  regime={m3.get('computed_regime')}  ADX={m3.get('adx')}",
      "regime_suitability='HIGH' for RANGING (A4 only touches TRENDING branch)")

# --- T4: ADX=24.9 (just below 25) -> MEDIUM (top of borderline range) ---
print("T4: ADX=24.9 (patched, top of borderline) -> MEDIUM")
re_module.calc_adx = lambda hist, period=14: 24.9
try:
    r4 = regime_ensemble_signal(hist_up)
    check('ADX=24.9 -> MEDIUM suitability',
          r4.regime_suitability == 'MEDIUM',
          f"regime_suitability={r4.regime_suitability!r}  ADX patch=24.9",
          "regime_suitability='MEDIUM' (24.9 is still in the 22-25 borderline band)")
finally:
    re_module.calc_adx = original_adx

# --- T5: ADX=22.1 (just above 22) -> MEDIUM (bottom of borderline range) ---
print("T5: ADX=22.1 (patched, bottom of borderline) -> MEDIUM")
re_module.calc_adx = lambda hist, period=14: 22.1
try:
    r5 = regime_ensemble_signal(hist_up)
    check('ADX=22.1 -> MEDIUM suitability',
          r5.regime_suitability == 'MEDIUM',
          f"regime_suitability={r5.regime_suitability!r}  ADX patch=22.1",
          "regime_suitability='MEDIUM' (22.1 is at the bottom of the borderline band)")
finally:
    re_module.calc_adx = original_adx

print("=" * 65)
print(f"A4 Tests: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
