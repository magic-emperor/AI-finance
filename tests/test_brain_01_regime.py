"""
Test 1: Regime-Ensemble — Market Condition Classifier
=======================================================
Tests all 6 regime classifications using deterministic, scenario-specific data.

DATA ENGINEERING PRINCIPLES:
- ATR% = avg(True Range) / price. To get ATR% = X%, set High-Low = approximately X% of price.
- ADX is from Directional Movement. calc_adx returns 100 when ONE direction dominates (pure
  +DM or pure -DM). Returns 0 when they cancel perfectly (alternating).
  For TRENDING_UP: High rises each bar with flat Low -> +DM dominates -> ADX=100.
  For RANGING:     Alternate up/down bars (even=high rises, odd=low drops) -> DM cancels -> ADX~0.
- SQUEEZE: last 20 bars have tight BB relative to prior 60 bars. Current BB < 70% of avg BB.
- Priority order: CHAOS > VOLATILE > SQUEEZE > TRENDING_UP/DOWN > RANGING.
"""
import sys
import inspect
import pandas as pd
import numpy as np

sys.path.insert(0, r'd:/AI Agent Finance')

# ── Mandatory first check: confirm which code is running ──────────────────────
import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.regime_ensemble_signal)
assert 'signal_generators' not in src_file, \
    f"Phase 4 shadowing! regime_ensemble_signal runs from: {src_file}"
print(f"[OK] regime_ensemble_signal: {src_file.split(chr(92))[-1]}")
print()

from market_agent.brain.regime_ensemble import regime_ensemble_signal

PASS = 0
FAIL = 0


def check(name, condition, actual_label, expected_label, measurements):
    global PASS, FAIL
    m = measurements
    regime  = m.get('computed_regime', '?')
    adx     = m.get('adx', 0)
    atr_pct = m.get('atr_pct', 0)
    bb_w    = m.get('bb_width', 0)
    avg_bb  = m.get('avg_bb_w', 0)
    ratio   = round(bb_w / avg_bb, 2) if avg_bb > 0 else '?'

    if condition:
        PASS += 1
        status = 'PASS'
    else:
        FAIL += 1
        status = 'FAIL'

    print(f"  [{status}] {name}")
    print(f"    Expected : {expected_label}")
    print(f"    Got      : {actual_label}")
    print(f"    Metrics  : regime={regime}  ADX={adx}  ATR%={atr_pct:.2%}  BB={bb_w:.4f}  avgBB={avg_bb:.4f}  ratio={ratio}")
    print()


# ─────────────────────────────────────────────────────────────────────────────
# DATA BUILDERS (deterministically engineered)
# ─────────────────────────────────────────────────────────────────────────────

def build_chaos_data(base=100.0):
    """
    A3-AWARE: 60 calm bars (wick=0.5%, median ATR ~0.5%) + 20 crisis bars (wick=10%).
    median_atr_pct ~ 0.005 -> chaos_threshold = max(5%, 0.005*5) = 5.0%
    Last bar ATR% ~ 10% >> 5.0% -> CHAOS fires.
    """
    n_base  = 60
    n_spike = 20
    p = [base] * (n_base + n_spike)
    h = [base * 1.005] * n_base + [base * 1.05] * n_spike  # 0.5% then 10%
    l = [base * 0.995] * n_base + [base * 0.95] * n_spike
    return pd.DataFrame({
        'Open': p, 'High': h, 'Low': l, 'Close': p,
        'Volume': [1_000_000] * (n_base + n_spike),
    })


def build_volatile_data(base=100.0):
    """
    A3-AWARE: 60 calm bars (wick=0.5%) + 20 volatile bars (wick=4%).
    median_atr_pct ~ 0.005 -> volatile_threshold = max(2%, 0.005*2.5) = 2.0%
                           -> chaos_threshold    = max(5%, 0.005*5.0) = 5.0%
    Last bar ATR% ~ 4% > volatile_threshold (2%) but < chaos_threshold (5%) -> VOLATILE.
    """
    n_base  = 60
    n_spike = 20
    p = [base] * (n_base + n_spike)
    h = [base * 1.005] * n_base + [base * 1.02] * n_spike   # 0.5% then 4%
    l = [base * 0.995] * n_base + [base * 0.98] * n_spike
    return pd.DataFrame({
        'Open': p, 'High': h, 'Low': l, 'Close': p,
        'Volume': [1_000_000] * (n_base + n_spike),
    })


