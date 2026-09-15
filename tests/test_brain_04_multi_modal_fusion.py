"""
Test 4: Multi-Modal-Fusion -- RSI + MACD Momentum with Divergence Detection
===========================================================================
From multi_modal_fusion.py decision tree (checked in ORDER):
  1. bullish_divergence: price lower low + RSI higher low + RSI<40 -> BUY 0.85
  2. bearish_divergence: price higher high + RSI lower high + RSI>60 -> SELL 0.85
  3. RSI < 30 + MACD bullish -> BUY 0.72
  4. RSI > 70 + MACD bearish -> SELL 0.72
  5. MACD bullish + hist_slope>0 + RSI<60 -> BUY 0.55 (weak tier)
  vol guard: ATR/price > 1.2% -> high_vol_asset, limit=5%; else limit=3%
  R:R: T1=2.0x, SL=1.0x (2:1)

All data builders diagnostically verified before inclusion.
"""
import sys
import inspect
import numpy as np
import pandas as pd

sys.path.insert(0, r'd:/AI Agent Finance')

import market_agent.brain.signal_generators as sg
src_file = inspect.getfile(sg.multi_modal_fusion_signal)
assert 'signal_generators' not in src_file, f"Phase 4 shadowing! {src_file}"
print(f"[OK] multi_modal_fusion_signal: {src_file.split(chr(92))[-1]}")
print()

from market_agent.brain.multi_modal_fusion import multi_modal_fusion_signal
from market_agent.brain.brain_utils import calc_atr

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


def make_df(prices, wick_pct=0.001):
    close = pd.Series([float(p) for p in prices])
    wick  = close * wick_pct
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': [1_000_000] * len(close),
    })


# ─────────────────────────────────────────────────────────────────────────────
# VERIFIED DATA BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def build_bullish_divergence():
    """
    Bullish divergence: price makes lower low vs swing low in -15:-3 window;
    RSI at current bar HIGHER than RSI at that swing low; RSI < 40.
    Structure: upramp (base) -> sharp drop -> small bounce -> steep second drop
    -> slow drift to new lower low.
    Diagnostically verified: div=True, RSI_now=14.8, RSI_swing=6.4, price_lo=True.
    Divergence is checked BEFORE RSI<30 tier (lines 133 vs 139 in brain).
    """
    prices = [100.0]
    for _ in range(35): prices.append(prices[-1] + 0.1)    # base uptrend
    for _ in range(12): prices.append(prices[-1] - 0.95)   # sharp drop: RSI crashes (swing low in -15:-3)
    for _ in range(6):  prices.append(prices[-1] + 0.2)    # small bounce: RSI rises to ~25
    for _ in range(6):  prices.append(prices[-1] - 0.35)   # slow drift: new lower low, RSI > RSI_swing
    return make_df(prices[:60], wick_pct=0.001)


def build_rsi_oversold_macd_bullish():
    """
    RSI < 30 + MACD bullish -> BUY 0.72.
    40 bars strong decline (RSI ~15-23), then 20 bars very slow recovery.
    Diagnostically verified: RSI=23, dir=BUY, conf=0.72.
    """
    prices = [100.0]
    for _ in range(40): prices.append(prices[-1] - 0.6)
    for _ in range(20): prices.append(prices[-1] + 0.05)
    return make_df(prices, wick_pct=0.001)


def build_high_volatility():
    """
    ATR/price > 1.2% -> high_vol_asset -> limit=5%. Wicks at 3% -> ATR~5.9% > 5.0%.
    Diagnostically verified: ATR%=0.0596, guard fires, dir=HOLD.
    """
    prices = [100.0 + i * 0.1 for i in range(60)]
    close  = pd.Series(prices)
    wick   = close * 0.03   # 3% wicks -> ATR ~5.9% > 5% limit
    return pd.DataFrame({
        'Open': close, 'High': close + wick,
        'Low':  close - wick, 'Close': close,
        'Volume': [1_000_000] * 60,
    })


def build_macd_weak_bullish():
    """
    MACD bullish + hist_slope>0 + RSI<60 -> BUY 0.55.
    Sinusoidal + downward drift, phase=0.3pi.
    Diagnostically verified: RSI=56.0, hist_slope=0.0770, dir=BUY, conf=0.55.
    """
    t     = np.linspace(0.3 * np.pi, 4.3 * np.pi, 60)
    drift = np.linspace(0, -6, 60)
    prices = list(100 + drift + 2.0 * np.sin(t))
    return make_df(prices, wick_pct=0.001)