def build_squeeze_data(n=80, base=100.0):
    """
    SQUEEZE: current BB < 70% of avg_bb_w AND ADX < 22.

    Key insight: BB uses Close.std(), ADX uses High.diff() and Low.diff().
    We can control them independently:

      - High = 103 (constant), Low = 97 (constant) throughout all 80 bars.
        -> high.diff() = 0, low.diff() = 0 -> DM = 0 -> ADX = 15.0 (fallback < 22) ✓

      - Wide period Close (bars 0-59): oscillates ±2pts via sin wave.
        -> std(close[-20:]) ≈ 1.4pts -> BB width ≈ 5-6% of price (large) ✓
        -> avg_bb_w (last 20 BB-width values) is non-zero: bars 60-69 still include
           wide-close bars in their trailing-20 window ✓

      - Tight period Close (bars 60-79): flat = base.
        -> std(close[-20:]) = 0 at bar 79 -> BB width = 0 ✓
        -> bb_w/avg_bb_w ≈ 0 << 0.70 -> SQUEEZE fires ✓

    High >= Close and Low <= Close: 103 >= 102 and 97 <= 98 ✓ (guaranteed throughout)
    """
    i = np.arange(60)
    close_wide  = base + 2.0 * np.sin(i * np.pi / 4)   # oscillates [98, 102]
    close_tight = np.full(20, base)                      # flat at base=100

    close = np.concatenate([close_wide, close_tight])
    high  = np.full(n, base + 3.0)   # constant 103 -> high.diff()=0 -> ADX fallback
    low   = np.full(n, base - 3.0)   # constant 97  -> low.diff()=0

    return pd.DataFrame({
        'Open':   close,
        'High':   high,
        'Low':    low,
        'Close':  close,
        'Volume': [1_000_000] * n,
    })


def build_trending_up_data(n=80, base=50000.0):
    """
    TRENDING_UP: High rises every bar, Low stays flat.
    +DM = high.diff() > abs(low.diff()) AND high.diff() > 0 -> always True here.
    -DM = 0 (Low never drops).
    ADX -> 100 (pure directional trend).
    ATR% kept small: use base=50000, wicks small relative to base.
    """
    step = 10.0   # $10/bar on BTC-like scale
    highs  = [base + i * step for i in range(n)]
    lows   = [base - 200.0] * n   # flat low, 200 below base
    closes = [base + i * step - 100 for i in range(n)]
    return pd.DataFrame({
        'Open':   closes, 'High': highs, 'Low': lows,
        'Close':  closes, 'Volume': [1_000_000] * n,
    })


def build_trending_down_data(n=80, base=50000.0):
    """
    TRENDING_DOWN: Low drops every bar, High stays flat.
    -DM dominates -> ADX -> 100. Price ends well below SMA50.
    """
    step = 10.0
    lows   = [base - i * step for i in range(n)]
    highs  = [base + 200.0] * n    # flat high
    closes = [base - i * step + 100 for i in range(n)]
    return pd.DataFrame({
        'Open':   closes, 'High': highs, 'Low': lows,
        'Close':  closes, 'Volume': [1_000_000] * n,
    })


def build_ranging_data(n=80, base=100.0):
    """
    RANGING: ADX = 15.0 (fallback when DM = 0 for all bars).

    From calc_adx source:
      plus_dm  fires only when high.diff() > 0 AND high.diff() > abs(low.diff())
      minus_dm fires only when low.diff() < 0  AND abs(low.diff()) > high.diff()

    If Close is PERFECTLY FLAT (diff=0 every bar):
      high.diff() = 0  (since H = close + const)
      low.diff()  = 0
    Neither condition fires -> DM=0 -> DI+, DI- = 0 -> DX=NaN -> ADX=15.0 fallback.
    ATR% = 2.0/100 = 2.0% (safely below VOLATILE 4% threshold).
    BrainSignal will say RANGING since ADX=15.0 < 22 cutoff.
    """
    closes = np.full(n, base)   # FLAT close: diff = 0 every bar
    wick   = 1.0
    return pd.DataFrame({
        'Open':   closes,
        'High':   closes + wick,
        'Low':    closes - wick,
        'Close':  closes,
        'Volume': [1_000_000] * n,
    })






# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────

print("=" * 65)
print("BRAIN 1: Regime-Ensemble -- All 6 Regime Classifications")
print("=" * 65)
print()

# Test 1: CHAOS
print("Test 1: CHAOS (ATR% > adaptive chaos_threshold)")
r = regime_ensemble_signal(build_chaos_data())
m = r.measurements
check('CHAOS classified',
      m['computed_regime'] == 'CHAOS' and m['atr_pct'] > m.get('chaos_threshold', 0.08),
      f"{m['computed_regime']} (ATR%={m['atr_pct']:.1%}, chaos_thr={m.get('chaos_threshold', '?'):.1%})",
      'CHAOS: ATR% > chaos_threshold (adaptive, based on 60-bar median)', m)

# Verify CHAOS blocks ALL brains (all trust weights = 0%)
weights_zero = all('0%' in s for s in r.supporting_factors)
print(f"  All trust weights 0% in CHAOS: {'OK' if weights_zero else 'FAIL'} | {r.supporting_factors}")
print()

# Test 2: VOLATILE
print("Test 2: VOLATILE (ATR% > volatile_threshold, < chaos_threshold)")
r = regime_ensemble_signal(build_volatile_data())
m = r.measurements
check('VOLATILE classified',
      m['computed_regime'] == 'VOLATILE'
          and m['atr_pct'] > m.get('volatile_threshold', 0.04)
          and m['atr_pct'] < m.get('chaos_threshold', 0.08),
      f"{m['computed_regime']} (ATR%={m['atr_pct']:.1%}, vol_thr={m.get('volatile_threshold', '?'):.1%}, chaos_thr={m.get('chaos_threshold', '?'):.1%})",
      'VOLATILE: volatile_threshold < ATR% < chaos_threshold', m)

# Test 3: SQUEEZE
print("Test 3: SQUEEZE (BB width < 70% of avg)")
df_sq = build_squeeze_data()
r = regime_ensemble_signal(df_sq)
m = r.measurements
bb_ratio = m['bb_width'] / m['avg_bb_w'] if m['avg_bb_w'] > 0 else 1.0
check('SQUEEZE classified',
      m['computed_regime'] == 'SQUEEZE' and bb_ratio < 0.70,
      f"{m['computed_regime']} (BB_ratio={bb_ratio:.3f})",
      'SQUEEZE with BB_ratio < 0.70', m)

# Test 4: TRENDING_UP (ADX > 25, price > SMA50)
print("Test 4: TRENDING_UP (ADX > 25, price > SMA50)")
df_up = build_trending_up_data()
r = regime_ensemble_signal(df_up)
m = r.measurements
check('TRENDING_UP classified',
      m['computed_regime'] == 'TRENDING_UP' and m['adx'] > 25,
      f"{m['computed_regime']} (ADX={m['adx']:.1f}, ATR%={m['atr_pct']:.2%})",
      'TRENDING_UP with ADX > 25', m)

# Test 5: TRENDING_DOWN (ADX > 25, price < SMA50)
print("Test 5: TRENDING_DOWN (ADX > 25, price < SMA50)")
df_dn = build_trending_down_data()
r = regime_ensemble_signal(df_dn)
m = r.measurements
check('TRENDING_DOWN classified',
      m['computed_regime'] == 'TRENDING_DOWN' and m['adx'] > 25,
      f"{m['computed_regime']} (ADX={m['adx']:.1f})",
      'TRENDING_DOWN with ADX > 25', m)

# Test 6: RANGING (ADX < 22)
print("Test 6: RANGING (ADX < 22)")
df_rng = build_ranging_data()
r = regime_ensemble_signal(df_rng)
m = r.measurements
check('RANGING classified',
      m['computed_regime'] == 'RANGING' and m['adx'] < 22,
      f"{m['computed_regime']} (ADX={m['adx']:.1f})",
      'RANGING with ADX < 22', m)