# ─────────────────────────────────────────────────────────────────────────────
# PRE-RUN DIAGNOSTIC
# ─────────────────────────────────────────────────────────────────────────────
print("Pre-run diagnostic:")
for lbl, ddf in [
    ('bullish_div',   build_bullish_divergence()),
    ('rsi_oversold',  build_rsi_oversold_macd_bullish()),
    ('high_vol',      build_high_volatility()),
    ('macd_weak',     build_macd_weak_bullish()),
]:
    r_pre = multi_modal_fusion_signal(ddf)
    m_pre = r_pre.measurements
    print(f"  {lbl:16s}: dir={r_pre.direction:4s}  conf={r_pre.confidence:.2f}  RSI={m_pre.get('rsi',0):.0f}  ATR%={m_pre.get('atr_pct',0):.4f}")
print()


# ─────────────────────────────────────────────────────────────────────────────
# TESTS
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("BRAIN 4: Multi-Modal-Fusion -- All 4 Core Scenarios")
print("=" * 65)
print()

# ── Test 1: Bullish divergence -> BUY 0.85 ───────────────────────────────────
print("Test 1: Bullish divergence -> BUY, confidence=0.85")
df1 = build_bullish_divergence()
r1  = multi_modal_fusion_signal(df1)
m1  = r1.measurements
check('Bullish divergence fires BUY',
      r1.direction == 'BUY' and m1.get('bullish_div', 0) == 1.0,
      f"dir={r1.direction}  conf={r1.confidence:.2f}  bullish_div={m1.get('bullish_div')}  RSI={m1.get('rsi', 0):.1f}",
      'BUY conf=0.85, bullish_div=1.0',
      r1.primary_evidence)

check('R:R: T1=2.0x SL=1.0x (2:1 mean-reversion)',
      r1.rr_t1_mult == 2.0 and r1.rr_sl_mult == 1.0,
      f"rr_t1={r1.rr_t1_mult}  rr_sl={r1.rr_sl_mult}  ratio={r1.rr_t1_mult/r1.rr_sl_mult:.1f}:1",
      'rr_t1=2.0, rr_sl=1.0 (wider SL for mean-reversion)')

# ── Test 2: RSI oversold + MACD bullish -> BUY ~0.72 ────────────────────────
print("Test 2: RSI oversold + MACD bullish -> BUY, confidence~0.72")
df2 = build_rsi_oversold_macd_bullish()
r2  = multi_modal_fusion_signal(df2)
m2  = r2.measurements
check('RSI oversold + MACD -> BUY',
      r2.direction == 'BUY' and abs(r2.confidence - 0.72) < 0.05,
      f"dir={r2.direction}  conf={r2.confidence:.2f}  RSI={m2.get('rsi', 0):.0f}",
      'BUY conf~0.72 (RSI<30 + MACD bullish)',
      r2.primary_evidence)

# ── Test 3: High volatility (equity) -> HOLD via vol guard ───────────────────
print("Test 3: High volatility -> HOLD via volatility guard")
df3 = build_high_volatility()
r3  = multi_modal_fusion_signal(df3, limit_volatility=True, symbol='RELIANCE')
m3  = r3.measurements
check('High vol -> HOLD (vol guard fires)',
      r3.direction == 'HOLD' and r3.reliability_flags.get('high_volatility_blocked'),
      f"dir={r3.direction}  ATR%={m3.get('atr_pct', 0):.4f}  conf={r3.confidence:.2f}",
      'HOLD with high_volatility_blocked=True (ATR%>limit)',
      r3.primary_evidence)

# ── Test 4: MACD weak tier removed (BF-7) -> HOLD ───────────────────────────
# BF-7 backtest showed MACD-only (no divergence, no extreme RSI) is pure noise.
# WR was <40% and it triggered on every minor oscillation. Tier removed entirely.
print("Test 4: MACD weak tier removed (BF-7) -> HOLD (no divergence, no extreme RSI)")
df4 = build_macd_weak_bullish()
r4  = multi_modal_fusion_signal(df4)
m4  = r4.measurements
check('MACD weak tier removed -> HOLD',
      r4.direction == 'HOLD',
      f"dir={r4.direction}  conf={r4.confidence:.2f}  RSI={m4.get('rsi', 0):.0f}  hist_slope={m4.get('hist_slope', 0):.4f}",
      'HOLD (BF-7: MACD standalone tier removed as noise)',
      r4.primary_evidence)

# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print(f"Multi-Modal-Fusion: {PASS} PASSED / {FAIL} FAILED")
print("=" * 65)
if FAIL == 0:
    print("ALL TESTS PASSED")
else:
    print("FAILURES DETECTED -- see above")
    sys.exit(1)