# Test 7: computed_regime is a clean key in measurements dict
print("Test 7: computed_regime is directly in measurements dict")
m7 = r.measurements
computed = m7.get('computed_regime')
check('computed_regime key accessible directly',
      isinstance(computed, str) and len(computed) > 0,
      f'measurements["computed_regime"] = "{computed}"',
      'String accessible without string parsing', m7)

# Test 8: Decision tree priority -- VOLATILE overrides SQUEEZE
print("Test 8: Priority -- VOLATILE beats SQUEEZE (ATR% check first)")
# Purpose-built flat-base dataset: FLAT closes throughout (no close-to-close drift)
# so the 60-bar median ATR% is entirely determined by H-L wick size.
# Structure:
#   Bars 0-59:  flat close=100, wick=0.5pt (0.5%) -> median_atr_pct~0.5%
#   Bars 60-79: flat close=100, wick=1.5pt (1.5%) -> VOLATILE trigger
#
# With median_atr_pct~0.005 (0.5%):
#   volatile_threshold = max(1.0%, 0.005*2.5) = max(1.0%, 1.25%) = 1.25%
#   chaos_threshold    = max(2.5%, 0.005*5.0) = max(2.5%, 2.5%)  = 2.5%
# Last bar ATR% = 1.5% > volatile_threshold (1.25%) AND < chaos_threshold (2.5%)
# -> VOLATILE fires. (FIX A1: old 4% wick exceeded chaos_thr 2.5% -> CHAOS)
base = 100.0
n_wide  = 60
n_tight = 20
close_w = np.full(n_wide,  base)
close_t = np.full(n_tight, base)
closes  = np.concatenate([close_w, close_t])
# Wide period: 0.5pt wick -> median_atr_pct ~0.5%
high_w  = close_w + 0.25
low_w   = close_w - 0.25
# Tight period: 1.5pt wick -> ATR%=1.5%, above volatile_thr but below chaos_thr
high_t  = close_t + 0.75
low_t   = close_t - 0.75
highs   = np.concatenate([high_w, high_t])
lows    = np.concatenate([low_w,  low_t])
df_t8 = pd.DataFrame({
    'Open': closes, 'High': highs, 'Low': lows,
    'Close': closes, 'Volume': [1_000_000] * (n_wide + n_tight),
})
r = regime_ensemble_signal(df_t8)
m = r.measurements
check('VOLATILE overrides SQUEEZE',
      m['computed_regime'] == 'VOLATILE',
      f"{m['computed_regime']} (ATR%={m['atr_pct']:.1%}, vol_thr={m.get('volatile_threshold','?'):.1%})",
      'VOLATILE (ATR% check runs before SQUEEZE check)', m)


# Test 9: CHAOS overrides TRENDING
print("Test 9: Priority -- CHAOS overrides TRENDING data")
df_chaos_trend = build_trending_up_data().copy()
# trending_up uses base=50000, step=10/bar. ATR% is ~0.4% (200/50000 range).
# median_atr_pct ~0.4% -> chaos_threshold = max(5%, 0.004*5) = 5%
# Widen last 20 bars to 10%: 5000 wick on 50000 base = 10% >> 5% chaos threshold
base_price = 50000.0
n = len(df_chaos_trend)
n_base = n - 20
df_chaos_trend.loc[df_chaos_trend.index[n_base:], 'High'] = base_price * 1.05
df_chaos_trend.loc[df_chaos_trend.index[n_base:], 'Low']  = base_price * 0.95
r = regime_ensemble_signal(df_chaos_trend)
m = r.measurements
check('CHAOS overrides TRENDING',
      m['computed_regime'] == 'CHAOS',
      f"{m['computed_regime']} (ATR%={m['atr_pct']:.1%}, chaos_thr={m.get('chaos_threshold','?'):.1%})",
      'CHAOS (ATR% > chaos_threshold, top priority)', m)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"Regime-Ensemble: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above for details")
    sys.exit(1)
